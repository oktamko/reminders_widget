from reminders_widget.shell.desktop import DesktopPin, clamp_to_monitors, foreground_kind

HWND = 1234


class FakeWin:
    def __init__(self):
        self.calls = []
        self.hook = None
        self.visible = True

    def is_visible(self, hwnd):
        return self.visible

    def hide(self, hwnd):
        self.calls.append(("hide", hwnd))

    def show_noactivate(self, hwnd):
        self.calls.append(("show", hwnd))

    def set_toolwindow(self, hwnd):
        self.calls.append(("toolwindow", hwnd))

    def round_corners(self, hwnd):
        self.calls.append(("corners", hwnd))

    def to_bottom(self, hwnd):
        self.calls.append(("bottom", hwnd))

    def raise_without_activating(self, hwnd):
        self.calls.append(("raise", hwnd))

    def class_of(self, hwnd):
        return {1: "Progman", 2: "WorkerW", 3: "Shell_TrayWnd", 4: "Chrome_WidgetWin_1"}.get(hwnd, "X")

    def root_of(self, hwnd):
        return HWND if hwnd in (HWND, 99) else hwnd

    def start_foreground_hook(self, callback):
        self.hook = callback
        return lambda: self.calls.append(("unhook",))


def test_apply_sets_tool_window_bottom_and_rounded_corners():
    win = FakeWin()
    DesktopPin(HWND, win=win).apply()
    # the taskbar only drops a button when the style changes while the window is hidden (final review #5)
    assert win.calls == [("hide", HWND), ("toolwindow", HWND), ("corners", HWND), ("show", HWND), ("bottom", HWND)]


def test_foreground_desktop_raises_without_activation():   # Win+D
    win = FakeWin()
    pin = DesktopPin(HWND, win=win)
    pin.start()
    win.hook(1)
    assert win.calls[-1] == ("raise", HWND)


def test_foreground_other_app_pushes_to_bottom():
    win = FakeWin()
    pin = DesktopPin(HWND, win=win)
    pin.start()
    win.hook(4)
    assert win.calls[-1] == ("bottom", HWND)


def test_clicking_the_widget_keeps_it_under_other_windows():   # final review #6
    win = FakeWin()
    pin = DesktopPin(HWND, win=win)
    pin.start()
    win.hook(4)           # another app was in front
    n = len(win.calls)
    win.hook(HWND)        # the user clicks the widget
    assert win.calls[n:] == [("bottom", HWND)]


def test_clicking_the_widget_after_win_d_keeps_it_up():   # Review Focus 3
    win = FakeWin()
    pin = DesktopPin(HWND, win=win)
    pin.start()
    win.hook(1)           # Win+D: the desktop is in front, the widget raised
    before = list(win.calls)
    win.hook(HWND)
    win.hook(99)          # a child of the widget
    win.hook(3)           # the taskbar
    assert win.calls == before


def test_a_widget_hidden_to_the_tray_stays_hidden_on_desktop_clicks():   # final review (re-graded)
    win = FakeWin()
    pin = DesktopPin(HWND, win=win)
    pin.start()
    win.visible = False
    win.hook(1)
    assert ("raise", HWND) not in win.calls


def test_stop_unhooks():
    win = FakeWin()
    pin = DesktopPin(HWND, win=win)
    pin.start()
    pin.stop()
    assert win.calls[-1] == ("unhook",)


def test_foreground_kind():
    assert foreground_kind("Progman") == foreground_kind("WorkerW") == "desktop"
    assert foreground_kind("Shell_TrayWnd") == "taskbar" and foreground_kind("Notepad") == "other"


MONS = [(0, 0, 2560, 1440), (2560, 104, 4480, 1304)]


def test_clamp_keeps_a_visible_rect():
    assert clamp_to_monitors((100, 100, 400, 560), MONS) == (100, 100, 400, 560)


def test_clamp_moves_a_rect_from_a_removed_monitor_to_the_primary():   # Review Focus 2
    x, y, w, h = clamp_to_monitors((5000, 300, 400, 560), MONS)
    assert (w, h) == (400, 560) and 0 <= x and x + w <= 2560 and 0 <= y and y + h <= 1440


def test_clamp_pulls_a_mostly_offscreen_rect_back():
    x, y, w, h = clamp_to_monitors((2540, -500, 400, 560), MONS[:1])
    assert x + w <= 2560 and y >= 0
