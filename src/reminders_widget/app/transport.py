"""CloudKit transport over httpx with the saved iCloud session cookies.

Logs only statuses and event names; cookie values never leave the jar.
"""
import json
import logging
import threading
from http.cookiejar import Cookie as JarCookie
from typing import Callable

import httpx

from reminders_widget.app.secrets import Cookie
from reminders_widget.icloud.errors import OfflineError, SessionExpired, Transient

VALIDATE_URL = "https://setup.icloud.com/setup/ws/1/validate"
HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://www.icloud.com",
    "Referer": "https://www.icloud.com/",
}
log = logging.getLogger("reminders_widget.session")


def _snapshot(jar: httpx.Cookies) -> list[Cookie]:
    return sorted(
        (Cookie(c.name, c.value or "", c.domain, c.path or "/", bool(c.secure),
                float(c.expires) if c.expires else None) for c in jar.jar),
        key=lambda c: (c.domain, c.path, c.name))


class HttpxTransport:
    """Implements reminders_widget.icloud.cloudkit.Transport."""

    def __init__(self, cookies: list[Cookie], on_cookies: Callable[[list[Cookie]], None],
                 client: httpx.Client | None = None, timeout: float = 30.0):
        self._client = client or httpx.Client()
        self._client.timeout = httpx.Timeout(timeout)
        self._on_cookies = on_cookies
        self._lock = threading.Lock()   # a request and a jar replacement never interleave
        self.set_cookies(cookies)

    def set_cookies(self, cookies: list[Cookie]) -> None:
        with self._lock:
            jar = self._client.cookies.jar
            jar.clear()
            for c in cookies:
                jar.set_cookie(JarCookie(
                    version=0, name=c.name, value=c.value, port=None, port_specified=False,
                    domain=c.domain, domain_specified=True, domain_initial_dot=c.domain.startswith("."),
                    path=c.path or "/", path_specified=True, secure=bool(c.secure),
                    expires=int(c.expires) if c.expires else None, discard=c.expires is None,
                    comment=None, comment_url=None, rest={}))
            self._last = _snapshot(self._client.cookies)

    def cookies(self) -> list[Cookie]:
        with self._lock:
            return _snapshot(self._client.cookies)

    def _send(self, url: str, content: bytes) -> httpx.Response:
        with self._lock:
            self._client.cookies.jar.clear_expired_cookies()
            try:
                resp = self._client.post(url, headers=HEADERS, content=content)
            except httpx.TransportError as exc:
                raise OfflineError(type(exc).__name__) from None
            now = _snapshot(self._client.cookies)
            changed = now != self._last
            if changed:
                self._last = now
        if changed:
            self._on_cookies(now)
        return resp

    def post(self, url: str, body: dict) -> tuple[int, dict]:
        resp = self._send(url, json.dumps(body).encode("utf-8"))
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return resp.status_code, data if isinstance(data, dict) else {}

    def validate(self) -> str:
        """The CloudKit database base URL for this session; SessionExpired if refused."""
        resp = self._send(VALIDATE_URL, b"null")
        if resp.status_code in (401, 403, 421):
            raise SessionExpired(f"HTTP {resp.status_code}")
        if resp.status_code == 429 or resp.status_code >= 500:
            raise Transient(f"HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            raise Transient("validate answered without JSON") from None   # e.g. a captive portal
        try:
            url = data["webservices"]["ckdatabasews"]["url"]
        except (KeyError, TypeError):
            url = None
        if not isinstance(url, str) or not url:
            raise SessionExpired("no ckdatabasews url")
        return url


class Session:
    def __init__(self, transport: HttpxTransport):
        self.transport = transport
        self._base: str | None = None

    def base_url(self) -> str:
        if self._base is None:
            self._base = self.transport.validate()
        return self._base

    def renew(self) -> bool:
        """One silent re-validation after iCloud refused the session."""
        self._base = None
        try:
            self._base = self.transport.validate()
        except SessionExpired:
            log.info("manual sign-in needed")
            return False
        log.info("session renewed silently")
        return True
