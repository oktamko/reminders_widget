import logging

import httpx
import pytest

from reminders_widget.app.secrets import Cookie
from reminders_widget.app.transport import VALIDATE_URL, HttpxTransport, Session
from reminders_widget.icloud.errors import OfflineError, SessionExpired

SECRET = "tok-SECRET-value-123"
CK = "https://p99-ckdatabasews.icloud.com:443"


def jar():
    return [Cookie("X-APPLE-WEBAUTH-TOKEN", SECRET, ".icloud.com", "/", True, None)]


def make(handler, saved=None):
    saved = saved if saved is not None else []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpxTransport(jar(), saved.append, client=client), saved


def test_post_sends_headers_cookie_and_json():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), origin=request.headers.get("origin"),
                    referer=request.headers.get("referer"), ctype=request.headers.get("content-type"),
                    cookie=request.headers.get("cookie", ""), body=request.content)
        return httpx.Response(200, json={"zones": []})

    t, _ = make(handler)
    status, data = t.post(CK + "/database/1/x/zones/list", {})
    assert (status, data) == (200, {"zones": []})
    assert seen["origin"] == "https://www.icloud.com" and seen["referer"] == "https://www.icloud.com/"
    assert seen["ctype"] == "application/json" and seen["body"] == b"{}"
    assert f"X-APPLE-WEBAUTH-TOKEN={SECRET}" in seen["cookie"]


def test_cookies_not_sent_outside_icloud():
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200, json={})

    t, _ = make(handler)
    t.post("https://example.com/x", {})
    assert SECRET not in seen["cookie"]


def test_set_cookie_updates_jar_and_persists():
    def handler(request):
        return httpx.Response(200, json={}, headers={
            "set-cookie": "X-APPLE-WEBAUTH-TOKEN=new-value; Domain=.icloud.com; Path=/; Secure"})

    t, saved = make(handler)
    t.post(CK + "/database/1/x/zones/list", {})
    assert saved and any(c.name == "X-APPLE-WEBAUTH-TOKEN" and c.value == "new-value" for c in saved[-1])
    assert any(c.value == "new-value" for c in t.cookies())


def test_unchanged_cookies_do_not_persist():
    t, saved = make(lambda r: httpx.Response(200, json={}))
    t.post(CK + "/x", {})
    assert saved == []


def test_connect_error_is_offline():
    def handler(request):
        raise httpx.ConnectError("no route")

    t, _ = make(handler)
    with pytest.raises(OfflineError):
        t.post(CK + "/x", {})


def test_non_json_body_becomes_empty_dict():
    t, _ = make(lambda r: httpx.Response(502, text="<html>bad gateway</html>"))
    assert t.post(CK + "/x", {}) == (502, {})


def _validate_handler(responses):
    calls = {"n": 0}

    def handler(request):
        if str(request.url) == VALIDATE_URL:
            assert request.content == b"null"
            status, body = responses[min(calls["n"], len(responses) - 1)]
            calls["n"] += 1
            return httpx.Response(status, json=body)
        return httpx.Response(200, json={})
    return handler, calls


def test_validate_returns_ckdatabasews_url():
    handler, _ = _validate_handler([(200, {"webservices": {"ckdatabasews": {"url": CK}}})])
    t, _ = make(handler)
    assert t.validate() == CK


def test_session_base_url_expired_on_421():
    handler, _ = _validate_handler([(421, {})])
    t, _ = make(handler)
    with pytest.raises(SessionExpired):
        Session(t).base_url()


def test_session_base_url_expired_without_url():
    handler, _ = _validate_handler([(200, {"webservices": {}})])
    t, _ = make(handler)
    with pytest.raises(SessionExpired):
        Session(t).base_url()


def test_renew_true_when_second_validate_succeeds(caplog):
    handler, calls = _validate_handler([(421, {}), (200, {"webservices": {"ckdatabasews": {"url": CK}}})])
    t, _ = make(handler)
    s = Session(t)
    with pytest.raises(SessionExpired):
        s.base_url()
    with caplog.at_level(logging.INFO, logger="reminders_widget"):
        assert s.renew() is True
    assert s.base_url() == CK and calls["n"] == 2
    assert "session renewed silently" in caplog.text


def test_renew_false_logs_manual_sign_in(caplog):
    handler, _ = _validate_handler([(421, {})])
    t, _ = make(handler)
    with caplog.at_level(logging.INFO, logger="reminders_widget"):
        assert Session(t).renew() is False
    assert "manual sign-in needed" in caplog.text


def test_no_cookie_value_in_logs(caplog):
    def handler(request):
        return httpx.Response(200, json={}, headers={
            "set-cookie": f"X-APPLE-WEBAUTH-TOKEN={SECRET}2; Domain=.icloud.com; Path=/"})

    t, _ = make(handler)
    with caplog.at_level(logging.DEBUG):
        t.post(CK + "/x", {})
        Session(t).renew()
    assert SECRET not in caplog.text
