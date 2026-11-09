"""Throwaway probe 4: glass (transparent) pywebview window and pinning to the desktop.

Run it yourself:  .venv/Scripts/python probes/probe_glass.py

- Opens a frameless, transparent WebView2 window with a static glass card (no real data).
- Tries three looks for 10 s each (transparent only, Acrylic, Mica-alt) and asks you a/n.
- Then pins the window to the bottom of the window order and asks you to press Win+D,
  click the desktop and open another window.
- Touches no iCloud data. Results are appended to probe-results.txt.
"""
import ctypes
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from urllib.parse import quote

from probe import RESULTS_FILE
from probe_webview_login import AppendResults

HERE = Path(__file__).parent
GLASS_HTML = HERE / "glass.html"
FONT_FILE = Path(r"C:\Users\oktamko\Documents\4_Zdroje\Fonts\písmo VUT\OpenSans-Regular.ttf")
WINDOW_TITLE = "Glass probe widget"
SHOW_SECONDS = 10

DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_NONE = 1
DWMSBT_ACRYLIC = 3
DWMSBT_MICA_ALT = 4
HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010


# --- Pure helpers / thin ctypes wrappers (unit-tested with fakes) ---

def font_file_uri(path):
    """file:/// URI with non-ASCII characters and spaces percent-encoded."""
    return Path(path).as_uri()


def glass_page_url(html_path, font_uri):
    url = Path(html_path).as_uri()
    return url + ("#font=" + quote(font_uri, safe="") if font_uri else "")


def hresult_text(value):
    return f"0x{value & 0xFFFFFFFF:08X}"


class MARGINS(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


def set_backdrop(hwnd, value, dwm):
    """DwmSetWindowAttribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, value); returns the HRESULT."""
    return dwm.DwmSetWindowAttribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE,
                                     ctypes.byref(ctypes.c_int(value)), 4)


def extend_frame(hwnd, dwm):
    """Extend the frame into the whole client area so the backdrop shows through."""
    return dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARGINS(-1, -1, -1, -1)))


def send_to_bottom(hwnd, user32):
    """Put the window at the bottom of the z-order without moving, resizing or activating."""
    return bool(user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))


# --- Real Win32 bindings ---

def load_win32():
    dwm = ctypes.WinDLL("dwmapi")
    dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p,
                                          wintypes.DWORD]
    dwm.DwmSetWindowAttribute.restype = ctypes.c_long
    dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.POINTER(MARGINS)]
    dwm.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
    user32 = ctypes.WinDLL("user32")
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    return dwm, user32


def find_hwnd(window, user32):
    """Native handle from the WinForms form; falls back to a lookup by window title."""
    try:
        return int(window.native.Handle.ToInt64()), "window.native.Handle"
    except Exception:  # cross-thread access guard or native not set yet
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        return (int(hwnd) if hwnd else 0), "FindWindowW"


def make_ui_action(fn):
    """Wrap fn as a System.Action for Control.Invoke (needs pythonnet, i.e. a running webview)."""
    import System
    return System.Action(fn)


def set_form_black(native, black, make_action=make_ui_action):
    """Set the WinForms form BackColor on the UI thread; returns the ARGB read back.

    pywebview leaves the form gray; DWM only treats black as glass for GDI-painted areas.
    """
    def apply():
        native.BackColor = black
    native.Invoke(make_action(apply))
    return native.BackColor.ToArgb() & 0xFFFFFFFF


# --- Probe steps ---

def apply_backdrop(label, value, hwnd, dwm, results):
    hr = set_backdrop(hwnd, value, dwm)
    results.add(f"DwmSetWindowAttribute 38={value} ({label})", "OK" if hr == 0 else "FAIL",
                f"HRESULT {hresult_text(hr)}")
    return hr == 0


def show_and_ask(questions, results):
    """Show the window for SHOW_SECONDS, then ask each (step, question) in turn."""
    print(f"\nPozeraj okno {SHOW_SECONDS} s...", flush=True)
    time.sleep(SHOW_SECONDS)
    for step, question in questions:
        results.confirm(step, question)


def report_font(window, results):
    try:
        state = window.evaluate_js("window.__fontState || 'neznámy'")
    except Exception as exc:
        state = f"nečitateľný ({type(exc).__name__})"
    results.add("písmo Open Sans v stránke", "INFO", str(state))


def install_pin_hook(window, hwnd, user32, results):
    """Re-send to bottom whenever Windows activates the window (a click raises it)."""
    def on_activated(*_):
        send_to_bottom(hwnd, user32)

    try:
        window.native.Activated += on_activated
        results.add("pripnutie dole: mechanizmus", "INFO", "udalosť WinForms Activated")
        return None
    except Exception as exc:
        results.add("udalosť Activated", "FAIL", f"{type(exc).__name__}; použijem časovač 1 s")
    stop = threading.Event()

    def loop():
        while not stop.wait(1):
            send_to_bottom(hwnd, user32)

    threading.Thread(target=loop, daemon=True).start()
    results.add("pripnutie dole: mechanizmus", "INFO", "časovač 1 s")
    return stop


def run_probe(window, results):
    dwm, user32 = load_win32()
    try:
        window.events.loaded.wait(20)
        time.sleep(1)
        hwnd, how = find_hwnd(window, user32)
        results.add("HWND okna", "OK" if hwnd else "FAIL", f"získaný cez {how}")
        if not hwnd:
            return
        font_ok = FONT_FILE.exists()
        results.add("písmo Open Sans", "INFO", "nájdené" if font_ok else "chýba, sans-serif")

        try:
            import System.Drawing
            argb = set_form_black(window.native, System.Drawing.Color.Black)
            results.add("BackColor formulára po nastavení", "INFO",
                        f"ARGB 0x{argb:08X} ({'čierna' if argb == 0xFF000000 else 'NIE čierna'})")
        except Exception as exc:
            results.add("nastavenie BackColor formulára na čiernu", "FAIL",
                        f"{type(exc).__name__}: {str(exc)[:150]}")
        report_font(window, results)

        print("\n--- Variant A: iba priehľadné pozadie WebView2 (bez DWM) ---")
        show_and_ask([
            ("priehľadné pozadie WebView2: plocha za oknom viditeľná",
             "Vidíš kartu 'Dnes' a okolo nej tapetu/plochu (okno je priehľadné)?"),
            ("priehľadné pozadie WebView2: rohy čisté",
             "Sú rohy karty čisté, bez čierneho, sivého alebo bieleho obdĺžnika?"),
        ], results)

        print("\n--- Variant B: Acrylic ---")
        hr = extend_frame(hwnd, dwm)
        results.add("DwmExtendFrameIntoClientArea -1", "OK" if hr == 0 else "FAIL",
                    f"HRESULT {hresult_text(hr)}")
        if apply_backdrop("Acrylic", DWMSBT_ACRYLIC, hwnd, dwm, results):
            show_and_ask([
                ("Acrylic: rozmazaná tapeta viditeľná",
                 "Je za kartou viditeľná rozmazaná tapeta?"),
                ("Acrylic: rohy čisté",
                 "Sú rohy karty čisté, bez čierneho/bieleho obdĺžnika?"),
            ], results)

        print("\n--- Variant C: Mica-alt ---")
        apply_backdrop("vypnutie", DWMSBT_NONE, hwnd, dwm, results)
        if apply_backdrop("Mica-alt", DWMSBT_MICA_ALT, hwnd, dwm, results):
            show_and_ask([
                ("Mica-alt: matné pozadie z farieb tapety viditeľné",
                 "Je za kartou viditeľné jemné matné pozadie z farieb tapety (Mica)?"),
                ("Mica-alt: rohy čisté",
                 "Sú rohy karty čisté, bez čierneho/bieleho obdĺžnika?"),
            ], results)

        print("\n--- Pripnutie na plochu (najspodnejšie v poradí okien) ---")
        ok = send_to_bottom(hwnd, user32)
        results.add("SetWindowPos HWND_BOTTOM", "OK" if ok else "FAIL")
        stop = install_pin_hook(window, hwnd, user32, results)
        try:
            results.confirm("Win+D, widget viditeľný na ploche",
                            "Stlač Win+D. Win+D minimalizuje bežné okná, takže vidíš plochu. "
                            "Je widget po Win+D stále viditeľný na ploche?")
            results.confirm("klik na plochu, widget zostal dole",
                            "Klikni na prázdne miesto plochy a potom na samotný widget. "
                            "Zostal widget viditeľný a pod ostatnými oknami?")
            results.confirm("iné okno otvorené nad widgetom",
                            "Otvor nejaké iné okno (napr. Prieskumník) cez widget a klikni "
                            "naň. Je widget pod ním (nevyskočil nad okno)?")
        finally:
            if stop:
                stop.set()
    except Exception as exc:  # probe records what breaks, never hangs the window
        results.add("beh sondy 4", "FAIL", f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        window.destroy()


def main():
    import webview

    sys.stdout.reconfigure(encoding="utf-8")
    results = AppendResults()
    results.add("--- sonda 4: priehľadné okno a pripnutie na plochu ---", "INFO")
    font_uri = font_file_uri(FONT_FILE) if FONT_FILE.exists() else None
    window = webview.create_window(WINDOW_TITLE, glass_page_url(GLASS_HTML, font_uri),
                                   width=380, height=420, x=200, y=150, frameless=True,
                                   easy_drag=True, transparent=True, shadow=False)
    webview.start(run_probe, (window, results))
    print(f"\nHotovo. Výsledky: {RESULTS_FILE.name}")


if __name__ == "__main__":
    main()
