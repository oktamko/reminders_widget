"""Tell the user when GitHub has a newer release. Notify only: nothing is downloaded or run.

At most one request a day to GitHub's "latest release" endpoint, without cookies and with a
plain User-Agent. Failures (offline, rate limits, odd answers) are silent and retried the
next day. Only the version and the release page URL are kept.
"""
import logging
import re
import time
from typing import Callable

import httpx

from reminders_widget import project
from reminders_widget.project import VERSION, is_release_url

log = logging.getLogger("reminders_widget.updates")
INTERVAL_S = 24 * 3600
_TAG = re.compile(r"v?(\d+)\.(\d+)\.(\d+)\Z")


def parse_version(tag) -> tuple[int, int, int] | None:
    m = _TAG.match(tag) if isinstance(tag, str) else None
    return tuple(int(p) for p in m.groups()) if m else None


def latest_url() -> str:
    return f"https://api.github.com/repos/{project.REPO}/releases/latest"


def default_fetch(url: str, *, transport: httpx.BaseTransport | None = None):
    headers = {"User-Agent": f"RemindersWidget/{VERSION}", "Accept": "application/vnd.github+json"}
    with httpx.Client(timeout=10.0, headers=headers, transport=transport, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


class UpdateChecker:
    def __init__(self, store, fetch: Callable[[str], object] = default_fetch, *, current: str = VERSION,
                 now: Callable[[], float] = time.time, on_found: Callable[[dict | None], None] = lambda u: None,
                 configured: Callable[[], bool] = lambda: project.repo_configured()):
        self._store, self._fetch, self._now, self._on_found = store, fetch, now, on_found
        self._configured = configured
        self._current = parse_version(current) or (0, 0, 0)

    def latest(self) -> dict | None:
        """The remembered newer release, if it is still newer than this copy."""
        saved = self._store.get_setting("update_latest", None)
        if isinstance(saved, dict) and (parse_version(saved.get("version")) or (0, 0, 0)) > self._current:
            return saved
        return None

    def check_if_due(self) -> dict | None:
        if not self._configured():                    # placeholder repository: never ask anyone
            return None
        last = self._store.get_setting("update_checked_at", None)
        if isinstance(last, (int, float)) and 0 <= self._now() - last < INTERVAL_S:
            return self.latest()
        self._store.set_setting("update_checked_at", self._now())
        try:
            found = self._read(self._fetch(latest_url()))
        except Exception as exc:  # offline, rate-limited, unexpected body: try again tomorrow
            log.info("update check failed: %s", type(exc).__name__)
            return self.latest()
        self._store.set_setting("update_latest", found)
        result = self.latest()
        self._on_found(result)
        return result

    def _read(self, data) -> dict | None:
        if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
            return None
        version, url = parse_version(data.get("tag_name")), data.get("html_url")
        if version is None or not is_release_url(url):
            return None
        return {"version": ".".join(map(str, version)), "url": url}


def start_update_checks(checker, stop, *, first_delay: float = 30.0, every: float = 6 * 3600):
    """A daemon thread: the first check shortly after start (sync goes first), then every `every` s.

    `check_if_due` itself keeps the real pace at one request a day.
    """
    import threading

    def loop():
        delay = first_delay
        while not stop.wait(delay):
            try:
                checker.check_if_due()
            except Exception as exc:  # never end the thread
                log.info("update check failed: %s", type(exc).__name__)
            delay = every
    thread = threading.Thread(target=loop, name="rw-updates", daemon=True)
    thread.start()
    return thread
