import logging

from reminders_widget.shell.tray import Hotkey, Tray, menu_items

WM_HOTKEY = 0x0312


def make_tray(mode="desktop", autostart=True):
    calls = []
    t = Tray(on_show=lambda: calls.append("show"), on_mode=lambda m: calls.append(("mode", m)),
             on_autostart=lambda v: calls.append(("autostart", v)), on_quit=lambda: calls.append("quit"),
             get_mode=lambda: mode, get_autostart=lambda: autostart, icon_factory=lambda **kw: None)
    return t, calls


def test_menu_items_and_radio_state():
    t, _ = make_tray(mode="window")
    items = {i["text"]: i for i in menu_items(t)}
    assert list(items) == ["Show reminders", "Desktop widget", "Normal window", "Start with Windows", "Quit"]
    assert items["Normal window"]["checked"]() is True and items["Desktop widget"]["checked"]() is False
    assert items["Start with Windows"]["checked"]() is True
    assert items["Show reminders"]["default"] is True


def test_menu_callbacks_fire():
    t, calls = make_tray(autostart=False)
    items = {i["text"]: i for i in menu_items(t)}
    for name in ("Show reminders", "Normal window", "Desktop widget", "Start with Windows", "Quit"):
        items[name]["action"]()
    assert calls == ["show", ("mode", "window"), ("mode", "desktop"), ("autostart", True), "quit"]


class FakeUser32:
    def __init__(self, ok=True):
        self.ok, self.msgs = ok, [WM_HOTKEY, 0]
        self.registered = []

    def RegisterHotKey(self, hwnd, ident, mods, vk):
        self.registered.append((mods, vk))
        return self.ok

    def UnregisterHotKey(self, hwnd, ident):
        return True

    def GetMessageW(self, msg_ptr, hwnd, lo, hi):
        kind = self.msgs.pop(0) if self.msgs else 0
        if kind:
            msg_ptr._obj.message = kind
        return 1 if kind else 0

    def PostThreadMessageW(self, *a):
        return True


def test_hotkey_calls_back_on_wm_hotkey():
    hits = []
    fake = FakeUser32()
    h = Hotkey(lambda: hits.append(1), user32=fake)
    h.start()
    h.join(2)
    assert hits == [1] and fake.registered == [(0x0001 | 0x0002 | 0x4000, ord("R"))]


def test_hotkey_taken_by_another_app_logs_and_does_not_raise(caplog):
    with caplog.at_level(logging.INFO, logger="reminders_widget"):
        h = Hotkey(lambda: None, user32=FakeUser32(ok=False))
        h.start()
        h.join(2)
    assert "hotkey" in caplog.text and h.registered is False
