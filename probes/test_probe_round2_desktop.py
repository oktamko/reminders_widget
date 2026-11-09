import base64

import pytest
from PIL import Image

from probe_round2_desktop import (
    bg_hex, compose_crop, crop_rect, jpeg_data_url, mode_name, page_url,
    parse_background_color)

MON1 = (0, 0, 1920, 1080)
MON2 = (1920, 0, 3840, 1080)
VIRT = (0, 0, 3840, 1080)
IMG = (3000, 2000)


def crop(mode, mon=MON1, win=(80, 80, 440, 540), img=IMG):
    return crop_rect(img[0], img[1], mon, VIRT, win, mode)


def approx(actual, expected):
    assert actual == pytest.approx(expected, abs=0.01)


def test_fill_monitor_at_origin():
    # scale 0.64 -> 1920x1280, centred vertically (offset -100)
    approx(crop("fill"), (125, 281.25, 562.5, 718.75))


def test_fill_second_monitor_same_relative_spot():
    approx(crop("fill", MON2, (2000, 80, 2360, 540)), (125, 281.25, 562.5, 718.75))


def test_fit_leaves_bars_so_crop_goes_outside_image():
    # scale 0.54 -> 1620x1080, offset x = 150: window starts left of the image
    approx(crop("fit"), ((80 - 150) / 0.54, 80 / 0.54, 360 / 0.54, 460 / 0.54))
    assert crop("fit")[0] < 0


def test_fit_second_monitor():
    approx(crop("fit", MON2, (2000, 80, 2360, 540)), crop("fit"))


def test_stretch_uses_separate_axis_scales():
    approx(crop("stretch"), (80 / 0.64, 80 / 0.54, 360 / 0.64, 460 / 0.54))


def test_center_is_unscaled():
    approx(crop("center"), (80 + 540, 80 + 460, 360, 460))


def test_center_second_monitor():
    approx(crop("center", MON2, (2000, 80, 2360, 540)), (620, 540, 360, 460))


def test_span_uses_virtual_screen_not_monitor():
    # fill on 3840x1080: scale 1.28 -> 3840x2560, offset y -740
    approx(crop("span", MON2, (2000, 80, 2360, 540)),
           (2000 / 1.28, (80 + 740) / 1.28, 360 / 1.28, 460 / 1.28))


def test_span_with_negative_virtual_origin():
    virt = (-1920, 0, 1920, 1080)
    sx, sy, sw, sh = crop_rect(3000, 2000, MON1, virt, (80, 80, 440, 540), "span")
    approx((sx, sw), ((80 + 1920) / 1.28, 360 / 1.28))


def test_tile_anchored_at_monitor_origin():
    approx(crop("tile", MON2, (2000, 80, 2360, 540)), (80, 80, 360, 460))


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        crop("bogus")


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


def test_compose_crop_fills_outside_with_background():
    img = Image.new("RGB", (100, 100), (255, 0, 0))
    out = compose_crop(img, (-50, -50, 200, 200), (100, 100), (0, 0, 255), "fit")
    assert out.size == (100, 100)
    assert out.getpixel((2, 2)) == (0, 0, 255)      # outside the image -> background
    assert out.getpixel((50, 50)) == (255, 0, 0)    # inside the image


def test_compose_crop_tile_wraps():
    img = Image.new("RGB", (10, 10), (0, 255, 0))
    out = compose_crop(img, (5, 5, 40, 40), (40, 40), (0, 0, 0), "tile")
    assert out.getpixel((39, 39)) == (0, 255, 0)


def test_page_url_has_no_secrets_and_is_file_uri():
    assert page_url(__file__).startswith("file:///")
