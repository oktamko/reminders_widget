"""iCloud session cookies, stored only DPAPI-encrypted for the current Windows user."""
import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
from pathlib import Path

CRYPTPROTECT_UI_FORBIDDEN = 0x1
_ENTROPY = b"RemindersWidget session v1"


class SecretError(Exception):
    """DPAPI could not protect or unprotect the data."""


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data) if data else (ctypes.c_ubyte * 1)()
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
for _fn in (_crypt32.CryptProtectData, _crypt32.CryptUnprotectData):
    _fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob), ctypes.c_void_p,
                    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    _fn.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p


def _call(fn, data: bytes) -> bytes:
    src, _keep = _blob(data)
    ent, _keep2 = _blob(_ENTROPY)
    out = _Blob()
    if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None,
              CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise SecretError(f"DPAPI failed (error {ctypes.get_last_error()})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def protect(data: bytes) -> bytes:
    return _call(_crypt32.CryptProtectData, data)


def unprotect(blob: bytes) -> bytes:
    return _call(_crypt32.CryptUnprotectData, blob)


@dataclass
class Cookie:
    name: str
    value: str = field(repr=False)
    domain: str
    path: str = "/"
    secure: bool = True
    expires: float | None = None


def save_cookies(path: Path, cookies: list[Cookie]) -> None:
    """Encrypt and atomically replace the session file."""
    path = Path(path)
    blob = protect(json.dumps([asdict(c) for c in cookies]).encode("utf-8"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(blob)
    os.replace(tmp, path)


def load_cookies(path: Path) -> list[Cookie] | None:
    """The saved cookies, or None if the file is missing or cannot be decrypted/read."""
    try:
        data = json.loads(unprotect(Path(path).read_bytes()).decode("utf-8"))
        return [Cookie(**item) for item in data]
    except (OSError, SecretError, ValueError, TypeError):
        return None
