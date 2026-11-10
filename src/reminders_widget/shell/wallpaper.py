"""The desktop wallpaper as Windows draws it, and the frosted glass made from it.

Measured on 2026-11-10 against PaintDesktop (probes/probe_glass_align.py): in "fill"
mode Windows centres a horizontal overflow but cuts only a third of a vertical
overflow from the top. Paths are never logged.
"""
import ctypes
import logging
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageStat

log = logging.getLogger("reminders_widget.wallpaper")
MODES = {"0": "center", "2": "stretch", "6": "fit", "10": "fill", "22": "span"}
FILL_TOP_SHARE = 1 / 3       # share of a vertical overflow cut from the top in fill/span
BLUR_CSS_PX = 24
SATURATION = 1.7
GRID_CELL = 32               # CSS px per luminance cell sent to the page

Rect = tuple[int, int, int, int]


# --- pure layout ---------------------------------------------------------------
def mode_name(style, tile) -> str:
    if str(tile) == "1":
        return "tile"
    if style is None:
        return "fill"
    return MODES.get(str(style), "fill")


def parse_background_color(text) -> tuple[int, int, int]:
    try:
        r, g, b = (max(0, min(255, int(p))) for p in str(text).split())
        return r, g, b
    except (ValueError, TypeError):
        return 0, 0, 0


def image_dest_rect(img_w: int, img_h: int, ref: Rect, mode: str) -> tuple[float, float, float, float]:
    """Where the scaled wallpaper lands on `ref` (a monitor; the virtual screen for span)."""
    rl, rt, rr, rb = ref
    rw, rh = rr - rl, rb - rt
    if mode == "stretch":
        w, h = rw, rh
    elif mode in ("fill", "span"):
        k = max(rw / img_w, rh / img_h)
        w, h = img_w * k, img_h * k
    elif mode == "fit":
        k = min(rw / img_w, rh / img_h)
        w, h = img_w * k, img_h * k
    elif mode in ("center", "tile"):
        w, h = float(img_w), float(img_h)
    else:
        raise ValueError(f"unknown wallpaper mode: {mode}")
    left = rl + (rw - w) / 2
    top = rt + (rh - h) / 2
    if mode in ("fill", "span") and h > rh:
        top = rt - (h - rh) * FILL_TOP_SHARE
    if mode == "tile":
        left, top = float(rl), float(rt)
    return left, top, left + w, top + h


def compose(img: Image.Image, monitors: list[Rect], virtual: Rect, mode: str,
            bg: tuple[int, int, int]) -> Image.Image:
    """The desktop over the whole virtual screen, physical px."""
    vl, vt, vr, vb = virtual
    canvas = Image.new("RGB", (vr - vl, vb - vt), bg)
    img = img.convert("RGB")
    for ml, mt, mr, mb in ([virtual] if mode == "span" else list(monitors)):
        tile = Image.new("RGB", (mr - ml, mb - mt), bg)
        dl, dt, dr, db = image_dest_rect(img.width, img.height, (ml, mt, mr, mb), mode)
        if mode == "tile":
            for x in range(0, mr - ml, img.width):
                for y in range(0, mb - mt, img.height):
                    tile.paste(img, (x, y))
        else:
            size = (max(1, round(dr - dl)), max(1, round(db - dt)))
            scaled = img if size == img.size else img.resize(size, Image.LANCZOS)
            tile.paste(scaled, (round(dl - ml), round(dt - mt)))
        canvas.paste(tile, (ml - vl, mt - vt))
    return canvas


def css_size(phys: tuple[int, int], scale: float) -> tuple[int, int]:
    return max(1, round(phys[0] / scale)), max(1, round(phys[1] / scale))


def origin_css(virtual: Rect, scale: float) -> tuple[float, float]:
    return virtual[0] / scale, virtual[1] / scale


def luminance(img: Image.Image) -> float:
    """Mean relative luminance 0..1 (sRGB approximation on a small thumbnail)."""
    small = img.convert("RGB").resize((32, 32))
    r, g, b = ImageStat.Stat(small).mean
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def luminance_grid(img: Image.Image, cell: int = GRID_CELL) -> dict:
    """Mean luminance per `cell`×`cell` block, row-major, so the page can read the area under it."""
    cols, rows = max(1, -(-img.width // cell)), max(1, -(-img.height // cell))
    small = img.convert("RGB").resize((cols, rows), Image.BOX)
    px = small.tobytes()
    values = [round((0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2]) / 255, 3)
              for i in range(0, len(px), 3)]
    return {"cell": cell, "cols": cols, "rows": rows, "values": values}


def build_glass_image(composite: Image.Image, scale: float) -> Image.Image:
    """CSS-px copy of the composite, blurred and saturated once."""
    css = composite.resize(css_size(composite.size, scale), Image.LANCZOS)
    return ImageEnhance.Color(css.filter(ImageFilter.GaussianBlur(BLUR_CSS_PX))).enhance(SATURATION)


def measure_offset(ours: Image.Image, real: Image.Image, *, search: int = 160, step: int = 8,
                   window: int = 300) -> tuple[int, int, float]:
    """(dx, dy, err): how far `ours` must move to match `real` (dy > 0: move down)."""
    def score(a, b):
        return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]

    a_full, b_full = ours.convert("L"), real.convert("L")
    k = max(1, step)
    a = a_full.resize((max(1, a_full.width // k), max(1, a_full.height // k))) if k > 1 else a_full
    b = b_full.resize((max(1, b_full.width // k), max(1, b_full.height // k))) if k > 1 else b_full
    r = max(1, search // k)
    cw, ch = b.width - 2 * r, b.height - 2 * r
    core = b.crop((r, r, r + cw, r + ch))
    coarse = min(((dx * k, dy * k, score(a.crop((r - dx, r - dy, r - dx + cw, r - dy + ch)), core))
                  for dy in range(-r, r + 1) for dx in range(-r, r + 1)), key=lambda t: t[2])
    cx, cy = a_full.width // 2, a_full.height // 2
    win = min(window, cx - search - k, cy - search - k)
    ref = b_full.crop((cx - win, cy - win, cx + win, cy + win))
    return min(((dx, dy, score(a_full.crop((cx - win - dx, cy - win - dy, cx + win - dx, cy + win - dy)), ref))
                for dy in range(coarse[1] - k, coarse[1] + k + 1)
                for dx in range(coarse[0] - k, coarse[0] + k + 1)), key=lambda t: t[2])


# --- Windows ---------------------------------------------------------------------
class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


@dataclass(frozen=True)
class Monitor:
    rect: Rect
    scale: float


def monitors() -> list[Monitor]:
    user32, shcore = ctypes.windll.user32, ctypes.WinDLL("shcore")
    out: list[Monitor] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(RECT), wintypes.LPARAM)

    def visit(mon, _hdc, rect, _data):
        r, dx, dy = rect.contents, wintypes.UINT(96), wintypes.UINT(96)
        shcore.GetDpiForMonitor(mon, 0, ctypes.byref(dx), ctypes.byref(dy))
        out.append(Monitor((r.left, r.top, r.right, r.bottom), dx.value / 96))
        return True

    user32.EnumDisplayMonitors(None, None, proc(visit), 0)
    return out


def virtual_rect() -> Rect:
    u = ctypes.windll.user32
    x, y = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
    return x, y, x + u.GetSystemMetrics(78), y + u.GetSystemMetrics(79)


def read_settings() -> tuple[Path | None, str, tuple[int, int, int]]:
    """(wallpaper file or None, fit mode, background rgb). The path is never logged."""
    import winreg
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.user32.SystemParametersInfoW(0x73, 260, buf, 0)
    path = Path(buf.value) if buf.value and os.path.exists(buf.value) else None
    if path is None:
        transcoded = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Themes/TranscodedWallpaper"
        path = transcoded if transcoded.exists() else None
    style = tile = bg = None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as k:
            style = winreg.QueryValueEx(k, "WallpaperStyle")[0]
            tile = winreg.QueryValueEx(k, "TileWallpaper")[0]
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Colors") as k:
            bg = winreg.QueryValueEx(k, "Background")[0]
    except OSError:
        pass
    return path, mode_name(style, tile), parse_background_color(bg)


@dataclass(frozen=True)
class Glass:
    image: Image.Image | None          # CSS-px, frosted; None for a solid-colour desktop
    origin_css: tuple[float, float]
    luminance: float
    background: tuple[int, int, int]
    retry: bool = False                # the file was there but unreadable (half-written); try again


def build_glass(scale: float) -> Glass:
    """Compose the current desktop and frost it once."""
    path, mode, bg = read_settings()
    virt = virtual_rect()
    if path is None:
        return Glass(None, origin_css(virt, scale), luminance(Image.new("RGB", (1, 1), bg)), bg)
    try:
        with Image.open(path) as img:
            comp = compose(img, [m.rect for m in monitors()], virt, mode, bg)
    except Exception as exc:  # unreadable or changing wallpaper file
        log.info("wallpaper unreadable: %s", type(exc).__name__)
        return Glass(None, origin_css(virt, scale), luminance(Image.new("RGB", (1, 1), bg)), bg, retry=True)
    log.info("glass built: mode %s, %d monitors", mode, len(monitors()))
    return Glass(build_glass_image(comp, scale), origin_css(virt, scale), luminance(comp), bg)


def glass_payload(glass: Glass) -> dict:
    """What the page needs: a JPEG data URL made once, its CSS size, origin and luminance."""
    import base64
    import io
    url, width, height, grid = None, 0, 0, None
    if glass.image is not None:
        grid = luminance_grid(glass.image)
        buf = io.BytesIO()
        glass.image.save(buf, "JPEG", quality=85)
        url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        width, height = glass.image.size
    return {"url": url, "width": width, "height": height, "origin": list(glass.origin_css),
            "luminance": round(glass.luminance, 3), "background": "#%02x%02x%02x" % glass.background,
            "grid": grid, "retry": glass.retry}


def desktop_signature() -> tuple:
    """Changes whenever the drawn desktop could change: file, its mtime, mode, monitors."""
    path, mode, bg = read_settings()
    try:
        mtime = path.stat().st_mtime if path else None
    except OSError:
        mtime = None
    return (str(path) if path else None, mtime, mode, bg, tuple(m.rect for m in monitors()),
            tuple(m.scale for m in monitors()))


class GlassWatcher:
    """Rebuilds the glass at start and whenever the desktop signature changes (polled).

    A build that raised or asked for a retry (wallpaper file mid-write) runs again next poll.
    """

    def __init__(self, on_change, *, signature=desktop_signature, build=None, interval: float = 5.0):
        import threading
        self._on_change, self._signature, self._build = on_change, signature, build
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="rw-glass", daemon=True)

    def _loop(self) -> None:
        last = object()
        while not self._stop.is_set():
            try:
                sig = self._signature()
                if sig != last:
                    result = self._build()              # raising leaves `last`: retried next poll
                    self._on_change(result)
                    if not (isinstance(result, dict) and result.get("retry")):
                        last = sig
            except Exception as exc:  # a half-written wallpaper file, a monitor going away
                log.info("glass rebuild failed: %s", type(exc).__name__)
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2)
