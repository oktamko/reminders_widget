"""iCloud sign-in in a WebView2 window; returns the session cookies (values never logged).

The session is complete when iCloud accepts the cookies (`validate`), not when one
particular cookie name appears: after a restart WebView2 forgets session-only
cookies and icloud.com re-creates its session with other cookies, some of them on
setup.icloud.com, which pywebview's get_cookies() (current page URL only) cannot see.
"""
import logging
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Callable

from reminders_widget.app.secrets import Cookie

log = logging.getLogger("reminders_widget.signin")
SIGNIN_URL = "https://www.icloud.com/"
COOKIE_URLS = ("https://www.icloud.com/", "https://setup.icloud.com/")
AUTH_COOKIE = "X-APPLE-WEBAUTH-TOKEN"
LOGIN_TIMEOUT_S = 600
TRUST_WAIT_S = 60
POLL_S = 2.0
VALIDATE_EVERY = 3          # try validate every N polls once any cookie exists


def _morsels(cookies):
    for jar in cookies or []:
        if hasattr(jar, "values"):
            yield from jar.values()


def morsels_to_cookies(cookies) -> list[Cookie]:
    """pywebview get_cookies() result (SimpleCookie objects) → Cookie list."""
    out = []
    for m in _morsels(cookies):
        expires = None
        if m["expires"]:
            try:
                expires = parsedate_to_datetime(m["expires"]).timestamp()
            except (TypeError, ValueError, OverflowError):
                expires = None
            year = next((t for t in str(m["expires"]).split() if t.isdigit() and len(t) == 4), None)
            if expires is not None and (expires < 946684800 or (year and int(year) < 2000)):
                expires = None   # 1970 / 0001 (parsed as 2001) mean "session cookie"
        out.append(Cookie(m.key, m.value, m["domain"] or ".icloud.com", m["path"] or "/",
                          bool(m["secure"]), expires))
    return out


def _names(cookies) -> set[str]:
    return {c.name for c in cookies}


def _cookies_for(window, url: str) -> list:
    """WebView2 cookies for `url` (pywebview 6.2 only reads the page URL's cookies)."""
    form = getattr(window, "native", None)
    browser = getattr(form, "browser", None)
    if form is None or browser is None or not hasattr(browser, "get_cookies"):
        return []
    from System import Func, Type  # pythonnet, present whenever WebView2 is

    out: list = []
    done = threading.Semaphore(0)

    def _get():
        saved = browser.url
        browser.url = url
        try:
            browser.get_cookies(out, done)
        finally:
            browser.url = saved

    form.Invoke(Func[Type](_get))
    done.acquire(timeout=10)
    return out


def collect(window) -> list[Cookie]:
    """The page's cookies plus those of the iCloud hosts in COOKIE_URLS, de-duplicated."""
    raw = list(window.get_cookies() or [])
    for url in COOKIE_URLS:
        try:
            raw += _cookies_for(window, url)
        except Exception as exc:  # internal API missing or changed: page cookies only
            log.info("extra cookie read failed: %s", type(exc).__name__)
            break
    seen, out = set(), []
    for c in morsels_to_cookies(raw):
        key = (c.name, c.domain, c.path)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _describe(cookies: list[Cookie]) -> str:
    return ", ".join(sorted(f"{c.name}@{c.domain}" for c in cookies)) or "none"


def sign_in(webview_dir=None, *, webview_module=None,
            validate: Callable[[list[Cookie]], bool] | None = None) -> list[Cookie] | None:
    """Open the sign-in window and wait until the session works; None if the user closes it.

    Must run on a worker thread while the pywebview GUI loop is running.
    """
    webview = webview_module
    if webview is None:
        import webview  # noqa: PLC0415 - GUI import only when signing in
    window = webview.create_window("Sign in to iCloud", SIGNIN_URL, width=520, height=720,
                                   on_top=True, focus=True)
    log.info("sign-in window opened")
    closed = threading.Event()
    window.events.closed += closed.set
    cookies: list[Cookie] = []
    described = False
    try:
        deadline, polls, accepted = time.time() + LOGIN_TIMEOUT_S, 0, False
        while time.time() < deadline and not closed.is_set():
            fresh = collect(window)
            if fresh:
                cookies = fresh
                if not described:
                    log.info("cookies seen: %s", _describe(cookies))
                    described = True
            if AUTH_COOKIE in _names(cookies):
                break
            if validate and cookies and polls % VALIDATE_EVERY == 0 and validate(cookies):
                accepted = True
                break
            polls += 1
            time.sleep(POLL_S)
        else:
            log.info("sign-in window closed or timed out")
            return None
        trust_deadline = time.time() + TRUST_WAIT_S
        while (not accepted and not any("TRUST" in n.upper() for n in _names(cookies))
               and time.time() < trust_deadline and not closed.is_set()):
            if validate and validate(cookies):
                break                    # iCloud already accepts them; no need to wait
            time.sleep(POLL_S)
            fresh = collect(window)
            if AUTH_COOKIE in _names(fresh):
                cookies = fresh          # keep the last complete snapshot
        log.info("signed in, %d cookies: %s", len(cookies), _describe(cookies))
        return cookies
    except Exception as exc:  # window gone mid-poll
        log.info("sign-in ended: %s", type(exc).__name__)
        return cookies if AUTH_COOKIE in _names(cookies) else None
    finally:
        try:
            window.destroy()
        except Exception:
            pass
