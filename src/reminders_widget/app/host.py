"""The pywebview window(s) around the App: desktop widget or normal window.

Desktop mode: frameless 360×560, pinned under all windows (DesktopPin), glass from
the wallpaper pushed as `rw:glass`, closing hides it to the tray. Window mode: a
normal framed window. Switching creates the new window before destroying the old
one, so the GUI loop never runs out of windows.
"""
import json
import logging
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger("reminders_widget.host")
UI_INDEX = Path(__file__).parent / "ui" / "index.html"
SIZES = {"desktop": (360, 560), "window": (400, 640)}
MODES = ("desktop", "window")


class WindowHost:
    def __init__(self, app, webview, *, pin_factory: Callable | None = None,
                 hwnd_of: Callable | None = None, monitors: Callable | None = None,
                 glass_factory: Callable | None = None, save_delay: float = 0.5,
                 shutting_down: Callable[[], bool] | None = None):
        self.app, self.webview = app, webview
        self._pin_factory = pin_factory or _default_pin
        self._hwnd_of = hwnd_of or (lambda w: w.native.Handle.ToInt64())
        self._monitors = monitors or _default_monitors
        self._glass_factory = glass_factory
        self._save_delay = save_delay
        self._shutting_down = shutting_down or _windows_shutting_down
        self.window = None
        self._pin = None
        self._glass = None          # GlassWatcher
        self._last_glass: dict | None = None
        self._quitting = False
        self._save_timer: threading.Timer | None = None
        self._replacing = False
        app.api._mode_hook = self.set_mode
        app.api._push = self.push_state

    # --- mode ------------------------------------------------------------------
    @property
    def mode(self) -> str:
        mode = self.app.store.get_setting("mode", "desktop")
        return mode if mode in MODES else "desktop"

    def set_mode(self, mode: str) -> None:
        if mode not in MODES or (mode == self.mode and self.window is not None):
            return
        self.app.store.set_setting("mode", mode)
        old, old_pin = self.window, self._pin
        self._replacing = True
        try:
            self.create()
            if old_pin:
                old_pin.stop()
            if old is not None:
                old.destroy()
        finally:
            self._replacing = False

    # --- window ----------------------------------------------------------------
    def _rect(self) -> tuple[int, int, int, int] | None:
        from reminders_widget.shell.desktop import clamp_to_monitors
        w, h = SIZES["desktop"]
        saved = self.app.store.get_setting("widget_rect", None)
        if not (isinstance(saved, list) and len(saved) == 4):
            return None
        return clamp_to_monitors((int(saved[0]), int(saved[1]), w, h), self._monitors())

    def create(self):
        mode = self.mode
        width, height = SIZES[mode]
        kw = dict(js_api=self.app.api, width=width, height=height, background_color="#141826")
        if mode == "desktop":
            kw.update(frameless=True, easy_drag=False, resizable=False)
            rect = self._rect()
            if rect:
                kw.update(x=rect[0], y=rect[1])
        else:
            kw.update(min_size=(320, 420))
        window = self.webview.create_window("Reminders", str(UI_INDEX), **kw)
        self.window, self._pin = window, None
        window.events.shown += lambda: self._on_shown(window, mode)
        window.events.closing += lambda *a: self._on_closing(window, mode)
        window.events.closed += lambda *a: self._on_closed(window)
        window.events.moved += lambda *a: self._on_moved(window, mode, *a)
        return window

    def _on_shown(self, window, mode: str) -> None:
        try:
            if hasattr(self.app, "scheduler"):
                self.app.scheduler.now()
            if mode == "desktop":
                self._pin = self._pin_factory(self._hwnd_of(window))
                self._pin.apply()
                self._pin.start()
                self._start_glass(window)
            self._js(window, f"document.body.classList.add('mode-{mode}')")
            if self._last_glass and mode == "desktop":
                self.push_glass(self._last_glass)
        except Exception as exc:
            log.warning("window setup failed: %s", type(exc).__name__)

    def _on_closing(self, window, mode: str):
        if self._quitting or self._replacing or window is not self.window:
            return True
        if self._shutting_down():   # never veto a Windows restart or sign-out
            return True
        if mode == "desktop":
            window.hide()           # the tray brings it back
            return False
        return True

    def _on_closed(self, window) -> None:
        if window is self.window and not self._replacing:
            self.quit()

    def _on_moved(self, window, mode: str, x=None, y=None) -> None:
        if mode != "desktop" or x is None or y is None:
            return
        w, h = SIZES["desktop"]

        def save():
            self.app.store.set_setting("widget_rect", [int(x), int(y), w, h])
        if self._save_delay <= 0:
            save()
            return
        if self._save_timer:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(self._save_delay, save)
        self._save_timer.daemon = True
        self._save_timer.start()

    # --- page pushes -------------------------------------------------------------
    def _js(self, window, code: str) -> None:
        try:
            window.evaluate_js(code)
        except Exception as exc:
            log.info("evaluate_js failed: %s", type(exc).__name__)

    def push_state(self, state: dict) -> None:
        if self.window is not None:
            detail = json.dumps(state, ensure_ascii=False)
            self._js(self.window, f"window.dispatchEvent(new CustomEvent('rw:state',{{detail:{detail}}}))")

    def push_glass(self, payload: dict) -> None:
        self._last_glass = payload
        if self.window is not None and self.mode == "desktop":
            detail = json.dumps(payload)
            self._js(self.window, f"window.dispatchEvent(new CustomEvent('rw:glass',{{detail:{detail}}}))")

    def _start_glass(self, window) -> None:
        if self._glass is not None or self._glass_factory is None:
            return
        self._glass = self._glass_factory(self.push_glass, self._hwnd_of(window))
        self._glass.start()

    # --- show / quit -------------------------------------------------------------
    def show(self) -> None:
        if self.window is None:
            return
        self.window.show()
        if self._pin is not None:
            self._pin.on_foreground("desktop")

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        for stopper in (self._pin, self._glass):
            try:
                if stopper:
                    stopper.stop()
            except Exception:
                pass
        if self.window is not None and not getattr(self.window, "destroyed", False):
            try:
                self.window.destroy()
            except Exception:
                pass
        self.app.shutdown()
        for other in list(getattr(self.webview, "windows", [])):
            try:
                other.destroy()
            except Exception:
                pass


def _windows_shutting_down() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.user32.GetSystemMetrics(0x2000))   # SM_SHUTTINGDOWN
    except Exception:
        return False


def _default_pin(hwnd):
    from reminders_widget.shell.desktop import DesktopPin
    return DesktopPin(hwnd)


def _default_monitors():
    from reminders_widget.shell.wallpaper import monitors
    return [m.rect for m in monitors()]


def default_glass_factory(on_change, hwnd):
    """GlassWatcher building glass at the widget's DPI scale."""
    import ctypes
    from reminders_widget.shell.wallpaper import GlassWatcher, build_glass, glass_payload
    try:
        scale = ctypes.windll.user32.GetDpiForWindow(hwnd) / 96 or 1.0
    except Exception:
        scale = 1.0
    return GlassWatcher(on_change, build=lambda: glass_payload(build_glass(scale)))
