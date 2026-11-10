"""Tray icon (pystray) and the global hotkey Ctrl+Alt+R (RegisterHotKey)."""
import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger("reminders_widget.tray")
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
HOTKEY_ID = 0x5257   # "RW"


def menu_items(tray: "Tray") -> list[dict]:
    """The tray menu as plain data (text, action, checked, radio, default)."""
    return [
        {"text": "Show reminders", "action": tray.on_show, "checked": None, "radio": False, "default": True},
        {"text": "Desktop widget", "action": lambda: tray.on_mode("desktop"),
         "checked": lambda: tray.get_mode() == "desktop", "radio": True, "default": False},
        {"text": "Normal window", "action": lambda: tray.on_mode("window"),
         "checked": lambda: tray.get_mode() == "window", "radio": True, "default": False},
        {"text": "Start with Windows", "action": lambda: tray.on_autostart(not tray.get_autostart()),
         "checked": lambda: bool(tray.get_autostart()), "radio": False, "default": False},
        {"text": "Quit", "action": tray.on_quit, "checked": None, "radio": False, "default": False},
    ]


def _icon_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), outline=(133, 183, 235, 255), width=7)
    d.line((22, 33, 30, 41, 44, 24), fill=(255, 255, 255, 255), width=6, joint="curve")
    return img


class Tray:
    def __init__(self, *, on_show, on_mode, on_autostart, on_quit, get_mode, get_autostart,
                 icon_factory: Callable | None = None):
        self.on_show, self.on_mode, self.on_autostart, self.on_quit = on_show, on_mode, on_autostart, on_quit
        self.get_mode, self.get_autostart = get_mode, get_autostart
        self._factory = icon_factory
        self._icon = None

    def _build(self):
        import pystray
        entries = []
        for item in menu_items(self):
            checked = item["checked"]
            entries.append(pystray.MenuItem(
                item["text"], (lambda a: (lambda icon, it: a()))(item["action"]),
                checked=(lambda c: (lambda it: c()))(checked) if checked else None,
                radio=item["radio"], default=item["default"]))
            if item["text"] in ("Normal window", "Start with Windows"):
                entries.append(pystray.Menu.SEPARATOR)
        factory = self._factory or pystray.Icon
        return factory(name="RemindersWidget", icon=_icon_image(), title="Reminders",
                       menu=pystray.Menu(*entries))

    def start(self) -> None:
        self._icon = self._build()
        if self._icon is not None:
            self._icon.run_detached()

    def refresh(self) -> None:
        if self._icon is not None:
            self._icon.update_menu()

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()
            self._icon = None


class Hotkey:
    """Ctrl+Alt+R on its own thread with a message loop."""

    def __init__(self, callback: Callable[[], None], *, vk: int = ord("R"),
                 mods: int = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, user32=None):
        self.callback, self.vk, self.mods = callback, vk, mods
        self._u = user32
        self._thread: threading.Thread | None = None
        self._tid = 0
        self.registered = False

    def _user32(self):
        if self._u is None:
            u = ctypes.WinDLL("user32", use_last_error=True)
            u.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            u.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            self._u = u
        return self._u

    def _loop(self) -> None:
        u = self._user32()
        self._tid = ctypes.windll.kernel32.GetCurrentThreadId()
        if not u.RegisterHotKey(None, HOTKEY_ID, self.mods, self.vk):
            log.info("hotkey Ctrl+Alt+R is taken by another app; not registered")
            return
        self.registered = True
        msg = wintypes.MSG()
        try:
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self.callback()
                    except Exception as exc:
                        log.warning("hotkey callback failed: %s", type(exc).__name__)
        finally:
            u.UnregisterHotKey(None, HOTKEY_ID)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="rw-hotkey", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def stop(self) -> None:
        if self._thread and self._tid:
            self._user32().PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(2)
