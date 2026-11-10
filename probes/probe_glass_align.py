"""Measure our wallpaper composite against what Windows draws (PaintDesktop).

No window, no human: reads the wallpaper file and the desktop image, prints offsets
and errors per monitor. Never prints the wallpaper path.
Run: .\\.venv\\Scripts\\python.exe probes\\probe_glass_align.py
"""
import ctypes
import sys
from ctypes import wintypes

from PIL import Image, ImageChops, ImageStat

import sys as _sys
_sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))
from reminders_widget.shell.wallpaper import compose as build_composite
from probe_round2_desktop import (bind_user32, enum_monitors, make_dpi_aware,
                                  read_wallpaper_path, read_wallpaper_registry, virtual_rect)


def paint_desktop(virtual):
    """The desktop as Windows paints it (wallpaper only, no windows), physical px."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    for fn, res in ((gdi32.CreateCompatibleDC, wintypes.HDC), (gdi32.CreateCompatibleBitmap, wintypes.HBITMAP),
                    (gdi32.SelectObject, wintypes.HGDIOBJ), (user32.GetDC, wintypes.HDC)):
        fn.restype = res
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SetWindowOrgEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PaintDesktop.argtypes = [wintypes.HDC]
    vl, vt, vr, vb = virtual
    w, h = vr - vl, vb - vt
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    gdi32.SetWindowOrgEx(mem, vl, vt, None)
    ok = user32.PaintDesktop(mem)

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
    bih = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.SelectObject(mem, old)
    lines = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bih), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, screen)
    print(f"PaintDesktop ok={bool(ok)} lines={lines}")
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)


def measure_offset(ours, real, search=160, step=8):
    """(dx, dy, err): shift of `ours` that best matches `real` (dx>0: ours must move right)."""
    def best(a, b, rng, k):
        a, b = a.convert("L"), b.convert("L")
        if k > 1:
            a = a.resize((a.width // k, a.height // k))
            b = b.resize((b.width // k, b.height // k))
        r = rng // k
        cw, ch = b.width - 2 * r, b.height - 2 * r
        core = b.crop((r, r, r + cw, r + ch))
        found = None
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                part = a.crop((r - dx, r - dy, r - dx + cw, r - dy + ch))
                err = ImageStat.Stat(ImageChops.difference(part, core)).mean[0]
                if found is None or err < found[2]:
                    found = (dx * k, dy * k, err)
        return found
    coarse = best(ours, real, search, step)
    # refine at full resolution around the coarse answer, on a centre window
    cx, cy = ours.width // 2, ours.height // 2
    win = 300
    box_real = real.crop((cx - win, cy - win, cx + win, cy + win))
    fine = None
    for dy in range(coarse[1] - step, coarse[1] + step + 1):
        for dx in range(coarse[0] - step, coarse[0] + step + 1):
            part = ours.crop((cx - win - dx, cy - win - dy, cx + win - dx, cy + win - dy)).convert("L")
            err = ImageStat.Stat(ImageChops.difference(part, box_real.convert("L"))).mean[0]
            if fine is None or err < fine[2]:
                fine = (dx, dy, err)
    return fine


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    make_dpi_aware()
    u = bind_user32()
    virtual = virtual_rect(u)
    monitors = enum_monitors(u)
    path, _source = read_wallpaper_path(u)
    mode, bg = read_wallpaper_registry()
    rects = [r for r, _dpi in monitors]
    print("virtual", virtual, "monitors", monitors, "mode", mode, "wallpaper file", bool(path))
    real = paint_desktop(virtual)
    if not path:
        print("no wallpaper file; nothing to compare")
        return
    img = Image.open(path)
    print("image size", img.size)
    ours = build_composite(img, rects, virtual, mode, bg)
    print("overall: zero-shift error",
          round(ImageStat.Stat(ImageChops.difference(ours.convert("L"), real.convert("L"))).mean[0], 2))
    vl, vt = virtual[0], virtual[1]
    for i, (ml, mt, mr, mb) in enumerate(rects):
        box = (ml - vl, mt - vt, mr - vl, mb - vt)
        dx, dy, err = measure_offset(ours.crop(box), real.crop(box))
        print(f"monitor {i}: size {mr - ml}x{mb - mt}: best shift dx={dx} dy={dy} err={err:.2f}")


if __name__ == "__main__":
    main()
