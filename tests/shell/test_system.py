import threading
import uuid

from reminders_widget.shell.system import (ShowSignal, SingleInstance, autostart_command,
                                           autostart_enabled, set_autostart)


def test_second_instance_is_refused():   # Review Focus 4
    name = f"RemindersWidget.Test.{uuid.uuid4()}"
    first, second = SingleInstance(name), SingleInstance(name)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        first.release()
        second.release()
    third = SingleInstance(name)
    assert third.acquire() is True
    third.release()


def test_show_signal_wakes_the_first_instance():
    name = f"RemindersWidget.TestShow.{uuid.uuid4()}"
    hits = threading.Event()
    sig = ShowSignal(name)
    sig.listen(hits.set)
    try:
        ShowSignal(name).send()
        assert hits.wait(2)
    finally:
        sig.close()


def test_autostart_write_read_remove():
    value = f"RemindersWidgetTest{uuid.uuid4().hex[:8]}"
    try:
        assert autostart_enabled(value) is False
        set_autostart(True, r"C:\x\pythonw.exe -m reminders_widget", value)
        assert autostart_enabled(value) is True
        set_autostart(False, "", value)
        assert autostart_enabled(value) is False
    finally:
        set_autostart(False, "", value)


def test_autostart_command_uses_pythonw():
    cmd = autostart_command()
    assert cmd.lower().endswith("pythonw.exe\" -m reminders_widget") or "pythonw" in cmd.lower()
