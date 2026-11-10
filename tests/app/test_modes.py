"""WindowHost: modes, placement, close-to-tray, glass push — with a fake pywebview."""
import pytest

from reminders_widget.app.host import WindowHost
from reminders_widget.store import Store


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self, *a):
        return [h(*a) for h in self.handlers]


class FakeWindow:
    def __init__(self, **kw):
        self.kw, self.hidden, self.destroyed, self.js = kw, False, False, []
        self.events = type("E", (), {})()
        for name in ("shown", "closing", "closed", "moved", "resized"):
            setattr(self.events, name, Event())

    def hide(self):
        self.hidden = True

    def show(self):
        self.hidden = False

    def destroy(self):
        self.destroyed = True
        self.events.closed.fire()

    def evaluate_js(self, js):
        self.js.append(js)


class FakeWebview:
    def __init__(self):
        self.windows = []

    def create_window(self, title, url=None, **kw):
        w = FakeWindow(title=title, url=url, **kw)
        self.windows.append(w)
        return w


class FakeApp:
    def __init__(self):
        self.store = Store(":memory:")
        self.api = type("A", (), {"_push": None, "_mode_hook": None})()
        self.shut = False

    def shutdown(self):
        self.shut = True


class FakePin:
    def __init__(self, hwnd):
        self.hwnd, self.applied, self.started, self.stopped = hwnd, False, False, False

    def apply(self):
        self.applied = True

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


MONS = [(0, 0, 2560, 1440)]


def make_host(mode=None, rect=None, shutting_down=lambda: False):
    app, wv, pins = FakeApp(), FakeWebview(), []
    if mode:
        app.store.set_setting("mode", mode)
    if rect:
        app.store.set_setting("widget_rect", rect)

    def pin_factory(hwnd):
        pins.append(FakePin(hwnd))
        return pins[-1]

    host = WindowHost(app, wv, pin_factory=pin_factory, hwnd_of=lambda w: 42,
                      monitors=lambda: MONS, glass_factory=None, save_delay=0,
                      shutting_down=shutting_down)
    return host, app, wv, pins


def test_default_mode_is_a_frameless_desktop_widget():
    host, app, wv, pins = make_host()
    w = host.create()
    assert host.mode == "desktop" and w.kw["frameless"] is True
    assert (w.kw["width"], w.kw["height"]) == (360, 560)
    w.events.shown.fire()
    assert pins and pins[0].applied and pins[0].started


def test_window_mode_is_a_normal_window_without_pin():
    host, app, wv, pins = make_host(mode="window")
    w = host.create()
    w.events.shown.fire()
    assert w.kw.get("frameless", False) is False and (w.kw["width"], w.kw["height"]) == (400, 640)
    assert pins == []


def test_switching_mode_saves_it_and_replaces_the_window():
    host, app, wv, pins = make_host()
    old = host.create()
    old.events.shown.fire()
    host.set_mode("window")
    assert app.store.get_setting("mode", None) == "window"
    assert len(wv.windows) == 2 and old.destroyed and not wv.windows[1].destroyed
    assert pins[0].stopped and not app.shut          # replacing the window does not quit


def test_saved_rect_off_every_monitor_is_clamped():   # Review Focus 2
    host, app, wv, _ = make_host(rect=[5000, 300, 360, 560])
    w = host.create()
    assert 0 <= w.kw["x"] <= 2560 - 360 and 0 <= w.kw["y"] <= 1440 - 560


def test_closing_the_widget_hides_it_to_the_tray():
    host, app, wv, _ = make_host()
    w = host.create()
    assert w.events.closing.fire() == [False] and w.hidden and not app.shut


def test_quit_shuts_everything_down():
    host, app, wv, _ = make_host()
    w = host.create()
    host.quit()
    assert w.destroyed and app.shut


def test_moving_the_widget_saves_its_rect():
    host, app, wv, _ = make_host()
    w = host.create()
    w.events.moved.fire(120, 140)
    assert app.store.get_setting("widget_rect", None) == [120, 140, 360, 560]


def test_glass_is_pushed_to_the_page_in_desktop_mode():
    host, app, wv, _ = make_host()
    w = host.create()
    host.push_glass({"url": None, "width": 0, "height": 0, "origin": [0, 0], "luminance": 0.9, "background": "#fff"})
    assert any("rw:glass" in js for js in w.js)


def test_api_mode_hook_switches_mode():
    host, app, wv, _ = make_host()
    host.create()
    app.api._mode_hook("window")
    assert host.mode == "window"


def test_windows_shutdown_closes_the_widget_instead_of_hiding_it():   # final review #1
    host, app, wv, _ = make_host(shutting_down=lambda: True)
    w = host.create()
    assert w.events.closing.fire() == [True] and not w.hidden
    w.events.closed.fire()
    assert app.shut
