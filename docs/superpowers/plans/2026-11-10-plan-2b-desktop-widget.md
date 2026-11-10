# Plan 2b — Desktop Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the plan-2a window into the spec's desktop widget: pinned on the wallpaper under all windows and still there after `Win+D`, with glass made from the wallpaper (aligned, smooth while dragging, one uniform frosted surface), light/dark text chosen from the wallpaper, a tray icon with a hotkey, a normal-window mode, autostart and a single instance.

**Architecture:** A new `reminders_widget.shell` package owns everything Windows-specific: wallpaper composition (`wallpaper.py`, pure + ctypes), window placement and the `Win+D` foreground hook (`desktop.py`), the tray icon and hotkey (`tray.py`), autostart and single instance (`system.py`). `app/main.py` picks a mode and hands the window to the shell; the UI gets the glass image and the contrast set through the existing `rw:state` push plus one `rw:glass` event.

**Tech Stack:** Python 3.12, pywebview 6.2.1 (pinned — `signin.py` uses its internals), ctypes (user32, gdi32, dwmapi), Pillow, pystray (new dependency, tray icon), pytest, Playwright UI tests.

**Spec:** `docs/superpowers/specs/2026-11-09-reminders-widget-design.md` (§5 window modes, look, motion; probe answers rounds 1–3 in §3), `docs/superpowers/plans/2026-11-09-plan-1-followups.md` (glass and Win+D findings).

## Global Constraints

- pywebview stays pinned at 6.2.1 in `pyproject.toml` (`pywebview==6.2.1`).
- No logging of wallpaper paths, user names, window titles of other apps, reminder content; window classes may be logged as kinds only ("desktop", "taskbar", "other", "own").
- Glass is one uniform frosted surface; no rim/displacement layer (Martin's decision). Blur and saturation are computed once per wallpaper/monitor change, never per frame or per move.
- All animations use `transform`/`opacity`; `prefers-reduced-motion` shortens them to a cross-fade.
- Interface text stays English; Martin's `banner_for` is not touched.
- Settings live in the Store settings table: `"mode"` ("desktop" | "window", default "desktop"), `"widget_rect"` (x, y, w, h physical px), `"autostart"` (bool, default true).
- Commits after each task, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Human-run steps are marked **(Martin runs it)**.

## Review Focus

1. **Wallpaper changes while the app runs** (slideshow, Spotlight, new picture). Expected: the glass follows within a few seconds; never a stale or black layer. Test in Task 1/4.
2. **Monitor layout changes** (unplug the second monitor, change scale). Expected: the widget stays on a visible monitor and the glass re-aligns; it is never placed off-screen. Test in Task 2.
3. **Clicking the widget in desktop mode.** Expected: clicks, typing in "New reminder", the detail panel and right-click all work; the widget does not jump above other windows when clicked and does not disappear. Test in Task 2 + Task 7.
4. **A second launch** (autostart plus a manual start). Expected: no second window or second sync; the running widget is shown. Test in Task 6.
5. **Light wallpaper.** Expected: dark text with ≥ 4.5:1 contrast; switching wallpaper dark↔light swaps the token set. Test in Task 4.

---

### Task 1: Wallpaper composition, measured against what Windows draws

**Files:**
- Create: `src/reminders_widget/shell/__init__.py`, `src/reminders_widget/shell/wallpaper.py`, `tests/shell/test_wallpaper.py`, `probes/probe_glass_align.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class Monitor:            # physical pixels
    rect: tuple[int, int, int, int]   # left, top, right, bottom
    scale: float                       # 1.0, 1.25, ...

def read_settings() -> tuple[Path | None, str, tuple[int, int, int]]   # path, mode name, bg rgb (path never logged)
def compose(img: PIL.Image.Image, monitors: list[Monitor], virtual: tuple[int,int,int,int],
            mode: str, bg: tuple[int,int,int]) -> PIL.Image.Image          # physical-px virtual-screen composite
def paint_desktop(virtual) -> PIL.Image.Image                                # GDI PaintDesktop into a memory DC: what Windows draws, no windows
def measure_offset(ours: Image, real: Image) -> tuple[int, int, float]      # (dx, dy, mean abs error) by best match of a downscaled search
@dataclass class Glass: image_css: Image; origin_css: tuple[float, float]; luminance: float
def build_glass(scale: float) -> Glass | None                               # compose → css size → frost once; None if no wallpaper file
```

Port `mode_name`, `parse_background_color`, `image_dest_rect`, `build_composite`, `css_size`, `origin_css`, `frost`, `read_wallpaper_path`, `read_wallpaper_registry`, `enum_monitors`, `virtual_rect` from `probes/probe_round2_desktop.py` (tested there; keep their tests).
- [ ] **Step 1:** Write `probes/probe_glass_align.py` (no window, no human): `paint_desktop(virtual)` vs `compose(...)` for the current wallpaper → print `(dx, dy, error)` per monitor and overall. The executor runs it on Martin's PC (it reads only the wallpaper and the desktop image, prints numbers, writes nothing). Expected on the current code: a non-zero `dy` (the reported upward shift).
- [ ] **Step 2:** Find the cause from the numbers (systematic-debugging: e.g. Windows anchoring "fill" differently on a monitor, work-area vs monitor rect, DPI) and fix `compose`; add a unit test that reproduces the measured case with a synthetic image and two monitors.
- [ ] **Step 3:** Re-run the probe: `|dx|, |dy| ≤ 1` px and error close to JPEG noise for every monitor. Record the numbers in the follow-ups file.
- [ ] **Step 4:** `build_glass` + tests (composite size equals the virtual screen in CSS px; luminance of a white image ≈ 1, black ≈ 0; `None` when the path is missing). Commit `feat: wallpaper glass aligned with the desktop`.

---

### Task 2: Desktop mode window

**Files:**
- Create: `src/reminders_widget/shell/desktop.py`, `tests/shell/test_desktop.py`

**Interfaces:**
- Consumes: pywebview window's native HWND (`window.native.Handle.ToInt64()`), `foreground_kind` from the probe (port it).
- Produces:

```python
def clamp_to_monitors(rect, monitors) -> tuple[int,int,int,int]   # keep at least 80×80 px visible on some monitor; else centre on the primary
class DesktopPin:
    def __init__(self, hwnd: int, *, user32=None): ...           # injectable for tests
    def apply(self) -> None      # WS_EX_TOOLWINDOW (no taskbar button), HWND_BOTTOM, DWMWA_WINDOW_CORNER_PREFERENCE=2
    def start(self) -> None      # SetWinEventHook(EVENT_SYSTEM_FOREGROUND) on its own message-loop thread
    def stop(self) -> None
    def on_foreground(self, kind: str) -> None   # "desktop" → show + raise to HWND_TOP without activation; "other" → HWND_BOTTOM; "own" → nothing
```

The probe's D2 "click" failure came from a page with nothing clickable; here the real UI is used. Clicking the widget makes it the foreground window ("own") — do not push it down then; push it down only when another app becomes foreground (Review Focus 3).
- [ ] Tests with a fake user32 recording calls: `apply` sets the tool-window style, bottom z-order and rounded corners; `on_foreground("desktop")` shows and raises without `SWP_ACTIVATE`; `"other"` → bottom; `"own"` → no call; `clamp_to_monitors` for a rect on a removed monitor returns one on the primary (Review Focus 2); hook callback kept referenced (assert the attribute exists after `start`).
- [ ] Implement, commit `feat: desktop mode window`.

---

### Task 3: Tray icon and hotkey

**Files:**
- Create: `src/reminders_widget/shell/tray.py`, `tests/shell/test_tray.py`; Modify: `pyproject.toml` (add `pystray>=0.19`, pin `pywebview==6.2.1`)

**Interfaces:**
- Produces: `class Tray(on_show, on_mode, on_quit, *, icon_factory=None)` with `start()`, `stop()`, menu items "Show reminders", "Desktop widget" (radio), "Normal window" (radio), "Start with Windows" (check), "Quit"; `class Hotkey(callback, vk=ord('R'), mods=MOD_CONTROL|MOD_ALT)` using `RegisterHotKey` on its own thread; registering fails gracefully (logged) when another app owns the combination.
- [ ] Tests with fakes: menu callbacks fire; radio state mirrors the current mode; hotkey thread calls back on a simulated `WM_HOTKEY`; a failed `RegisterHotKey` logs and does not raise.
- [ ] Implement (icon: a 64×64 Pillow-drawn ring in the accent colour, no file), commit `feat: tray icon and hotkey`.

---

### Task 4: Glass and contrast in the UI

**Files:**
- Modify: `src/reminders_widget/app/ui/style.css`, `src/reminders_widget/app/ui/app.js`, `src/reminders_widget/app/ui/index.html`; Create: `tests/app/test_ui_glass.py`

**Interfaces:**
- Consumes: `Glass` from Task 1 (Python sends `rw:glass` with `{url, origin:[x,y], luminance}`; `url` is a JPEG data URL made once).
- Produces: in desktop mode the `.g` card shows a `.wall` layer (`position:fixed` in the page, natural CSS size, `transform: translate3d(originX - screenX, originY - screenY, 0)` updated on `requestAnimationFrame` only when `screenX/Y` changed, `will-change: transform`); `body.light` / `body.dark` token sets chosen from `luminance` (> 0.55 → light, i.e. dark text); in window mode no wallpaper layer, tokens from `prefers-color-scheme`.
- [ ] UI tests: after `rw:glass` the layer exists with the given size and moves by the right amount when `screenX` changes (stub `window.screenX`); luminance 0.9 → `body.light` and the computed text colour contrasts ≥ 4.5:1 with the computed glass colour over white; 0.1 → `body.dark`; window mode → no `.wall` (Review Focus 5); reduced motion → transitions ≤ 0.01 s.
- [ ] Python: `shell.wallpaper.build_glass` runs at start and on `WM_SETTINGCHANGE`/`SPI_SETDESKWALLPAPER`, display change and DPI change (listener thread with a hidden message window); a wallpaper with no file (solid colour) sends `{url:null}` and the UI uses the tinted style (Review Focus 1). Test with fakes.
- [ ] Commit `feat: glass from the wallpaper in the widget`.

---

### Task 5: Modes and window placement

**Files:**
- Modify: `src/reminders_widget/app/main.py`, `src/reminders_widget/app/api.py`; Test: `tests/app/test_modes.py`

**Interfaces:**
- Produces: `App.set_mode(mode: str)`; `Api.get_mode()`/`Api.set_mode(mode)` (JS: a small menu on the list-picker panel with "Desktop widget" / "Normal window"); mode persists in settings; desktop mode → frameless, `easy_drag` on the header area, `DesktopPin` applied, size 360×560 CSS, position from `"widget_rect"` clamped; window mode → normal framed window 400×640, no pin; switching recreates the pywebview window (keeping the App and scheduler); widget position saved on move (debounced 500 ms).
- [ ] Tests (fakes for the window factory and pin): default mode desktop; switching saves the setting and builds the other window kind once; a saved rect off all monitors is clamped (Review Focus 2); closing the widget in desktop mode hides it to the tray instead of quitting (Quit only from the tray).
- [ ] Commit `feat: desktop and window modes`.

---

### Task 6: Single instance and autostart

**Files:**
- Create: `src/reminders_widget/shell/system.py`, `tests/shell/test_system.py`; Modify: `src/reminders_widget/app/main.py`

**Interfaces:**
- Produces: `acquire_single_instance(name="RemindersWidget.Single") -> bool` (named mutex); `signal_running_instance()` (named event `RemindersWidget.Show` the first instance waits on and then shows itself); `set_autostart(enabled: bool, command: str)` / `autostart_enabled() -> bool` via `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value `RemindersWidget` with `pythonw.exe -m reminders_widget` for now (plan 3 replaces it with the installed exe).
- [ ] Tests: second `acquire_single_instance` in the same test process (separate handle) returns False; the show event wakes a waiting thread; autostart write/read/remove against a test-only value name (cleaned up in `finally`) (Review Focus 4).
- [ ] `main.run`: a second instance signals the first and exits; the tray "Start with Windows" toggles autostart; default on at first run. Commit `feat: single instance and autostart`.

---

### Task 7: Motion and status polish

**Files:**
- Modify: `src/reminders_widget/app/scheduler.py` (an `on_start` callback), `src/reminders_widget/app/api.py` (`syncing` flag in state), `src/reminders_widget/app/ui/*`; Test: extend `tests/app/test_scheduler.py`, `tests/app/test_ui.py`

- [ ] Status dot pulses while a run is in progress (`on_start` → push `syncing: true`; result → false); cross-fade between status colours.
- [ ] A state `version` counter in `Api.state()`; the UI ignores a state older than the one it shows (plan-2a deferred item).
- [ ] `inert` on the hidden detail panel; `swapText` race fixed (the last requested text wins).
- [ ] Tests for each; commit `feat: motion and status polish`.

---

### Task 8: Real desktop run (Martin runs it)

- [ ] `.\.venv\Scripts\python.exe -m reminders_widget`: the widget sits on the wallpaper; the glass lines up with the wallpaper around it and stays smooth while dragging; text readable on his wallpaper; click a ring, priority, add a reminder, open the detail panel, right-click; open other windows (the widget stays under them); `Win+D` (the widget stays visible and clickable); tray menu switches to the normal window and back; `Ctrl+Alt+R` shows it; restart the PC (it starts by itself, one instance); unplug the second monitor if possible. The executor records the result in `docs/superpowers/plans/2026-11-10-plan-2b-checklist.md` (no reminder titles).
