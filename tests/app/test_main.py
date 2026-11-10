"""App wiring without a GUI: fake sign-in, fake transport over FakeCloudKit."""
import json
from pathlib import Path

from reminders_widget.app.main import App
from reminders_widget.app.secrets import Cookie, load_cookies
from reminders_widget.app.signin import morsels_to_cookies
from reminders_widget.icloud.errors import OfflineError, SessionExpired
from tests.fakes.fake_cloudkit import FakeCloudKit

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "records.json"
COOKIES = [Cookie("X-APPLE-WEBAUTH-TOKEN", "v1", ".icloud.com", "/", True, None)]


class FakeTransport:
    """HttpxTransport's interface over FakeCloudKit; validate behaviour is scripted."""

    def __init__(self, cookies, on_cookies, validate=("ok",)):
        self.fake = FakeCloudKit(json.loads(FIXTURE.read_text(encoding="utf-8")))
        self.jar, self.on_cookies, self.script = list(cookies), on_cookies, list(validate)

    def post(self, url, body):
        return self.fake.post(url, body)

    def validate(self):
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if step == "offline":
            raise OfflineError("down")
        if step == "expired" or not self.jar:
            raise SessionExpired("421")
        return "https://fake"

    def set_cookies(self, cookies):
        self.jar = list(cookies)

    def cookies(self):
        return self.jar


def make_app(tmp_path, validate=("ok",), sign_in_result=COOKIES, with_cookies=True):
    calls = {"sign_in": 0}

    def sign_in(_webview_dir):
        calls["sign_in"] += 1
        return sign_in_result

    holder = {}

    def factory(cookies, on_cookies):
        holder["t"] = FakeTransport(cookies, on_cookies, validate)
        return holder["t"]

    if with_cookies:
        from reminders_widget.app.paths import data_dir, session_path
        from reminders_widget.app.secrets import save_cookies
        save_cookies(session_path(data_dir(tmp_path)), COOKIES)
    app = App(tmp_path, sign_in=sign_in, transport_factory=factory, sign_in_async=False)
    return app, calls, holder


def test_first_start_without_cookies_signs_in_once_and_saves(tmp_path):
    app, calls, _ = make_app(tmp_path, with_cookies=False)
    app.start(gui=False)
    try:
        assert calls["sign_in"] == 1
        assert load_cookies(app.session_file) == COOKIES
        assert app.run_sync().status == "ok"
        assert app.api.state()["reminders"]
    finally:
        app.shutdown()


def test_offline_at_start_reports_offline_without_sign_in(tmp_path):   # Review Focus 1
    app, calls, _ = make_app(tmp_path, validate=("offline",))
    app.start(gui=False)
    try:
        assert app.run_sync().status == "offline"
        assert calls["sign_in"] == 0
        app.api.on_sync(app.run_sync())
        assert app.api.state()["status"] == "offline"
    finally:
        app.shutdown()


def test_offline_then_online_recovers(tmp_path):
    app, _, _ = make_app(tmp_path, validate=("offline", "ok"))
    app.start(gui=False)
    try:
        assert app.run_sync().status == "offline"
        assert app.run_sync().status == "ok"
    finally:
        app.shutdown()


def test_session_expired_renews_silently(tmp_path):   # Review Focus 3
    app, calls, _ = make_app(tmp_path, validate=("expired", "ok"))
    app.start(gui=False)
    try:
        assert app.run_sync().status == "ok"
        assert calls["sign_in"] == 0
    finally:
        app.shutdown()


def test_session_expired_mid_use_keeps_local_edits(tmp_path):   # Review Focus 3
    app, calls, holder = make_app(tmp_path, validate=("ok",))
    app.start(gui=False)
    try:
        assert app.run_sync().status == "ok" and app.store.lists()
        holder["t"].fake.session_valid = False      # iCloud now refuses the session
        holder["t"].script = ["expired"]            # and a silent re-validation fails too
        assert app.run_sync().status == "session_expired"
        assert app.api.add("Offline edit")["ok"] and app.store.pending()
        assert calls["sign_in"] == 0                # the banner asks; nothing opens by itself
    finally:
        app.shutdown()


def test_sign_in_from_banner_rebuilds_session(tmp_path):
    app, calls, holder = make_app(tmp_path, validate=("expired", "expired", "ok"))
    app.start(gui=False)
    try:
        assert app.run_sync().status == "session_expired"
        assert app.api.sign_in()["ok"]
        assert calls["sign_in"] == 1 and holder["t"].jar == COOKIES
        assert app.run_sync().status == "ok"
    finally:
        app.shutdown()


def test_shutdown_stops_scheduler(tmp_path):   # Review Focus 5
    app, _, _ = make_app(tmp_path)
    app.start(gui=False, scheduler=True)
    app.shutdown()
    assert not app.scheduler_running()


def test_morsels_to_cookies():
    from http.cookies import SimpleCookie
    jar = SimpleCookie()
    jar["X-APPLE-WEBAUTH-TOKEN"] = "abc"
    jar["X-APPLE-WEBAUTH-TOKEN"]["domain"] = ".icloud.com"
    jar["X-APPLE-WEBAUTH-TOKEN"]["path"] = "/"
    jar["X-APPLE-WEBAUTH-TOKEN"]["secure"] = True
    jar["X-APPLE-WEBAUTH-TOKEN"]["expires"] = "Wed, 01 Dec 2027 00:00:00 GMT"
    out = morsels_to_cookies([jar])
    assert out[0].name == "X-APPLE-WEBAUTH-TOKEN" and out[0].value == "abc"
    assert out[0].domain == ".icloud.com" and out[0].secure is True and out[0].expires > 1.8e9
