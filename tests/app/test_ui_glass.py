"""Glass layer and contrast tokens in the window (Playwright, headless)."""
import json

import pytest

from tests.app.test_ui import EXE, FAKE, STATE, UI

pytestmark = pytest.mark.skipif(EXE is None, reason="no Chromium-based browser for UI tests")
PIXEL = ("data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4n"
         "ICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/"
         "2gAIAQEAAD8AKp//2Q==")
SCREEN = """
window.__sx = 80; window.__sy = 80;
Object.defineProperty(window, 'screenX', {get: () => window.__sx, configurable: true});
Object.defineProperty(window, 'screenY', {get: () => window.__sy, configurable: true});
"""


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=EXE, headless=True)
        yield b
        b.close()


def open_page(browser, reduced=False):
    page = browser.new_page(viewport={"width": 360, "height": 560})
    if reduced:
        page.emulate_media(reduced_motion="reduce")
    page.add_init_script(SCREEN + FAKE % json.dumps(STATE))
    page.goto(UI.as_uri())
    page.wait_for_selector(".row")
    return page


def grid(fn, width=3584, height=1152, cell=32):
    cols, rows = -(-width // cell), -(-height // cell)
    return {"cell": cell, "cols": cols, "rows": rows,
            "values": [fn(c, r) for r in range(rows) for c in range(cols)]}


def jpeg(rgb):
    import base64
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), rgb).save(buf, "JPEG", quality=95)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def send_glass(page, luminance=0.2, url=PIXEL, lum_grid=None):
    page.evaluate("""g => window.dispatchEvent(new CustomEvent('rw:glass', {detail: g}))""",
                  {"url": url, "width": 3584, "height": 1152, "origin": [0, 0],
                   "luminance": luminance, "background": "#101020", "grid": lum_grid})
    page.wait_for_timeout(100)


def wall_offset(page):
    return page.evaluate("""() => { const w = document.querySelector('.wall');
        const m = new DOMMatrix(getComputedStyle(w).transform); return [m.m41, m.m42]; }""")


def test_glass_layer_has_the_image_size_and_follows_the_window(browser):
    page = open_page(browser)
    send_glass(page)
    wall = page.locator(".wall")
    assert wall.count() == 1
    assert page.evaluate("getComputedStyle(document.querySelector('.wall')).width") == "3584px"
    gx, gy = page.evaluate("(() => { const r = document.getElementById('g').getBoundingClientRect(); return [r.left, r.top]; })()")
    x0, y0 = wall_offset(page)
    assert (x0, y0) == (pytest.approx(-(80 + gx), abs=1.5), pytest.approx(-(80 + gy), abs=1.5))
    page.evaluate("window.__sx = 380; window.__sy = 280")
    page.wait_for_timeout(120)
    x1, y1 = wall_offset(page)
    assert (x1 - x0, y1 - y0) == (pytest.approx(-300, abs=0.5), pytest.approx(-200, abs=0.5))


def _contrast(page):
    """Text vs the glass tint composited over white (worst case for dark text)."""
    return page.evaluate("""() => {
      const p = s => s.match(/[\\d.]+/g).map(Number);
      const lum = ([r, g, b]) => [r, g, b].map(v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; })
                                 .reduce((a, v, i) => a + v * [.2126, .7152, .0722][i], 0);
      const fg = p(getComputedStyle(document.querySelector('.row .t')).color);
      const bgc = p(getComputedStyle(document.getElementById('g')).backgroundColor);
      const a = bgc[3] ?? 1, under = document.body.classList.contains('light') ? 255 : 0;
      const bg = [0, 1, 2].map(i => bgc[i] * a + under * (1 - a));
      const [L1, L2] = [lum(fg.slice(0, 3)), lum(bg)].sort((x, y) => y - x);
      return (L1 + .05) / (L2 + .05);
    }""")


def test_light_wallpaper_gets_dark_text_with_enough_contrast(browser):   # Review Focus 5
    page = open_page(browser)
    send_glass(page, luminance=0.9)
    assert page.evaluate("document.body.classList.contains('light')")
    assert _contrast(page) >= 4.5


def test_dark_wallpaper_gets_light_text(browser):
    page = open_page(browser)
    send_glass(page, luminance=0.1)
    assert page.evaluate("document.body.classList.contains('dark')")
    assert _contrast(page) >= 4.5


def test_no_glass_event_means_no_wall_layer(browser):   # window mode
    page = open_page(browser)
    assert page.locator(".wall").count() == 0


def test_solid_colour_desktop_uses_the_tinted_style(browser):   # Review Focus 1
    page = open_page(browser)
    send_glass(page, url=None, luminance=0.95)
    assert page.locator(".wall").count() == 0 and page.evaluate("document.body.classList.contains('light')")


def test_reduced_motion_shortens_transitions(browser):
    page = open_page(browser, reduced=True)
    dur = page.evaluate("getComputedStyle(document.querySelector('.row')).transitionDuration")
    assert all(float(d.rstrip("s")) <= 0.01 for d in dur.split(","))


def _lum(rgb):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= .03928 else ((v + .055) / 1.055) ** 2.4
    return .2126 * ch(rgb[0]) + .7152 * ch(rgb[1]) + .0722 * ch(rgb[2])


def test_rendered_glass_keeps_text_readable_over_a_mid_grey_wallpaper(browser):   # final review #2
    page = open_page(browser)
    send_glass(page, luminance=0.5, url=jpeg((128, 128, 128)), lum_grid=grid(lambda c, r: 0.5))
    fg = page.evaluate("getComputedStyle(document.querySelector('.row .t')).color")
    page.add_style_tag(content=".top,.main{visibility:hidden}")
    page.wait_for_timeout(50)
    from PIL import Image
    import io
    shot = Image.open(io.BytesIO(page.screenshot())).convert("RGB")
    bg = shot.getpixel((180, 300))
    text = [int(float(v)) for v in fg[fg.index("(") + 1:fg.index(")")].split(",")[:3]]
    l1, l2 = sorted([_lum(text), _lum(bg)], reverse=True)
    assert (l1 + .05) / (l2 + .05) >= 4.5


def test_text_tone_follows_the_wallpaper_under_the_widget(browser):   # final review #2
    page = open_page(browser)
    light_left = grid(lambda c, r: 0.9 if c < 56 else 0.1)       # left half of the desktop is light
    send_glass(page, luminance=0.1, lum_grid=light_left)          # the whole desktop averages dark
    assert page.evaluate("document.body.classList.contains('light')")
    page.evaluate("window.__sx = 2600")
    page.wait_for_timeout(150)
    assert page.evaluate("document.body.classList.contains('dark')")


def test_desktop_widget_card_fills_the_window(browser):   # final review #3
    page = open_page(browser)
    page.evaluate("document.body.classList.add('mode-desktop')")
    send_glass(page)
    r = page.evaluate("(() => { const r = document.getElementById('g').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })()")
    assert r == [0, 0, 360, 560]
