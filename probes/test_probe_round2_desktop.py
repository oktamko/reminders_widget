import base64

import pytest
from PIL import Image

from probe_round2_desktop import (
    bg_hex, build_composite, css_size, frost, image_dest_rect, jpeg_data_url, mode_name,
    origin_css, page_url, parse_background_color, translate_offset)

# two monitors: B (negative x, 1920x1080) left of the primary A
MON_B = (-1920, 0, 0, 1080)
MON_A = (0, 0, 1920, 1080)
VIRT2 = (-1920, 0, 1920, 1080)
IMG = (3000, 2000)


def dest(mode, ref):
    return image_dest_rect(IMG[0], IMG[1], ref, mode)


def approx(actual, expected):
    assert actual == pytest.approx(expected, abs=0.01)


def test_fill_covers_monitor_centred():
    # scale 0.64 -> 1920x1280, 100 px cropped top and bottom
    approx(dest("fill", MON_A), (0, -100, 1920, 1180))


def test_fill_on_monitor_at_negative_x():
    approx(dest("fill", MON_B), (-1920, -100, 0, 1180))


def test_fit_leaves_side_bars():
    # scale 0.54 -> 1620x1080
    approx(dest("fit", MON_A), (150, 0, 1770, 1080))
    approx(dest("fit", MON_B), (-1770, 0, -150, 1080))


def test_stretch_is_the_monitor():
    approx(dest("stretch", MON_B), (-1920, 0, 0, 1080))


def test_center_is_unscaled():
    approx(dest("center", MON_A), (-540, -460, 2460, 1540))


def test_span_uses_virtual_screen():
    # scale 1.28 -> 3840x2560, centred on the 3840x1080 virtual screen
    approx(dest("span", VIRT2), (-1920, -740, 1920, 1820))


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        dest("bogus", MON_A)


def test_composite_places_each_monitor_and_background_in_bars():
    img = Image.new("RGB", (100, 100), (255, 0, 0))
    comp = build_composite(img, [(-200, 0, 0, 100), (0, 0, 200, 100)], (-200, 0, 200, 100),
                           "fit", (0, 0, 255))
    assert comp.size == (400, 100)
    assert comp.getpixel((5, 50)) == (0, 0, 255)      # left monitor, left bar
    assert comp.getpixel((100, 50)) == (255, 0, 0)    # left monitor, image
    assert comp.getpixel((205, 50)) == (0, 0, 255)    # right monitor, left bar
    assert comp.getpixel((300, 50)) == (255, 0, 0)    # right monitor, image


def test_composite_span_is_one_image_across_both():
    img = Image.new("RGB", (40, 10), (0, 255, 0))
    comp = build_composite(img, [(-20, 0, 0, 10), (0, 0, 20, 10)], (-20, 0, 20, 10),
                           "span", (0, 0, 0))
    assert comp.getpixel((0, 5)) == (0, 255, 0) and comp.getpixel((39, 5)) == (0, 255, 0)


def test_css_size_and_origin_use_scale():
    assert css_size((3840, 1080), 1.25) == (3072, 864)
    assert origin_css((-1920, 0, 1920, 1080), 1.25) == (-1536.0, 0.0)


def test_translate_offset_keeps_image_fixed_on_screen():
    # window at screen (100, 80) CSS, image origin (-1536, 0): layer shifted by origin - window
    assert translate_offset((-1536, 0), (100, 80)) == (-1636, -80)
    # window moves 10 px right: layer moves 10 px left, so the image stays put on screen
    assert translate_offset((-1536, 0), (110, 80)) == (-1646, -80)
    # constant correction (e.g. measured JS vs Win32 offset) is added to the window position
    assert translate_offset((0, 0), (100, 80), (0, -3)) == (-100, -77)


def test_frost_blurs_and_saturates():
    img = Image.new("RGB", (60, 60), (0, 0, 0))
    img.paste((200, 100, 100), (30, 0, 60, 60))
    out = frost(img, radius=4, saturation=1.7)
    assert out.size == img.size
    assert 0 < out.getpixel((30, 30))[0] < 200  # edge is blurred


def test_mode_name_from_registry_values():
    assert mode_name("10", "0") == "fill"
    assert mode_name("6", "0") == "fit"
    assert mode_name("2", "0") == "stretch"
    assert mode_name("22", "0") == "span"
    assert mode_name("0", "0") == "center"
    assert mode_name("0", "1") == "tile"
    assert mode_name(None, None) == "fill"  # Windows default when unset


def test_background_colour_parsing():
    assert parse_background_color("0 78 152") == (0, 78, 152)
    assert parse_background_color("garbage") == (0, 0, 0)
    assert parse_background_color(None) == (0, 0, 0)
    assert bg_hex((0, 78, 152)) == "#004e98"


def test_data_url_roundtrip():
    url = jpeg_data_url(b"\xff\xd8abc")
    assert url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"\xff\xd8abc"


def test_page_url_has_no_secrets_and_is_file_uri():
    assert page_url(__file__).startswith("file:///")


def test_error_logging_never_contains_the_path_in_the_exception_message():
    from probe_round2_desktop import safe_error
    exc = FileNotFoundError(r"C:\Users\fakeuser\AppData\wall.jpg")
    text = safe_error(exc)
    assert "fakeuser" not in text and "wall.jpg" not in text
    assert "FileNotFoundError" in text


def test_cropper_open_failure_is_logged_without_path(tmp_path, monkeypatch):
    import probe_round2_desktop as m

    bad = tmp_path / "fakeuser_secret.jpg"
    bad.write_bytes(b"not an image")
    lines = []

    class R:
        def add(self, step, status, detail=""):
            lines.append(f"{step} {status} {detail}")

    monkeypatch.setattr(m, "bind_user32", lambda: object())
    monkeypatch.setattr(m, "read_wallpaper_path", lambda u: (str(bad), "SystemParametersInfoW"))
    monkeypatch.setattr(m, "read_wallpaper_registry", lambda: ("fill", (0, 0, 0)))
    monkeypatch.setattr(m, "window_rect", lambda u, h: (0, 0, 1, 1))
    import threading
    ok = m.run_part_c_safely(None, 1, object(), type("D", (), {"DwmSetWindowAttribute":
                             lambda *a: 0})(), R(), threading.Event())
    assert ok is False
    assert not any("fakeuser" in line or "secret" in line for line in lines)


def test_foreground_kind_classification():
    from probe_round2_desktop import foreground_kind
    assert foreground_kind("Progman") == "plocha"
    assert foreground_kind("WorkerW") == "plocha"
    assert foreground_kind("Shell_TrayWnd") == "panel úloh"
    assert foreground_kind("CabinetWClass") == "iná aplikácia"
