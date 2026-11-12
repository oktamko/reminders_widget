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


def test_autostart_command_frozen_is_the_quoted_exe():   # plan 3 Review Focus 5
    exe = r"C:\Users\Ľuboš Novák\AppData\Local\Programs\Reminders Widget\RemindersWidget.exe"
    assert autostart_command(frozen=True, executable=exe) == f'"{exe}"'


def test_autostart_command_from_source_keeps_pythonw(tmp_path):
    exe = tmp_path / "python.exe"
    (tmp_path / "pythonw.exe").write_text("")
    assert autostart_command(frozen=False, executable=str(exe)) == f'"{tmp_path / "pythonw.exe"}" -m reminders_widget'


def test_autostart_value_reads_the_stored_command():
    from reminders_widget.shell.system import autostart_value
    value = f"RemindersWidgetTest{uuid.uuid4().hex[:8]}"
    try:
        assert autostart_value(value) is None
        set_autostart(True, r'"C:\x y\RemindersWidget.exe"', value)
        assert autostart_value(value) == r'"C:\x y\RemindersWidget.exe"'
    finally:
        set_autostart(False, "", value)
