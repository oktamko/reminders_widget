"""iCloud sign-in in a WebView2 window; returns the session cookies (never logged)."""
import logging
import threading
import time
from email.utils import parsedate_to_datetime

from reminders_widget.app.secrets import Cookie

log = logging.getLogger("reminders_widget.signin")
SIGNIN_URL = "https://www.icloud.com/"
LOGIN_TIMEOUT_S = 600
TRUST_WAIT_S = 60
POLL_S = 2.0


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
            except (TypeError, ValueError):
                expires = None
        out.append(Cookie(m.key, m.value, m["domain"] or ".icloud.com", m["path"] or "/",
                          bool(m["secure"]), expires))
    return out


def _names(cookies) -> set[str]:
    return {c.name for c in cookies}


def sign_in(webview_dir=None, *, webview_module=None) -> list[Cookie] | None:
    """Open the sign-in window and wait for the session; None if the user closes it.

    Must run on a worker thread while the pywebview GUI loop is running.
    """
    webview = webview_module
    if webview is None:
        import webview  # noqa: PLC0415 - GUI import only when signing in
    window = webview.create_window("Sign in to iCloud", SIGNIN_URL, width=520, height=720)
    closed = threading.Event()
    window.events.closed += closed.set
    cookies: list[Cookie] = []
    try:
        deadline = time.time() + LOGIN_TIMEOUT_S
        while time.time() < deadline and not closed.is_set():
            cookies = morsels_to_cookies(window.get_cookies())
            if "X-APPLE-WEBAUTH-TOKEN" in _names(cookies):
                break
            time.sleep(POLL_S)
        else:
            log.info("sign-in window closed or timed out")
            return None
        trust_deadline = time.time() + TRUST_WAIT_S
        while (not any("TRUST" in n.upper() for n in _names(cookies))
               and time.time() < trust_deadline and not closed.is_set()):
            time.sleep(POLL_S)
            cookies = morsels_to_cookies(window.get_cookies())
        log.info("signed in, %d cookies, trust cookie %s", len(cookies),
                 "present" if any("TRUST" in n.upper() for n in _names(cookies)) else "absent")
        return cookies
    except Exception as exc:  # window gone mid-poll
        log.info("sign-in ended: %s", type(exc).__name__)
        return cookies if "X-APPLE-WEBAUTH-TOKEN" in _names(cookies) else None
    finally:
        try:
            window.destroy()
        except Exception:
            pass
