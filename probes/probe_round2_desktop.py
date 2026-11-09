"""Throwaway probe, round 2, parts C and D: glass from the wallpaper, and surviving Win+D.

Run it yourself:  .\\.venv\\Scripts\\python.exe probes\\probe_round2_desktop.py [--part C|D]

- Part C: opens an opaque frameless window at a fixed spot (80,80), crops the wallpaper
  behind it, blurs it as "glass" and asks whether it looks aligned with the wallpaper.
- Part D: parents the same window into the desktop (Progman/WorkerW) and asks you to
  press Win+D.
- No iCloud. The wallpaper path is never logged, only which source worked.
Results are appended to probe-results.txt.
"""
import argparse
import base64
import ctypes
import io
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from probe import RESULTS_FILE
from probe_glass import FONT_FILE, find_hwnd, font_file_uri, hresult_text, load_win32
from probe_webview_login import AppendResults

HERE = Path(__file__).parent
PAGE = HERE / "glass_wallpaper.html"
WINDOW_TITLE = "Glass wallpaper probe"
WIN_X, WIN_Y, WIN_W, WIN_H = 80, 80, 360, 460   # CSS px
MARGIN_CSS = 40                                  # must match --margin in the html
POLL_SECONDS = 0.3
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2

MODES = {"0": "center", "2": "stretch", "6": "fit", "10": "fill", "22": "span"}


# --- Pure maths (unit-tested) ---

def mode_name(style, tile):
    """Fit mode name from HKCU\\Control Panel\\Desktop WallpaperStyle/TileWallpaper."""
    if str(tile) == "1":
        return "tile"
    if style is None:
        return "fill"
    return MODES.get(str(style), f"neznámy({style})")


def parse_background_color(text):
    """'r g b' from HKCU\\Control Panel\\Colors\\Background; black on anything odd."""
    try:
        r, g, b = (max(0, min(255, int(p))) for p in str(text).split())
        return r, g, b
    except (ValueError, TypeError):
        return 0, 0, 0


def bg_hex(rgb):
    return "#%02x%02x%02x" % rgb


def crop_rect(img_w, img_h, mon_rect, virtual_rect, win_rect, mode):
    """Rectangle (sx, sy, sw, sh) in image pixels that lies behind win_rect.

    Rects are (left, top, right, bottom) in physical screen pixels. The wallpaper is
    laid out on the monitor (on the virtual screen for span) according to mode; the
    result may reach outside the image (fit/center bars) - the caller paints the
    desktop background colour there.
    """
    if mode not in ("fill", "fit", "stretch", "center", "span", "tile"):
        raise ValueError(f"unknown wallpaper mode: {mode}")
    rl, rt, rr, rb = virtual_rect if mode == "span" else mon_rect
    rw, rh = rr - rl, rb - rt
    wl, wt, wr, wb = win_rect
    ww, wh = wr - wl, wb - wt
    if mode == "tile":
        return float(wl - rl), float(wt - rt), float(ww), float(wh)
    if mode == "stretch":
        kx, ky = rw / img_w, rh / img_h
    elif mode in ("fill", "span"):
        kx = ky = max(rw / img_w, rh / img_h)
    elif mode == "fit":
        kx = ky = min(rw / img_w, rh / img_h)
    else:  # center
        kx = ky = 1.0
    ox = (rw - img_w * kx) / 2
    oy = (rh - img_h * ky) / 2
    return ((wl - rl - ox) / kx, (wt - rt - oy) / ky, ww / kx, wh / ky)


def jpeg_data_url(data):
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def compose_crop(img, rect, out_size, bg_rgb, mode):
    """Render image region rect=(sx,sy,sw,sh) at out_size; outside the image is bg_rgb."""
    from PIL import Image

    sx, sy, sw, sh = rect
    ow, oh = out_size
    if mode == "tile":
        iw, ih = img.size
        sx, sy = sx % iw, sy % ih
        nx, ny = int((sx + sw) // iw) + 1, int((sy + sh) // ih) + 1
        big = Image.new("RGB", (iw * nx, ih * ny))
        for i in range(nx):
            for j in range(ny):
                big.paste(img, (i * iw, j * ih))
        img = big
    iw, ih = img.size
    canvas = Image.new("RGB", (ow, oh), bg_rgb)
    kx, ky = ow / sw, oh / sh
    x0, y0 = max(sx, 0.0), max(sy, 0.0)
    x1, y1 = min(sx + sw, float(iw)), min(sy + sh, float(ih))
    if x1 > x0 and y1 > y0:
        piece = img.convert("RGB").resize(
            (max(1, round((x1 - x0) * kx)), max(1, round((y1 - y0) * ky))),
            Image.LANCZOS, box=(x0, y0, x1, y1))
        canvas.paste(piece, (round((x0 - sx) * kx), round((y0 - sy) * ky)))
    return canvas


def page_url(html_path, font_uri=None):
    from urllib.parse import quote
    url = Path(html_path).as_uri()
    return url + ("#font=" + quote(font_uri, safe="") if font_uri else "")


# --- Win32 bindings ---

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT),
                ("dwFlags", wintypes.DWORD)]


def make_dpi_aware():
    """Per-monitor v2, before any window exists. Returns (ok, last error text)."""
    fn = ctypes.windll.user32.SetProcessDpiAwarenessContext
    fn.argtypes = [ctypes.c_ssize_t]
    fn.restype = wintypes.BOOL
    ok = bool(fn(-4))
    return ok, ("" if ok else f"GetLastError {ctypes.GetLastError()}")


def bind_user32():
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    u.GetWindowRect.restype = wintypes.BOOL
    u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.MonitorFromWindow.restype = wintypes.HANDLE
    u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    u.GetMonitorInfoW.restype = wintypes.BOOL
    u.GetSystemMetrics.argtypes = [ctypes.c_int]
    u.GetSystemMetrics.restype = ctypes.c_int
    u.GetDpiForWindow.argtypes = [wintypes.HWND]
    u.GetDpiForWindow.restype = wintypes.UINT
    u.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
    u.SystemParametersInfoW.restype = wintypes.BOOL
    u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    u.FindWindowW.restype = wintypes.HWND
    u.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    u.FindWindowExW.restype = wintypes.HWND
    u.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                      wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    u.SendMessageTimeoutW.restype = ctypes.c_size_t
    u.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    u.SetParent.restype = wintypes.HWND
    u.GetParent.argtypes = [wintypes.HWND]
    u.GetParent.restype = wintypes.HWND
    u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u.GetAncestor.restype = wintypes.HWND
    u.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    u.ScreenToClient.restype = wintypes.BOOL
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u.SetWindowPos.restype = wintypes.BOOL
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetClassNameW.restype = ctypes.c_int
    u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    u.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    u.EnumWindows.restype = wintypes.BOOL
    u.enum_proc_type = enum_proc
    return u


def window_rect(u, hwnd):
    r = RECT()
    if not u.GetWindowRect(hwnd, ctypes.byref(r)):
        raise OSError("GetWindowRect failed")
    return r.left, r.top, r.right, r.bottom


def monitor_rect(u, hwnd):
    mon = u.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not u.GetMonitorInfoW(mon, ctypes.byref(info)):
        raise OSError("GetMonitorInfoW failed")
    m = info.rcMonitor
    return m.left, m.top, m.right, m.bottom


def virtual_rect(u):
    x, y = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
    return x, y, x + u.GetSystemMetrics(78), y + u.GetSystemMetrics(79)


def read_wallpaper_path(u):
    """Returns (path or None, source label). The path itself is never logged."""
    buf = ctypes.create_unicode_buffer(260)
    u.SystemParametersInfoW(0x73, 260, buf, 0)
    if buf.value and os.path.exists(buf.value):
        return buf.value, "SystemParametersInfoW"
    transcoded = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Themes/TranscodedWallpaper"
    if transcoded.exists():
        return str(transcoded), "TranscodedWallpaper"
    return None, "nenájdená"


def read_wallpaper_registry():
    """(mode name, background rgb) from the registry."""
    import winreg
    style = tile = None
    bg = None
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
    return mode_name(style, tile), parse_background_color(bg)


# --- Desktop parenting (part D) ---

def class_name(u, hwnd):
    buf = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(hwnd, buf, 256)
    return buf.value


def find_workerw(u):
    """Returns (workerw hwnd or None, layout label, progman, raw info). Handles both layouts."""
    progman = u.FindWindowW("Progman", None)
    res = ctypes.c_size_t(0)
    ret = u.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 1000, ctypes.byref(res))
    found = {}

    def visit(hwnd, _):
        if u.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
            found["holder"] = hwnd
            return False
        return True

    u.EnumWindows(u.enum_proc_type(visit), 0)
    holder = found.get("holder")
    found_info = {"sendmessage": ret, "progman": progman, "holder": holder}
    if holder is None:
        return None, "SHELLDLL_DefView nenájdené", progman, found_info
    if holder == progman:
        workerw = u.FindWindowExW(progman, None, "WorkerW", None)
        return workerw or None, "24H2: SHELLDLL_DefView pod Progman, WorkerW je dieťa Progman", \
            progman, found_info
    workerw = u.FindWindowExW(None, holder, "WorkerW", None)
    return workerw or None, "klasické: WorkerW za oknom so SHELLDLL_DefView", progman, found_info


# --- Probe steps ---

def safe_error(exc):
    """Exception text safe for the results file: type name only, never the message
    (Pillow and OS errors embed the wallpaper path, which contains the user name)."""
    return f"{type(exc).__name__} (podrobnosti sa zámerne nelogujú)"


def evaluate(window, js):
    try:
        return window.evaluate_js(js)
    except Exception as exc:
        return f"JS chyba {type(exc).__name__}"


class WallpaperCropper:
    def __init__(self, u, hwnd, wallpaper_path, mode, bg_rgb):
        from PIL import Image
        self.u, self.hwnd, self.mode, self.bg = u, hwnd, mode, bg_rgb
        self.img = Image.open(wallpaper_path)
        self.img.load()
        self.img = self.img.convert("RGB")

    @property
    def scale(self):
        """Read on every use so dragging to a monitor with another DPI stays aligned."""
        return (self.u.GetDpiForWindow(self.hwnd) or 96) / 96

    def data_url(self, win_rect):
        scale = self.scale
        m = round(MARGIN_CSS * scale)
        l, t, r, b = win_rect
        rect = crop_rect(self.img.width, self.img.height, monitor_rect(self.u, self.hwnd),
                         virtual_rect(self.u), (l - m, t - m, r + m, b + m), self.mode)
        out = (round((r - l + 2 * m) / scale), round((b - t + 2 * m) / scale))
        buf = io.BytesIO()
        compose_crop(self.img, rect, out, self.bg, self.mode).save(buf, "JPEG", quality=88)
        return jpeg_data_url(buf.getvalue())


def apply_crop(window, cropper, rect):
    js = "window.setWallpaper(%r, %r)" % (cropper.data_url(rect), bg_hex(cropper.bg))
    return evaluate(window, js)


def start_recrop_poll(window, cropper, hwnd, u, results, stop):
    """Poll the window rect; when it moves, crop again and log it (once per move burst)."""
    state = {"rect": window_rect(u, hwnd), "logged": 0}

    def loop():
        while not stop.wait(POLL_SECONDS):
            try:
                rect = window_rect(u, hwnd)
                if rect != state["rect"]:
                    state["rect"] = rect
                    n = apply_crop(window, cropper, rect)
                    if state["logged"] < 3:
                        state["logged"] += 1
                        results.add("nové orezanie po presune okna", "INFO",
                                    f"okno {rect[0]},{rect[1]}; setWallpaper volaní: {n}")
            except Exception as exc:
                results.add("nové orezanie po presune okna", "FAIL",
                            safe_error(exc))
                return

    threading.Thread(target=loop, daemon=True).start()


def part_c(window, hwnd, u, dwm, results, stop, ask=True):
    hr = dwm.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
                                   ctypes.byref(ctypes.c_int(DWMWCP_ROUND)), 4)
    results.add("DwmSetWindowAttribute 33=2 (oblé rohy)", "OK" if hr == 0 else "FAIL",
                f"HRESULT {hresult_text(hr)}")
    path, source = read_wallpaper_path(u)
    results.add("tapeta: zdroj", "OK" if path else "FAIL", source)
    if source == "TranscodedWallpaper":
        results.add("tapeta: upozornenie", "INFO",
                    "súbor je už prekódovaný, zarovnanie sa môže líšiť od pôvodného obrázka")
    mode, bg = read_wallpaper_registry()
    results.add("tapeta: režim prispôsobenia", "INFO", mode)
    if not path:
        results.add("sklo z tapety", "FAIL", "bez obrázka tapety nie je z čoho orezať")
        return False
    cropper = WallpaperCropper(u, hwnd, path, mode, bg)
    results.add("DPI mierka okna", "INFO", f"{cropper.scale * 100:.0f} %")
    results.add("rozmer tapety", "INFO", f"{cropper.img.width}x{cropper.img.height}")
    rect = window_rect(u, hwnd)
    results.add("okno, fyzické px", "INFO", f"{rect[0]},{rect[1]} {rect[2]-rect[0]}x{rect[3]-rect[1]}")
    results.add("stránka: innerWidth x innerHeight @ devicePixelRatio", "INFO", str(evaluate(
        window, "innerWidth + 'x' + innerHeight + ' @ ' + devicePixelRatio")))
    n = apply_crop(window, cropper, rect)
    results.add("setWallpaper do stránky", "OK" if n == 1 else "FAIL", f"návrat {n}")
    if ask:
        start_recrop_poll(window, cropper, hwnd, u, results, stop)
    time.sleep(1.5)
    if not ask:  # part D alone: the glass is only there so the widget has something to show
        return True
    results.confirm("sklo z tapety: vyzerá ako sklo nad tvojou tapetou",
                    "Vyzerá okno ako sklo nad tvojou tapetou (rozmazaný obraz, jemný lesk)?")
    results.confirm("sklo z tapety: zarovnané bez posunu",
                    "Je obraz v skle zarovnaný s tapetou okolo okna (bez posunu a bez zväčšenia)?")
    results.confirm("sklo z tapety: rohy oblé a čisté",
                    "Sú rohy okna oblé a čisté (bez čierneho či bieleho rohu)?")
    results.confirm("sklo z tapety: presun ťahaním",
                    "Presuň okno ťahaním (chytiť kdekoľvek). Ide obraz v skle s oknom a zostáva "
                    "zarovnaný s tapetou?")
    return True


def run_part_c_safely(window, hwnd, u, dwm, results, stop, ask=True):
    try:
        return part_c(window, hwnd, u, dwm, results, stop, ask=ask)
    except Exception as exc:
        results.add("sklo z tapety", "FAIL", safe_error(exc))
        return False


def part_d(window, hwnd, u, results):
    workerw, layout, progman, info = find_workerw(u)
    results.add("Progman", "INFO", f"hwnd {info['progman']}")
    results.add("SendMessageTimeoutW 0x052C", "INFO", f"návrat {info['sendmessage']}")
    results.add("okno so SHELLDLL_DefView", "INFO", f"hwnd {info['holder']}")
    results.add("rozloženie plochy", "OK" if workerw else "FAIL", layout)
    if not workerw:
        return
    results.add("WorkerW", "INFO", f"hwnd {workerw}")
    rect = window_rect(u, hwnd)
    old_parent = u.GetAncestor(hwnd, 1)  # GA_PARENT; GetParent would return an owner
    ctypes.set_last_error(0)
    prev = u.SetParent(hwnd, workerw)
    err = ctypes.get_last_error()
    now_parent = u.GetAncestor(hwnd, 1)
    results.add("SetParent(widget, WorkerW)", "OK" if now_parent == workerw else "FAIL",
                f"vrátil {prev}, GetLastError {err}, rodič pred {old_parent}, teraz {now_parent}")
    style = u.GetWindowLongPtrW(hwnd, -16)
    new_style = (style & ~0x80000000) | 0x40000000  # WS_POPUP -> WS_CHILD
    u.SetWindowLongPtrW(hwnd, -16, new_style)
    results.add("štýl okna POPUP -> CHILD", "INFO",
                f"0x{style & 0xFFFFFFFF:08X} -> 0x{u.GetWindowLongPtrW(hwnd, -16) & 0xFFFFFFFF:08X}")
    # Child coordinates are relative to the parent: convert the old screen position.
    pt = wintypes.POINT(rect[0], rect[1])
    ok_pt = u.ScreenToClient(workerw, ctypes.byref(pt))
    results.add("ScreenToClient(WorkerW)", "OK" if ok_pt else "FAIL", f"{pt.x},{pt.y}")
    ok_pos = u.SetWindowPos(hwnd, None, pt.x, pt.y, 0, 0, 0x0001 | 0x0004 | 0x0010 | 0x0020)  # NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED
    results.add("SetWindowPos (pôvodné miesto)", "OK" if ok_pos else "FAIL", f"návrat {int(bool(ok_pos))}")
    now = window_rect(u, hwnd)
    results.add("okno po SetParent, obrazovkové súradnice", "INFO",
                f"{now[0]},{now[1]} (pred: {rect[0]},{rect[1]})")
    time.sleep(1)
    results.confirm("po SetParent widget viditeľný",
                    "Je widget stále viditeľný na svojom mieste?")
    results.confirm("Win+D: widget viditeľný",
                    "Stlač Win+D. Je widget po Win+D stále viditeľný na ploche?")
    results.confirm("klikateľný",
                    "Dá sa na widget kliknúť (reaguje na klik)?")
    results.confirm("ťahateľný",
                    "Dá sa widget potiahnuť myšou?")
    results.confirm("zostáva pod ostatnými oknami",
                    "Otvor iné okno (napr. Prieskumník) cez widget a klikni naň. Je widget pod ním?")


def run_probe(window, results, parts):
    stop = threading.Event()
    try:
        dwm, _ = load_win32()
        u = bind_user32()
        window.events.loaded.wait(20)
        time.sleep(1)
        hwnd, how = find_hwnd(window, u)
        results.add("HWND okna", "OK" if hwnd else "FAIL", f"získaný cez {how}")
        if not hwnd:
            return
        run_part_c_safely(window, hwnd, u, dwm, results, stop, ask="C" in parts)
        if "D" in parts:
            part_d(window, hwnd, u, results)
    except Exception as exc:  # the probe records what breaks, never hangs the window
        results.add("beh sondy kola 2 (C, D)", "FAIL", safe_error(exc))
    finally:
        stop.set()
        window.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", choices=["C", "D"], help="run just one part (default both)")
    args = parser.parse_args()
    parts = {args.part} if args.part else {"C", "D"}

    sys.stdout.reconfigure(encoding="utf-8")
    results = AppendResults()
    results.add("--- kolo 2: sklo z tapety, Win+D ---", "INFO")
    ok, detail = make_dpi_aware()
    results.add("SetProcessDpiAwarenessContext(-4)", "OK" if ok else "INFO",
                detail or "per-monitor v2")
    import webview

    font_uri = font_file_uri(FONT_FILE) if FONT_FILE.exists() else None
    window = webview.create_window(WINDOW_TITLE, page_url(PAGE, font_uri), width=WIN_W, height=WIN_H,
                                   x=WIN_X, y=WIN_Y, frameless=True, easy_drag=True, shadow=False)
    webview.start(run_probe, (window, results, parts))
    print(f"\nHotovo. Výsledky: {RESULTS_FILE.name}")


if __name__ == "__main__":
    main()
