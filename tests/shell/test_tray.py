import logging

from reminders_widget.shell.tray import Hotkey, Tray, menu_items

WM_HOTKEY = 0x0312


def make_tray(autostart=True, updates=True):
    calls = []
    t = Tray(on_show=lambda: calls.append("show"), on_autostart=lambda v: calls.append(("autostart", v)),
             on_updates=lambda v: calls.append(("updates", v)), on_quit=lambda: calls.append("quit"),
             get_autostart=lambda: autostart, get_updates=lambda: updates, icon_factory=lambda **kw: None)
    return t, calls


def test_menu_items_without_a_mode_switch():   # UI feedback 3: widget only
    t, _ = make_tray()
    items = {i["text"]: i for i in menu_items(t)}
    assert list(items) == ["Show reminders", "Start with Windows", "Check for updates", "Quit"]
    assert items["Start with Windows"]["checked"]() is True and items["Check for updates"]["checked"]() is True
    assert items["Show reminders"]["default"] is True


def test_menu_callbacks_fire():
    t, calls = make_tray(autostart=False, updates=True)
    items = {i["text"]: i for i in menu_items(t)}
    for name in ("Show reminders", "Start with Windows", "Check for updates", "Quit"):
        items[name]["action"]()
    assert calls == ["show", ("autostart", True), ("updates", False), "quit"]


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
