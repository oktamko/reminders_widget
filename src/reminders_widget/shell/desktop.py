"""Desktop mode: the widget sits on the wallpaper under every window and survives Win+D.

Variant D2 from the desktop probes: stay a top-level window at the bottom of the
z-order; when the desktop itself becomes the foreground window (Win+D, a click on
the wallpaper) bring the widget up without activating it; when another app becomes
foreground push it back down. Clicking the widget itself changes nothing.
"""
import ctypes
import logging
import threading
import time
from ctypes import wintypes
from typing import Callable

log = logging.getLogger("reminders_widget.desktop")
DESKTOP_CLASSES = ("Progman", "WorkerW", "SHELLDLL_DefView", "SysListView32")
TASKBAR_CLASSES = ("Shell_TrayWnd", "Shell_SecondaryTrayWnd")
MIN_VISIBLE = 80
Rect = tuple[int, int, int, int]   # x, y, w, h for windows; l, t, r, b for monitors


def foreground_kind(cls: str) -> str:
    if cls in DESKTOP_CLASSES:
        return "desktop"
    if cls in TASKBAR_CLASSES:
        return "taskbar"
    return "other"


def clamp_to_monitors(rect: Rect, monitors: list[tuple[int, int, int, int]]) -> Rect:
    """Keep the widget fully on a monitor it overlaps by MIN_VISIBLE px; else the primary."""
    x, y, w, h = rect
    if not monitors:
        return rect

    def overlap(m):
        ml, mt, mr, mb = m
        return max(0, min(x + w, mr) - max(x, ml)) >= MIN_VISIBLE and max(0, min(y + h, mb) - max(y, mt)) >= MIN_VISIBLE

    target = next((m for m in monitors if overlap(m)), None)
    if target is None:
        target = next((m for m in monitors if m[0] == 0 and m[1] == 0), monitors[0])
        ml, mt, mr, mb = target
        x, y = ml + (mr - ml - w) // 2, mt + (mb - mt - h) // 2
    ml, mt, mr, mb = target
    w, h = min(w, mr - ml), min(h, mb - mt)
    x = max(ml, min(x, mr - w))
    y = max(mt, min(y, mb - h))
    return x, y, w, h


class Win32:
    """The handful of user32/dwmapi calls the pin needs."""

    SWP = 0x0001 | 0x0002 | 0x0010          # NOSIZE | NOMOVE | NOACTIVATE

    def __init__(self):
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.dwm = ctypes.WinDLL("dwmapi")
        u = self.u
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        u.GetAncestor.restype = wintypes.HWND
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

    def set_toolwindow(self, hwnd):
        GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
        style = self.u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        self.u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)

    def round_corners(self, hwnd):
        pref = ctypes.c_int(2)   # DWMWCP_ROUND
        self.dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)

    def to_bottom(self, hwnd):
        self.u.SetWindowPos(hwnd, 1, 0, 0, 0, 0, self.SWP)            # HWND_BOTTOM

    def raise_without_activating(self, hwnd):
        self.u.ShowWindow(hwnd, 4)                                    # SW_SHOWNOACTIVATE
        self.u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, self.SWP | 0x0040)  # TOPMOST + SHOWWINDOW
        self.u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, self.SWP | 0x0040)  # NOTOPMOST keeps it on top for now

    def class_of(self, hwnd):
        buf = ctypes.create_unicode_buffer(128)
        self.u.GetClassNameW(hwnd, buf, 128)
        return buf.value

    def root_of(self, hwnd):
        return self.u.GetAncestor(hwnd, 2) or hwnd                    # GA_ROOT

    def start_foreground_hook(self, callback: Callable[[int], None]) -> Callable[[], None]:
        u = self.u
        proc = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                  ctypes.c_long, ctypes.c_long, wintypes.DWORD, wintypes.DWORD)
        u.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, proc,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        u.SetWinEventHook.restype = wintypes.HANDLE
        u.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        u.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                   wintypes.UINT, wintypes.UINT]
        stop, ready = threading.Event(), threading.Event()
        state: dict = {}
        state["proc"] = proc(lambda _h, _e, fg, *_rest: callback(fg))   # kept referenced

        def pump():
            state["hook"] = u.SetWinEventHook(3, 3, None, state["proc"], 0, 0, 0)   # EVENT_SYSTEM_FOREGROUND
            ready.set()
            msg = wintypes.MSG()
            while not stop.is_set():
                while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    u.TranslateMessage(ctypes.byref(msg))
                    u.DispatchMessageW(ctypes.byref(msg))
                time.sleep(0.02)
            if state.get("hook"):
                u.UnhookWinEvent(state["hook"])

        thread = threading.Thread(target=pump, name="rw-foreground", daemon=True)
        thread.start()
        ready.wait(2)
        if not state.get("hook"):
            log.warning("foreground hook not installed")

        def unhook():
            stop.set()
            thread.join(2)
        return unhook


class DesktopPin:
    def __init__(self, hwnd: int, *, win=None):
        self.hwnd = hwnd
        self.win = win or Win32()
        self._unhook: Callable[[], None] | None = None

    def apply(self) -> None:
        self.win.set_toolwindow(self.hwnd)
        self.win.round_corners(self.hwnd)
        self.win.to_bottom(self.hwnd)

    def start(self) -> None:
        if self._unhook is None:
            self._unhook = self.win.start_foreground_hook(self._on_foreground_hwnd)

    def stop(self) -> None:
        if self._unhook:
            self._unhook()
            self._unhook = None

    def _on_foreground_hwnd(self, fg: int) -> None:
        try:
            if not fg or fg == self.hwnd or self.win.root_of(fg) == self.hwnd:
                kind = "own"
            else:
                kind = foreground_kind(self.win.class_of(fg))
            self.on_foreground(kind)
        except Exception as exc:  # never break the hook thread
            log.warning("foreground handling failed: %s", type(exc).__name__)

    def on_foreground(self, kind: str) -> None:
        if kind == "desktop":
            self.win.raise_without_activating(self.hwnd)
        elif kind == "other":
            self.win.to_bottom(self.hwnd)
