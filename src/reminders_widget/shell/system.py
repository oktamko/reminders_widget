"""One running instance (named mutex + a 'show yourself' event) and autostart (HKCU Run)."""
import ctypes
import logging
import sys
import threading
import winreg
from ctypes import wintypes
from pathlib import Path
from typing import Callable

log = logging.getLogger("reminders_widget.system")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "RemindersWidget"
ERROR_ALREADY_EXISTS = 183

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateEventW.restype = wintypes.HANDLE
_k32.SetEvent.argtypes = [wintypes.HANDLE]
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]


class SingleInstance:
    def __init__(self, name: str = "Local\\RemindersWidget.Single"):
        self.name = name
        self._handle = None

    def acquire(self) -> bool:
        """True for the first instance; False when another one already runs."""
        handle = _k32.CreateMutexW(None, False, self.name)
        already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        if already:
            _k32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle:
            _k32.CloseHandle(self._handle)
            self._handle = None


class ShowSignal:
    """A named auto-reset event: later launches `send()`, the running one `listen()`s."""

    def __init__(self, name: str = "Local\\RemindersWidget.Show"):
        self._handle = _k32.CreateEventW(None, False, False, name)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def send(self) -> None:
        _k32.SetEvent(self._handle)

    def listen(self, callback: Callable[[], None]) -> None:
        def loop():
            while not self._stop.is_set():
                if _k32.WaitForSingleObject(self._handle, 250) == 0 and not self._stop.is_set():
                    try:
                        callback()
                    except Exception as exc:
                        log.warning("show callback failed: %s", type(exc).__name__)
        self._thread = threading.Thread(target=loop, name="rw-show", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(1)
        if self._handle:
            _k32.CloseHandle(self._handle)
            self._handle = None


def autostart_command(frozen: bool | None = None, executable: str | None = None) -> str:
    """The Run command: the installed exe, or pythonw.exe -m reminders_widget from source."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    exe = Path(executable or sys.executable)
    if frozen:
        return f'"{exe}"'
    pythonw = exe.with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else exe}" -m reminders_widget'


def autostart_value(value: str = RUN_VALUE) -> str | None:
    """The stored Run command, or None when autostart is off."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            data = winreg.QueryValueEx(k, value)[0]
            return data if isinstance(data, str) else None
    except OSError:
        return None


def autostart_enabled(value: str = RUN_VALUE) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, value)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool, command: str, value: str = RUN_VALUE) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
        if enabled:
            winreg.SetValueEx(k, value, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(k, value)
            except OSError:
                pass
