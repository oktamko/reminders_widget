from reminders_widget.app.secrets import (Cookie, SecretError, load_cookies, protect,
                                          save_cookies, unprotect)
import pytest

SECRET = "AQAAAsecretcookievalue1234567890"


def _jar():
    return [Cookie("X-APPLE-WEBAUTH-TOKEN", SECRET, ".icloud.com", "/", True, None),
            Cookie("X-APPLE-WEBAUTH-USER", "u-" + SECRET, ".icloud.com", "/", True, 1893456000.0)]


def test_protect_round_trip():
    blob = protect(b"hello")
    assert blob != b"hello" and unprotect(blob) == b"hello"


def test_unprotect_garbage_raises_secret_error():
    with pytest.raises(SecretError):
        unprotect(b"not a dpapi blob")


def test_save_and_load_cookies(tmp_path):
    path = tmp_path / "session.bin"
    save_cookies(path, _jar())
    assert load_cookies(path) == _jar()


def test_file_never_contains_a_cookie_value(tmp_path):
    path = tmp_path / "session.bin"
    save_cookies(path, _jar())
    raw = path.read_bytes()
    assert SECRET.encode() not in raw and b"X-APPLE-WEBAUTH" not in raw


def test_corrupted_or_missing_file_gives_none(tmp_path):
    path = tmp_path / "session.bin"
    assert load_cookies(path) is None
    path.write_bytes(b"\x00\x01garbage")
    assert load_cookies(path) is None


def test_repr_hides_value():
    assert SECRET not in repr(_jar()[0]) and "X-APPLE-WEBAUTH-TOKEN" in repr(_jar()[0])
