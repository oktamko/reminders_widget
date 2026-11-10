"""Wires the app: data folder → logging → store → session → sync → scheduler → window."""
import json
import logging
import threading
from pathlib import Path
from typing import Callable

from reminders_widget.app import signin
from reminders_widget.app.api import Api
from reminders_widget.app.logsetup import setup_logging
from reminders_widget.app.paths import data_dir, db_path, logs_dir, session_path, webview_dir
from reminders_widget.app.scheduler import Scheduler
from reminders_widget.app.secrets import Cookie, load_cookies, save_cookies
from reminders_widget.app.transport import HttpxTransport, Session
from reminders_widget.icloud.cloudkit import CloudKit
from reminders_widget.icloud.errors import OfflineError, SessionExpired
from reminders_widget.store import Store
from reminders_widget.sync import SyncEngine, SyncResult

log = logging.getLogger("reminders_widget.app")
UI_INDEX = Path(__file__).parent / "ui" / "index.html"


class App:
    def __init__(self, base: Path | None = None, *,
                 sign_in: Callable[[Path], list[Cookie] | None] = signin.sign_in,
                 transport_factory: Callable | None = None, sign_in_async: bool = True):
        self.dir = data_dir(base)
        setup_logging(logs_dir(self.dir))
        self.session_file = session_path(self.dir)
        self.store = Store(db_path(self.dir))
        self._sign_in, self._sign_in_async = sign_in, sign_in_async
        factory = transport_factory or (lambda cookies, on: HttpxTransport(cookies, on))
        self.transport = factory(load_cookies(self.session_file) or [], self._save_cookies)
        self.session = Session(self.transport)
        self.engine: SyncEngine | None = None
        self._engine_lock = threading.Lock()
        self._signing_in = threading.Lock()
        self.scheduler = Scheduler(self.run_sync, self._on_result)
        self.api = Api(self.store, self.scheduler)
        self.api._sign_in_hook = self.request_sign_in
        self._scheduler_started = False
        self._shut = False
        self._asked_sign_in = False   # one automatic sign-in per expiry episode
        self._save_lock = threading.Lock()

    # --- session ---------------------------------------------------------------
    def _save_cookies(self, cookies: list[Cookie]) -> None:
        """Persist the jar; a failure (file locked, disk full) never breaks a sync run."""
        with self._save_lock:
            try:
                save_cookies(self.session_file, cookies)
            except Exception as exc:
                log.warning("saving the session failed: %s", type(exc).__name__)

    def _engine(self) -> SyncEngine:
        with self._engine_lock:
            if self.engine is None:
                self.engine = SyncEngine(CloudKit(self.transport, self.session.base_url()), self.store)
            return self.engine

    def run_sync(self) -> SyncResult:
        if not self.transport.cookies():
            return SyncResult("session_expired")     # not signed in yet: no request at all
        try:
            result = self._engine().run_once()
        except OfflineError:
            return SyncResult("offline")
        except SessionExpired:
            result = SyncResult("session_expired")
        if result.status == "session_expired":
            try:
                renewed = self.session.renew()
            except OfflineError:
                return SyncResult("offline")
            if not renewed:
                return result
            with self._engine_lock:
                self.engine = None
            try:
                return self._engine().run_once()
            except OfflineError:
                return SyncResult("offline")
            except SessionExpired:
                return SyncResult("session_expired")
        return result

    def _on_result(self, result: SyncResult) -> None:
        log.info("sync %s, %d notices", result.status, len(result.notices))
        if result.status == "ok":
            self._asked_sign_in = False
        elif result.status == "session_expired" and not self._asked_sign_in:
            self._asked_sign_in = True
            self.request_sign_in()
        self.api._on_sync(result)

    def request_sign_in(self) -> None:
        """Open the sign-in window (on a worker thread when the GUI is running)."""
        if self._shut:
            return
        if self._sign_in_async:
            threading.Thread(target=self._do_sign_in, name="rw-signin", daemon=True).start()
        else:
            self._do_sign_in()

    def _do_sign_in(self) -> None:
        if not self._signing_in.acquire(blocking=False):
            return
        try:
            if self._shut:
                return
            cookies = self._sign_in(webview_dir(self.dir))
            if not cookies or self._shut:
                return
            self._save_cookies(cookies)
            self.transport.set_cookies(cookies)
            with self._engine_lock:
                self.engine = None
            self.session = Session(self.transport)
            log.info("manual sign-in completed")
            if self._scheduler_started:
                self.scheduler.start()
                self.scheduler.now()
        finally:
            self._signing_in.release()

    # --- lifecycle ---------------------------------------------------------------
    def start(self, *, gui: bool = True, scheduler: bool | None = None) -> None:
        """After the window exists: sign in if there is no session, then start syncing."""
        if not self.transport.cookies():
            self._asked_sign_in = True
            self.request_sign_in()
        if scheduler if scheduler is not None else gui:
            self._scheduler_started = True
            self.scheduler.start()

    def scheduler_running(self) -> bool:
        thread = getattr(self.scheduler, "_thread", None)
        return bool(thread and thread.is_alive())

    def shutdown(self) -> None:
        self._shut = True
        self.scheduler.stop()
        self.store.close()


def run(base: Path | None = None) -> None:
    import webview

    app = App(base)
    window = webview.create_window("Reminders", str(UI_INDEX), js_api=app.api,
                                   width=400, height=640, min_size=(320, 420),
                                   background_color="#141826")

    def push(state: dict) -> None:
        detail = json.dumps(state, ensure_ascii=False)
        try:
            window.evaluate_js(f"window.dispatchEvent(new CustomEvent('rw:state',{{detail:{detail}}}))")
        except Exception as exc:
            log.info("push failed: %s", type(exc).__name__)

    app.api._push = push
    window.events.shown += app.scheduler.now
    window.events.closed += app.shutdown
    webview.start(app.start, private_mode=False, storage_path=str(webview_dir(app.dir)))
