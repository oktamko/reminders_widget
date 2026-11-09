from pathlib import Path

from probe_glass import (DWMSBT_ACRYLIC, DWMSBT_MICA_ALT, DWMWA_SYSTEMBACKDROP_TYPE,
                         HWND_BOTTOM, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE,
                         extend_frame, font_file_uri, glass_page_url, hresult_text,
                         send_to_bottom, set_backdrop)


class FakeDwm:
    def __init__(self, result=0):
        self.result = result
        self.calls = []

    def DwmSetWindowAttribute(self, hwnd, attr, ref, size):
        self.calls.append(("attr", hwnd, attr, ref._obj.value, size))
        return self.result

    def DwmExtendFrameIntoClientArea(self, hwnd, margins_ref):
        m = margins_ref._obj
        self.calls.append(("extend", hwnd, (m.left, m.right, m.top, m.bottom)))
        return self.result


class FakeUser32:
    def __init__(self, result=1):
        self.result = result
        self.calls = []

    def SetWindowPos(self, *args):
        self.calls.append(args)
        return self.result


def test_font_uri_percent_encodes_non_ascii_and_spaces():
    uri = font_file_uri(Path(r"C:\Fonts\písmo VUT\OpenSans-Regular.ttf"))
    assert uri == "file:///C:/Fonts/p%C3%ADsmo%20VUT/OpenSans-Regular.ttf"


def test_page_url_carries_font_in_hash_and_survives_decoding():
    from urllib.parse import unquote
    url = glass_page_url(Path(r"C:\x\glass.html"), "file:///C:/F/p%C3%ADsmo/a.ttf")
    assert url.startswith("file:///C:/x/glass.html#font=")
    assert unquote(url.split("#font=")[1]) == "file:///C:/F/p%C3%ADsmo/a.ttf"


def test_page_url_without_font_has_no_hash():
    assert "#" not in glass_page_url(Path(r"C:\x\glass.html"), None)


def test_set_backdrop_passes_attribute_38_and_value():
    dwm = FakeDwm()
    assert set_backdrop(1234, DWMSBT_ACRYLIC, dwm) == 0
    assert dwm.calls == [("attr", 1234, DWMWA_SYSTEMBACKDROP_TYPE, 3, 4)]
    assert DWMWA_SYSTEMBACKDROP_TYPE == 38 and DWMSBT_MICA_ALT == 4


def test_extend_frame_uses_minus_one_margins():
    dwm = FakeDwm()
    extend_frame(7, dwm)
    assert dwm.calls == [("extend", 7, (-1, -1, -1, -1))]


def test_send_to_bottom_flags():
    user32 = FakeUser32()
    assert send_to_bottom(99, user32) is True
    hwnd, after, x, y, cx, cy, flags = user32.calls[0]
    assert (hwnd, after, x, y, cx, cy) == (99, HWND_BOTTOM, 0, 0, 0, 0)
    assert flags == SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
    assert HWND_BOTTOM == 1


def test_send_to_bottom_reports_failure():
    assert send_to_bottom(99, FakeUser32(result=0)) is False


def test_hresult_text_is_unsigned_hex():
    assert hresult_text(0) == "0x00000000"
    assert hresult_text(-2147024809) == "0x80070057"
