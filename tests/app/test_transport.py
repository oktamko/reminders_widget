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


def test_expired_cookie_is_not_sent():   # final review 3
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    t = HttpxTransport([Cookie("OLD", "gone", ".icloud.com", "/", True, 1.0),
                        Cookie("NEW", "here", ".icloud.com", "/", True, None)], lambda c: None, client=client)
    t.post(CK + "/x", {})
    assert "NEW=here" in seen["cookie"] and "OLD=" not in seen["cookie"]


def test_secure_cookie_not_sent_over_http():   # final review 3
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200, json={})

    t, _ = make(handler)
    t.post("http://p1-ckdatabasews.icloud.com/x", {})
    assert SECRET not in seen["cookie"]


def test_saved_jar_keeps_secure_and_expiry():   # final review 3
    def handler(request):
        return httpx.Response(200, json={}, headers={
            "set-cookie": "B=2; Domain=.icloud.com; Path=/; Secure; Expires=Wed, 01 Dec 2027 00:00:00 GMT"})

    t, saved = make(handler)
    t.post(CK + "/x", {})
    by_name = {c.name: c for c in saved[-1]}
    assert by_name["B"].secure is True and by_name["B"].expires and by_name["B"].expires > 1.8e9
    assert by_name["X-APPLE-WEBAUTH-TOKEN"].secure is True


@pytest.mark.parametrize("status,body", [(503, {}), (429, {}), (200, "<html>portal</html>")])
def test_validate_outage_is_transient_not_expired(status, body):   # final review 6
    from reminders_widget.icloud.errors import Transient

    def handler(request):
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    t, _ = make(handler)
    with pytest.raises(Transient):
        t.validate()


def test_set_cookies_waits_for_in_flight_request():   # final review 5
    import threading
    gate, entered = threading.Event(), threading.Event()

    def handler(request):
        entered.set()
        gate.wait(2)
        return httpx.Response(421, json={}, headers={
            "set-cookie": "X-APPLE-WEBAUTH-TOKEN=; Domain=.icloud.com; Path=/; Max-Age=0"})

    t, saved = make(handler)
    worker = threading.Thread(target=lambda: t.post(CK + "/x", {}))
    worker.start()
    entered.wait(2)
    setter = threading.Thread(target=lambda: t.set_cookies(
        [Cookie("X-APPLE-WEBAUTH-TOKEN", "fresh", ".icloud.com", "/", True, None)]))
    setter.start()
    gate.set()
    worker.join(2)
    setter.join(2)
    assert [c.value for c in t.cookies() if c.name == "X-APPLE-WEBAUTH-TOKEN"] == ["fresh"]
