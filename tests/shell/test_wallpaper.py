import pytest
from PIL import Image

from reminders_widget.shell.wallpaper import (FILL_TOP_SHARE, build_glass_image, compose, css_size,
                                              image_dest_rect, luminance, measure_offset, mode_name,
                                              origin_css, parse_background_color)

# Martin's PC, measured with PaintDesktop on 2026-11-10: 8400x5600 image, "fill".
MON_A = (2560, 104, 4480, 1304)    # 1920x1200
MON_B = (0, 0, 2560, 1440)         # 2560x1440
VIRTUAL = (0, 0, 4480, 1440)


def test_fill_crops_a_third_of_the_vertical_overflow_from_the_top():
    assert FILL_TOP_SHARE == pytest.approx(1 / 3)
    l, t, r, b = image_dest_rect(8400, 5600, MON_B, "fill")
    assert (r - l) == pytest.approx(2560)
    overflow = (b - t) - 1440
    assert t == pytest.approx(-overflow / 3)          # measured: -88.3 ± 1
    l, t, r, b = image_dest_rect(8400, 5600, MON_A, "fill")
    assert t - MON_A[1] == pytest.approx(-((b - t) - 1200) / 3)   # measured: -26 ± 1


def test_fill_horizontal_overflow_is_centred():
    l, t, r, b = image_dest_rect(4000, 1000, (0, 0, 1000, 1000), "fill")
    assert l == pytest.approx(-(r - l - 1000) / 2) and t == 0


@pytest.mark.parametrize("mode", ["stretch", "fit", "center", "tile", "span"])
def test_other_modes_keep_their_layout(mode):
    l, t, r, b = image_dest_rect(1000, 500, (0, 0, 1000, 1000), mode)
    if mode == "stretch":
        assert (l, t, r, b) == (0, 0, 1000, 1000)
    if mode == "fit":
        assert (l, t, r, b) == (0, 250, 1000, 750)


def _stripes(w, h):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = ((x * 7) % 256, (y * 5) % 256, ((x + y) * 3) % 256)
    return img


def test_compose_matches_a_windows_like_rendering_on_two_monitors():
    img = _stripes(420, 280)                       # 3:2 like the real wallpaper
    mon_a, mon_b, virt = (128, 5, 224, 65), (0, 0, 128, 72), (0, 0, 224, 72)
    ours = compose(img, [mon_a, mon_b], virt, "fill", (0, 0, 0))
    # reference: each monitor filled with the image scaled to width, top cut by 1/3 of the overflow
    ref = Image.new("RGB", (224, 72))
    for ml, mt, mr, mb in (mon_a, mon_b):
        w, h = mr - ml, mb - mt
        scaled = img.resize((w, round(280 * w / 420)), Image.LANCZOS)
        top = round((scaled.height - h) / 3)
        ref.paste(scaled.crop((0, top, w, top + h)), (ml, mt))
    dx, dy, err = measure_offset(ours, ref, search=8, step=1, window=20)
    assert (dx, dy) == (0, 0)


def test_measure_offset_finds_a_known_shift():
    img = _stripes(300, 200)
    shifted = Image.new("RGB", img.size)
    shifted.paste(img, (0, 7))
    dx, dy, _ = measure_offset(img, shifted, search=16, step=2, window=40)
    assert (dx, dy) == (0, 7)


def test_registry_parsing():
    assert mode_name("10", "0") == "fill" and mode_name("0", "1") == "tile" and mode_name(None, None) == "fill"
    assert parse_background_color("10 20 30") == (10, 20, 30) and parse_background_color("bad") == (0, 0, 0)


def test_css_size_and_origin():
    assert css_size((4480, 1440), 1.25) == (3584, 1152)
    assert origin_css((-1920, 0, 2560, 1440), 1.25) == (-1536.0, 0.0)


def test_build_glass_image_and_luminance():
    white = Image.new("RGB", (400, 200), (255, 255, 255))
    black = Image.new("RGB", (400, 200), (0, 0, 0))
    g = build_glass_image(white, 1.25)
    assert g.size == (320, 160)
    assert luminance(white) == pytest.approx(1.0, abs=0.01)
    assert luminance(black) == pytest.approx(0.0, abs=0.01)


def test_glass_payload_is_a_jpeg_data_url_with_geometry():
    from reminders_widget.shell.wallpaper import Glass, glass_payload
    g = Glass(Image.new("RGB", (320, 160), (10, 20, 30)), (-1536.0, 0.0), 0.2, (0, 0, 0))
    p = glass_payload(g)
    assert p["url"].startswith("data:image/jpeg;base64,")
    assert p["width"] == 320 and p["height"] == 160 and p["origin"] == [-1536.0, 0.0]
    assert p["luminance"] == 0.2 and p["background"] == "#000000"


def test_glass_payload_without_image():
    from reminders_widget.shell.wallpaper import Glass, glass_payload
    p = glass_payload(Glass(None, (0.0, 0.0), 0.9, (240, 240, 240)))
    assert p["url"] is None and p["background"] == "#f0f0f0" and p["luminance"] == 0.9


def test_watcher_rebuilds_only_when_the_signature_changes():
    import time as _t
    from reminders_widget.shell.wallpaper import GlassWatcher
    sigs = iter(["a", "a", "b", "b"])
    built = []
    w = GlassWatcher(on_change=built.append, signature=lambda: next(sigs, "b"),
                     build=lambda: "glass-" + str(len(built)), interval=0.02)
    w.start()
    _t.sleep(0.3)
    w.stop()
    assert built == ["glass-0", "glass-1"]          # once at start, once after a -> b


def test_luminance_grid_reports_light_and_dark_areas_separately():   # final review #2
    from reminders_widget.shell.wallpaper import luminance_grid
    img = Image.new("RGB", (128, 64), (0, 0, 0))
    img.paste(Image.new("RGB", (64, 64), (255, 255, 255)), (64, 0))
    g = luminance_grid(img, cell=32)
    assert (g["cell"], g["cols"], g["rows"]) == (32, 4, 2)
    rows = [g["values"][i * 4:(i + 1) * 4] for i in range(2)]
    for row in rows:
        assert row[0] == pytest.approx(0.0, abs=0.02) and row[3] == pytest.approx(1.0, abs=0.02)


def test_glass_payload_carries_the_luminance_grid():
    from reminders_widget.shell.wallpaper import Glass, glass_payload
    p = glass_payload(Glass(Image.new("RGB", (100, 40), (255, 255, 255)), (0.0, 0.0), 1.0, (0, 0, 0)))
    assert p["grid"]["cols"] == 4 and p["grid"]["rows"] == 2 and len(p["grid"]["values"]) == 8
    assert glass_payload(Glass(None, (0.0, 0.0), 0.9, (240, 240, 240)))["grid"] is None
