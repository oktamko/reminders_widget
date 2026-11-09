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
import json
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

from probe import RESULTS_FILE
from probe_glass import (FONT_FILE, find_hwnd, font_file_uri, hresult_text, load_win32,
                         send_to_bottom)
from probe_webview_login import AppendResults

HERE = Path(__file__).parent
PAGE = HERE / "glass_wallpaper.html"
WINDOW_TITLE = "Glass wallpaper probe"
WIN_X, WIN_Y, WIN_W, WIN_H = 80, 80, 360, 460   # CSS px
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


def jpeg_data_url(data):
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def image_dest_rect(img_w, img_h, ref_rect, mode):
    """Where the scaled wallpaper lands, (l, t, r, b) in physical screen px, when laid out
    on ref_rect (a monitor; the virtual screen for span). May extend past ref_rect."""
    rl, rt, rr, rb = ref_rect
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
    if mode == "tile":
        left, top = float(rl), float(rt)
    return left, top, left + w, top + h


def build_composite(img, monitors, virtual, mode, bg_rgb):
    """The desktop as Windows draws it over the whole virtual screen, in physical px."""
    from PIL import Image

    vl, vt, vr, vb = virtual
    canvas = Image.new("RGB", (vr - vl, vb - vt), bg_rgb)
    img = img.convert("RGB")
    areas = [virtual] if mode == "span" else list(monitors)
    for ml, mt, mr, mb in areas:
        tile = Image.new("RGB", (mr - ml, mb - mt), bg_rgb)
        dl, dt, dr, db = image_dest_rect(img.width, img.height, (ml, mt, mr, mb), mode)
        if mode == "tile":
            for x in range(0, mr - ml, img.width):
                for y in range(0, mb - mt, img.height):
                    tile.paste(img, (x, y))
        else:
            size = (max(1, round(dr - dl)), max(1, round(db - dt)))
            scaled = img if size == img.size else img.resize(size, Image.LANCZOS)
            tile.paste(scaled, (round(dl - ml), round(dt - mt)))  # negative offsets clip
        canvas.paste(tile, (ml - vl, mt - vt))
    return canvas


def css_size(phys_size, scale):
    return max(1, round(phys_size[0] / scale)), max(1, round(phys_size[1] / scale))


def origin_css(virtual, scale):
    return virtual[0] / scale, virtual[1] / scale


def translate_offset(origin, screen_xy, corr=(0.0, 0.0)):
    """CSS translate that keeps the image fixed on the screen while the window moves:
    the layer sits at the window's top-left, so shift it by (image origin - window origin)."""
    return origin[0] - (screen_xy[0] + corr[0]), origin[1] - (screen_xy[1] + corr[1])


def frost(img_css, radius=24, saturation=1.7):
    """Blur once and boost saturation (what CSS blur()+saturate() did per frame)."""
    from PIL import ImageEnhance, ImageFilter
    return ImageEnhance.Color(img_css.filter(ImageFilter.GaussianBlur(radius))).enhance(saturation)


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


def enum_monitors(u):
    """[(rect, dpi)] for every monitor, physical px."""
    out = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                              ctypes.POINTER(RECT), wintypes.LPARAM)
    u.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, proc, wintypes.LPARAM]
    u.EnumDisplayMonitors.restype = wintypes.BOOL
    shcore = ctypes.WinDLL("shcore")
    shcore.GetDpiForMonitor.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.POINTER(wintypes.UINT),
                                        ctypes.POINTER(wintypes.UINT)]

    def visit(mon, hdc, rect, data):
        r = rect.contents
        dx, dy = wintypes.UINT(96), wintypes.UINT(96)
        shcore.GetDpiForMonitor(mon, 0, ctypes.byref(dx), ctypes.byref(dy))
        out.append(((r.left, r.top, r.right, r.bottom), dx.value))
        return True

    u.EnumDisplayMonitors(None, None, proc(visit), 0)
    return out


def send_glass(window, img_css, origin, corr, blurred):
    buf = io.BytesIO()
    img_css.save(buf, "JPEG", quality=85)
    js = "window.setGlass(%r, %s, %s, %s, %s, %s, %s)" % (
        jpeg_data_url(buf.getvalue()), json.dumps(origin[0]), json.dumps(origin[1]),
        img_css.width, img_css.height, json.dumps(corr[0]), json.dumps(corr[1]))
    return evaluate(window, js)


def part_c(window, hwnd, u, dwm, results, stop, ask=True):
    from PIL import Image

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
        results.add("sklo z tapety", "FAIL", "bez obrázka tapety nie je z čoho skladať")
        return False
    img = Image.open(path)
    img.load()
    results.add("rozmer tapety", "INFO", f"{img.width}x{img.height}")
    monitors = enum_monitors(u)
    virtual = virtual_rect(u)
    scale = (u.GetDpiForWindow(hwnd) or 96) / 96
    results.add("monitory", "INFO", f"{len(monitors)}, DPI mierky: "
                + ", ".join(f"{dpi * 100 // 96} %" for _, dpi in monitors))
    if len({dpi for _, dpi in monitors}) > 1:
        results.add("poznámka k DPI", "INFO",
                    "monitory majú rôznu mierku; použitá mierka monitora s widgetom")
    results.add("DPI mierka okna", "INFO", f"{scale * 100:.0f} %")
    t0 = time.time()
    composite = build_composite(img, [r for r, _ in monitors], virtual, mode, bg)
    css = composite.resize(css_size(composite.size, scale), Image.LANCZOS)
    frosted = frost(css)
    results.add("zloženie plochy, rozmazanie (raz)", "OK",
                f"{css.width}x{css.height} CSS px, {time.time() - t0:.1f} s")
    origin = origin_css(virtual, scale)
    # window.screenX/Y in CSS px vs the Win32 rect: measured once at rest, the difference
    # (frame/offset, if any) is applied as a constant correction in the page.
    rect = window_rect(u, hwnd)
    send_glass(window, css, origin, (0.0, 0.0), False)
    time.sleep(0.5)
    sample = evaluate(window, "JSON.stringify(window.__sample())")
    try:
        sx, sy = json.loads(sample)["sx"], json.loads(sample)["sy"]
    except (TypeError, ValueError, KeyError):
        sx, sy = rect[0] / scale, rect[1] / scale
    corr = (rect[0] / scale - sx, rect[1] / scale - sy)
    results.add("window.screenX/Y a okno z Win32", "INFO",
                f"JS {sx:.1f},{sy:.1f} CSS; Win32 {rect[0]},{rect[1]} px = "
                f"{rect[0] / scale:.1f},{rect[1] / scale:.1f} CSS; korekcia {corr[0]:.1f},{corr[1]:.1f}")
    results.add("vzorka stránky", "INFO", str(sample))
    results.add("počiatok virtuálnej plochy", "INFO", f"{origin[0]:.1f},{origin[1]:.1f} CSS px")
    if not ask:  # part D alone: the glass is only there so the widget has something to show
        send_glass(window, frosted, origin, corr, True)
        return True
    send_glass(window, css, origin, corr, False)
    print("\nNajprv 8 s NEROZMAZANÁ tapeta v okne, porovnaj hrany s tapetou okolo okna...", flush=True)
    time.sleep(8)
    results.confirm("zarovnanie: nerozmazaná tapeta nadväzuje",
                    "Nerozmazaná tapeta v okne presne nadväzuje na tapetu okolo?")
    send_glass(window, frosted, origin, corr, True)
    time.sleep(1)
    results.confirm("sklo z tapety: vyzerá ako sklo nad tvojou tapetou",
                    "Vyzerá okno ako sklo nad tvojou tapetou (rozmazaný obraz, jemný lesk)?")
    results.confirm("sklo z tapety: rohy oblé a čisté",
                    "Sú rohy okna oblé a čisté (bez čierneho či bieleho rohu)?")
    results.confirm("sklo z tapety: presun ťahaním",
                    "Presuň okno ťahaním (chytiť kdekoľvek). Zostáva obraz v skle zarovnaný "
                    "s tapetou okolo?")
    results.confirm("sklo z tapety: plynulosť ťahania",
                    "Pri ťahaní ide tapeta plynulo, bez trhania?")
    return True


def run_part_c_safely(window, hwnd, u, dwm, results, stop, ask=True):
    try:
        return part_c(window, hwnd, u, dwm, results, stop, ask=ask)
    except Exception as exc:
        results.add("sklo z tapety", "FAIL", safe_error(exc))
        return False


def child_classes(u, parent):
    """Class names of the direct children, top of the z-order first (names only)."""
    names, child = [], None
    for _ in range(50):
        child = u.FindWindowExW(parent, child, None, None)
        if not child:
            break
        names.append(class_name(u, child) or "?")
    return names


def d_confirms(results, tag):
    results.confirm(f"{tag}: widget viditeľný", "Je widget viditeľný na svojom mieste?")
    results.confirm(f"{tag}: Win+D viditeľný",
                    "Stlač Win+D. Je widget po Win+D stále viditeľný na ploche?")
    results.confirm(f"{tag}: klikateľný", "Dá sa na widget kliknúť (reaguje na klik)?")
    results.confirm(f"{tag}: ťahateľný", "Dá sa widget potiahnuť myšou?")
    results.confirm(f"{tag}: pod oknami",
                    "Otvor iné okno (napr. Prieskumník) cez widget a klikni naň. Je widget pod ním?")


def unparent(u, hwnd, rect, style, results):
    """Return the widget to a top-level window at its old screen position."""
    u.SetParent(hwnd, None)
    u.SetWindowLongPtrW(hwnd, -16, style)
    # NOSIZE | NOACTIVATE | FRAMECHANGED | SHOWWINDOW
    ok = u.SetWindowPos(hwnd, 0, rect[0], rect[1], 0, 0, 0x0001 | 0x0010 | 0x0020 | 0x0040)
    results.add("návrat medzi okná najvyššej úrovne", "OK" if ok else "FAIL",
                f"rodič teraz {u.GetAncestor(hwnd, 1)}")


def part_d1(window, hwnd, u, results):
    """Parent into Progman itself and raise above SHELLDLL_DefView."""
    progman = u.FindWindowW("Progman", None)
    res = ctypes.c_size_t(0)
    ret = u.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 1000, ctypes.byref(res))
    results.add("D1: Progman, SendMessageTimeoutW 0x052C", "INFO", f"hwnd {progman}, návrat {ret}")
    results.add("D1: potomkovia Progman pred", "INFO", ", ".join(child_classes(u, progman)))
    rect = window_rect(u, hwnd)
    style = u.GetWindowLongPtrW(hwnd, -16)
    ctypes.set_last_error(0)
    prev = u.SetParent(hwnd, progman)
    err = ctypes.get_last_error()
    now_parent = u.GetAncestor(hwnd, 1)
    results.add("D1: SetParent(widget, Progman)", "OK" if now_parent == progman else "FAIL",
                f"vrátil {prev}, GetLastError {err}, teraz {now_parent}")
    u.SetWindowLongPtrW(hwnd, -16, (style & ~0x80000000) | 0x40000000)
    pt = wintypes.POINT(rect[0], rect[1])
    u.ScreenToClient(progman, ctypes.byref(pt))
    # HWND_TOP (0) within Progman, NOSIZE | NOACTIVATE | FRAMECHANGED
    ok = u.SetWindowPos(hwnd, 0, pt.x, pt.y, 0, 0, 0x0001 | 0x0010 | 0x0020)
    results.add("D1: SetWindowPos HWND_TOP v rámci Progman", "OK" if ok else "FAIL",
                f"návrat {int(bool(ok))}, pozícia {pt.x},{pt.y}")
    results.add("D1: potomkovia Progman po", "INFO", ", ".join(child_classes(u, progman)))
    time.sleep(1)
    try:
        d_confirms(results, "D1")
    finally:
        unparent(u, hwnd, rect, style, results)


def bind_event_hook():
    hu = ctypes.WinDLL("user32", use_last_error=True)
    proc = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
                              ctypes.c_long, ctypes.c_long, wintypes.DWORD, wintypes.DWORD)
    hu.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, proc,
                                   wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
    hu.SetWinEventHook.restype = wintypes.HANDLE
    hu.UnhookWinEvent.argtypes = [wintypes.HANDLE]
    hu.UnhookWinEvent.restype = wintypes.BOOL
    hu.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
                                wintypes.UINT, wintypes.UINT]
    hu.PeekMessageW.restype = wintypes.BOOL
    hu.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    hu.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    hu.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    hu.ShowWindow.restype = wintypes.BOOL
    return hu, proc


DESKTOP_CLASSES = ("Progman", "WorkerW", "SHELLDLL_DefView", "SysListView32")


def foreground_kind(cls):
    """Which kind of window became foreground: desktop, taskbar or other app."""
    if cls in DESKTOP_CLASSES:
        return "plocha"
    if cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        return "panel úloh"
    return "iná aplikácia"


def part_d2(window, hwnd, u, results):
    """Stay top-level at the bottom; on foreground = desktop re-show, else push to bottom."""
    hu, proc = bind_event_hook()
    stop = threading.Event()
    counts = {"plocha": 0, "iná aplikácia": 0, "panel úloh": 0, "vlastné": 0}
    logged = {"n": 0}
    send_to_bottom(hwnd, u)

    def on_event(hook, event, fg, id_obj, id_child, thread, ms):
        try:
            if fg == hwnd or u.GetAncestor(fg, 2) == hwnd:  # GA_ROOT
                kind = "vlastné"
            else:
                kind = foreground_kind(class_name(u, fg))
            counts[kind] += 1
            if kind == "plocha":
                hu.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE, in case Win+D minimised it
                # NOSIZE | NOMOVE | NOACTIVATE | SHOWWINDOW: TOPMOST then NOTOPMOST
                u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
                u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
            elif kind == "iná aplikácia":
                send_to_bottom(hwnd, u)
            if logged["n"] < 20:
                logged["n"] += 1
                results.add("D2: udalosť popredia", "INFO", kind)
        except Exception as exc:
            results.add("D2: udalosť popredia", "FAIL", safe_error(exc))

    callback = proc(on_event)  # kept alive until the thread ends
    state = {}

    def pump():
        # EVENT_SYSTEM_FOREGROUND = 3, WINEVENT_OUTOFCONTEXT = 0
        state["hook"] = hu.SetWinEventHook(3, 3, None, callback, 0, 0, 0)
        state["err"] = ctypes.get_last_error()
        state["ready"] = True
        msg = wintypes.MSG()
        while not stop.is_set():
            while hu.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                hu.TranslateMessage(ctypes.byref(msg))
                hu.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.02)
        hu.UnhookWinEvent(state["hook"])

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    while not state.get("ready"):
        time.sleep(0.05)
    results.add("D2: SetWinEventHook(EVENT_SYSTEM_FOREGROUND)", "OK" if state["hook"] else "FAIL",
                f"GetLastError {state['err']}")
    try:
        if state["hook"]:
            d_confirms(results, "D2")
    finally:
        stop.set()
        t.join(2)
        results.add("D2: počty udalostí", "INFO", ", ".join(f"{k} {v}" for k, v in counts.items()))


def part_d0(window, hwnd, u, results):
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
    results.confirm("D0: po SetParent widget viditeľný",
                    "Je widget stále viditeľný na svojom mieste?")
    results.confirm("D0: Win+D: widget viditeľný",
                    "Stlač Win+D. Je widget po Win+D stále viditeľný na ploche?")
    results.confirm("D0: klikateľný",
                    "Dá sa na widget kliknúť (reaguje na klik)?")
    results.confirm("D0: ťahateľný",
                    "Dá sa widget potiahnuť myšou?")
    results.confirm("D0: zostáva pod ostatnými oknami",
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
        for name, fn in (("D0", part_d0), ("D1", part_d1), ("D2", part_d2)):
            if name in parts:
                try:
                    fn(window, hwnd, u, results)
                except Exception as exc:
                    results.add(f"{name}: beh", "FAIL", safe_error(exc))
    except Exception as exc:  # the probe records what breaks, never hangs the window
        results.add("beh sondy kola 2 (C, D)", "FAIL", safe_error(exc))
    finally:
        stop.set()
        window.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--part", choices=["C", "D0", "D1", "D2"],
                        help="run just one part (default: C, D1, D2)")
    args = parser.parse_args()
    parts = {args.part} if args.part else {"C", "D1", "D2"}

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
