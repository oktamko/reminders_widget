"""Once-a-day update notice from GitHub Releases (fake fetch and clock)."""
import httpx
import pytest

from reminders_widget.app.updates import UpdateChecker, default_fetch, parse_version
from reminders_widget.project import REPO, VERSION, releases_url
from reminders_widget.store import Store

DAY = 24 * 3600


def release(tag="v9.0.0", url=None, **kw):
    return {"tag_name": tag, "html_url": url or f"{releases_url()}/tag/{tag}", "draft": False,
            "prerelease": False, **kw}


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def checker(fetch, found=None, clock=None, current="0.1.0"):
    calls = []

    def counted(url):
        calls.append(url)
        return fetch(url)
    c = UpdateChecker(Store(":memory:"), counted, current=current, now=clock or Clock(),
                      on_found=(found.append if found is not None else lambda u: None),
                      configured=lambda: True)
    return c, calls


def test_parse_version():
    assert parse_version("v1.2.3") == (1, 2, 3) and parse_version("0.2.0") == (0, 2, 0)
    assert parse_version("1.2") is None and parse_version("v1.2.3-beta") is None and parse_version(None) is None


def test_newer_release_is_reported_with_its_page():
    found = []
    c, calls = checker(lambda url: release("v0.2.0"), found)
    assert c.check_if_due() == {"version": "0.2.0", "url": f"{releases_url()}/tag/v0.2.0"}
    assert found == [{"version": "0.2.0", "url": f"{releases_url()}/tag/v0.2.0"}]
    assert calls == [f"https://api.github.com/repos/{REPO}/releases/latest"]


@pytest.mark.parametrize("tag", ["v0.1.0", "v0.0.9"])
def test_same_or_older_release_reports_nothing(tag):
    c, _ = checker(lambda url: release(tag))
    assert c.check_if_due() is None


def test_checks_at_most_once_a_day():
    clock = Clock()
    c, calls = checker(lambda url: release("v0.2.0"), clock=clock)
    c.check_if_due()
    clock.t += 3600
    assert c.check_if_due() == {"version": "0.2.0", "url": f"{releases_url()}/tag/v0.2.0"}   # remembered
    assert len(calls) == 1
    clock.t += DAY
    c.check_if_due()
    assert len(calls) == 2


def _raise(url):
    raise httpx.ConnectError("offline")


@pytest.mark.parametrize("fetch", [_raise, lambda url: {"message": "API rate limit exceeded"}, lambda url: []])
def test_failures_are_silent_and_retried_next_day(fetch):   # plan 3 Review Focus 2
    clock = Clock()
    c, calls = checker(fetch, clock=clock)
    assert c.check_if_due() is None
    assert c.check_if_due() is None and len(calls) == 1      # not hammered
    clock.t += DAY + 1
    c.check_if_due()
    assert len(calls) == 2


@pytest.mark.parametrize("url", ["https://evil.example/releases/tag/v0.2.0",
                                  "https://github.com/someone/else/releases/tag/v0.2.0"])
def test_url_outside_the_repo_is_ignored(url):
    c, _ = checker(lambda u: release("v0.2.0", url=url))
    assert c.check_if_due() is None


def test_drafts_and_prereleases_are_ignored():
    c, _ = checker(lambda u: release("v0.2.0", prerelease=True))
    assert c.check_if_due() is None


def test_an_installed_upgrade_clears_the_notice():
    clock = Clock()
    store = Store(":memory:")
    UpdateChecker(store, lambda u: release("v0.2.0"), current="0.1.0", now=clock,
                  configured=lambda: True).check_if_due()
    assert UpdateChecker(store, lambda u: release("v0.2.0"), current="0.2.0", now=clock).latest() is None


def test_request_sends_no_cookies_and_a_plain_user_agent():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=release("v0.2.0"))
    data = default_fetch("https://api.github.com/repos/x/y/releases/latest", transport=httpx.MockTransport(handler))
    assert data["tag_name"] == "v0.2.0"
    (req,) = seen
    assert req.headers["User-Agent"] == f"RemindersWidget/{VERSION}"
    assert "cookie" not in {k.lower() for k in req.headers}


def test_http_error_raises_for_the_checker_to_swallow():
    with pytest.raises(httpx.HTTPStatusError):
        default_fetch("https://api.github.com/x", transport=httpx.MockTransport(lambda r: httpx.Response(403)))


def test_background_checks_start_late_repeat_and_stop():
    import threading
    import time as _t
    from reminders_widget.app.updates import start_update_checks
    calls = []

    class C:
        def check_if_due(self):
            calls.append(_t.monotonic())
    stop = threading.Event()
    thread = start_update_checks(C(), stop, first_delay=0.05, every=0.05)
    _t.sleep(0.03)
    assert calls == []                      # not right at start
    _t.sleep(0.2)
    assert len(calls) >= 2
    stop.set()
    thread.join(1)
    assert not thread.is_alive()


def test_release_url_with_other_capitalisation_is_accepted():   # final review 1
    url = f"{releases_url()}/tag/v0.2.0".replace(REPO, REPO.upper())
    c, _ = checker(lambda u: release("v0.2.0", url=url))
    assert c.check_if_due() == {"version": "0.2.0", "url": url}


def test_placeholder_repository_is_never_asked():   # final review 2
    calls = []
    c = UpdateChecker(Store(":memory:"), lambda u: calls.append(u) or release("v0.2.0"),
                      configured=lambda: False)
    assert c.check_if_due() is None and calls == []


def test_repo_configured_is_false_for_the_placeholder(monkeypatch):
    from reminders_widget import project
    monkeypatch.setattr(project, "REPO", "OWNER/apple-reminders-widget")
    assert project.repo_configured() is False
    monkeypatch.setattr(project, "REPO", "MartinCimbala/apple-reminders-widget")
    assert project.repo_configured() is True
