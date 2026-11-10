"""Desktop mode: the widget sits on the wallpaper under every window and survives Win+D.

Variant D2 from the desktop probes: stay a top-level window at the bottom of the
z-order; when the desktop itself becomes the foreground window (Win+D, a click on
the wallpaper) bring the widget up without activating it; when another app becomes
foreground push it back down. Clicking the widget sends it back to the bottom, unless
it was raised over the desktop (Win+D), where the bottom would hide it again.
"""
import ctypes
import logging
import threading
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
        u.IsWindowVisible.argtypes = [wintypes.HWND]
        self.dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

    def set_toolwindow(self, hwnd):
        GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
        style = self.u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        self.u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)

    def is_visible(self, hwnd):
        return bool(self.u.IsWindowVisible(hwnd))

    def hide(self, hwnd):
        self.u.ShowWindow(hwnd, 0)                                    # SW_HIDE

    def show_noactivate(self, hwnd):
        self.u.ShowWindow(hwnd, 4)                                    # SW_SHOWNOACTIVATE

    def drop_minimize_box(self, hwnd):
        """Win+D minimizes every window that can be minimized; the widget must not be one."""
        GWL_STYLE, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = -16, 0x20000, 0x10000
        style = self.u.GetWindowLongPtrW(hwnd, GWL_STYLE)
        self.u.SetWindowLongPtrW(hwnd, GWL_STYLE, style & ~(WS_MINIMIZEBOX | WS_MAXIMIZEBOX))

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
        return self._win_event_hook(3, callback)          # EVENT_SYSTEM_FOREGROUND

    def start_minimize_hook(self, callback: Callable[[int], None]) -> Callable[[], None]:
        return self._win_event_hook(0x16, callback)       # EVENT_SYSTEM_MINIMIZESTART

    def _win_event_hook(self, event: int, callback: Callable[[int], None]) -> Callable[[], None]:
        """An out-of-context WinEvent hook on its own thread; `callback(hwnd)` runs there."""
        u, k32 = self.u, ctypes.windll.kernel32
        proc = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                                  ctypes.c_long, ctypes.c_long, wintypes.DWORD, wintypes.DWORD)
        u.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, proc,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        u.SetWinEventHook.restype = wintypes.HANDLE
        u.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        u.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        u.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        ready = threading.Event()
        state: dict = {"proc": proc(lambda _h, _e, hwnd, *_rest: callback(hwnd))}   # kept referenced

        def pump():
            state["tid"] = k32.GetCurrentThreadId()
            state["hook"] = u.SetWinEventHook(event, event, None, state["proc"], 0, 0, 0)
            ready.set()
            msg = wintypes.MSG()
            while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                u.TranslateMessage(ctypes.byref(msg))
                u.DispatchMessageW(ctypes.byref(msg))
            if state.get("hook"):
                u.UnhookWinEvent(state["hook"])

        thread = threading.Thread(target=pump, name=f"rw-winevent-{event:x}", daemon=True)
        thread.start()
        ready.wait(2)
        if not state.get("hook"):
            log.warning("window event hook %x not installed", event)

        def unhook():
            if state.get("tid"):
                u.PostThreadMessageW(state["tid"], 0x0012, 0, 0)   # WM_QUIT
            thread.join(2)
        return unhook


class DesktopPin:
    def __init__(self, hwnd: int, *, win=None):
        self.hwnd = hwnd
        self.win = win or Win32()
        self._unhook: Callable[[], None] | None = None
        self._last = None          # the last foreground kind that was not the widget or the taskbar

    def apply(self) -> None:
        # The taskbar re-reads WS_EX_TOOLWINDOW only when the window is shown again.
        self.win.hide(self.hwnd)
        self.win.set_toolwindow(self.hwnd)
        self.win.drop_minimize_box(self.hwnd)
        self.win.round_corners(self.hwnd)
        self.win.show_noactivate(self.hwnd)
        self.win.to_bottom(self.hwnd)

    def start(self) -> None:
        if self._unhook is None:
            unhooks = [self.win.start_foreground_hook(self._on_foreground_hwnd),
                       self.win.start_minimize_hook(self._on_minimize)]
            self._unhook = lambda: [u() for u in unhooks]

    def stop(self) -> None:
        if self._unhook:
            self._unhook()
            self._unhook = None

    def _on_minimize(self, hwnd: int) -> None:
        """Win+D (or anything else) minimized the widget: show it again, without activating."""
        if hwnd != self.hwnd:
            return
        try:
            self.win.raise_without_activating(self.hwnd)
            if isinstance(self.win, Win32):                 # the minimize may finish after the event
                threading.Timer(0.25, self.win.raise_without_activating, (self.hwnd,)).start()
        except Exception as exc:
            log.warning("restore after minimize failed: %s", type(exc).__name__)

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
            self._last = kind
            if self.win.is_visible(self.hwnd):       # hidden to the tray: stay hidden
                self.win.raise_without_activating(self.hwnd)
        elif kind == "other":
            self._last = kind
            self.win.to_bottom(self.hwnd)
        elif kind == "own" and self._last != "desktop":
            self.win.to_bottom(self.hwnd)            # a click activates it; keep it under other windows
