"""Wires the app: data folder → logging → store → session → sync → scheduler → window."""
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
from reminders_widget.icloud.errors import OfflineError, SessionExpired, Transient
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
        self.api = Api(self.store, self.scheduler, signed_in=lambda: bool(self.transport.cookies()))
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

    def _cookies_work(self, cookies: list[Cookie]) -> bool:
        """Does iCloud accept these cookies? (a throwaway transport, nothing saved)"""
        try:
            HttpxTransport(cookies, lambda c: None).validate()
            return True
        except Exception:
            return False

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
        except Transient as exc:
            return SyncResult("transient", retry_after=getattr(exc, "retry_after", None))
        except SessionExpired:
            result = SyncResult("session_expired")
        if result.status == "session_expired":
            try:
                renewed = self.session.renew()
            except OfflineError:
                return SyncResult("offline")
            except Transient as exc:
                return SyncResult("transient", retry_after=getattr(exc, "retry_after", None))
            if not renewed:
                return result
            with self._engine_lock:
                self.engine = None
            try:
                return self._engine().run_once()
            except OfflineError:
                return SyncResult("offline")
            except Transient as exc:
                return SyncResult("transient", retry_after=getattr(exc, "retry_after", None))
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
            cookies = self._sign_in(webview_dir(self.dir), validate=self._cookies_work)
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

    from reminders_widget.app.host import WindowHost, default_glass_factory
    from reminders_widget.shell.system import (ShowSignal, SingleInstance, autostart_command,
                                               autostart_enabled, set_autostart)
    from reminders_widget.shell.tray import Hotkey, Tray

    instance = SingleInstance()
    if not instance.acquire():
        ShowSignal().send()          # the running widget shows itself; this launch ends
        return
    app = App(base)
    if not app.store.get_setting("autostart_initialised", False):
        try:
            set_autostart(True, autostart_command())   # spec: on by default
        except OSError as exc:
            log.warning("autostart setup failed: %s", type(exc).__name__)
        app.store.set_setting("autostart_initialised", True)
    host = WindowHost(app, webview, glass_factory=default_glass_factory)

    def toggle_autostart(enabled: bool) -> None:
        try:
            set_autostart(enabled, autostart_command())
        except OSError as exc:
            log.warning("autostart change failed: %s", type(exc).__name__)
        tray.refresh()

    tray = Tray(on_show=host.show, on_mode=host.set_mode, on_quit=host.quit,
                on_autostart=toggle_autostart, get_mode=lambda: host.mode,
                get_autostart=autostart_enabled)
    show_signal = ShowSignal()
    show_signal.listen(host.show)
    hotkey = Hotkey(host.show)
    host.create()

    def started() -> None:
        app.start()
        for part in (tray, hotkey):
            try:
                part.start()
            except Exception as exc:
                log.warning("%s failed to start: %s", type(part).__name__, type(exc).__name__)

    try:
        webview.start(started, private_mode=False, storage_path=str(webview_dir(app.dir)))
    finally:
        tray.stop()
        hotkey.stop()
        show_signal.close()
        instance.release()
