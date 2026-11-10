import logging

from reminders_widget.app.logsetup import setup_logging
from reminders_widget.app.paths import data_dir, db_path, logs_dir, session_path, webview_dir


def test_data_dir_created_under_base(tmp_path):
    d = data_dir(tmp_path)
    assert d == tmp_path / "RemindersWidget" and d.is_dir()
    assert db_path(d) == d / "reminders.db"
    assert session_path(d) == d / "session.bin"
    assert webview_dir(d) == d / "webview"
    assert logs_dir(d) == d / "logs" and logs_dir(d).is_dir()


def _close_app_handlers():
    root = logging.getLogger("reminders_widget")
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()


def test_setup_logging_writes_sync_lines(tmp_path):
    try:
        setup_logging(tmp_path)
        logging.getLogger("reminders_widget.sync").info("pull ok 3 records")
        for h in logging.getLogger("reminders_widget").handlers:
            h.flush()
        text = (tmp_path / "app.log").read_text(encoding="utf-8")
        assert "reminders_widget.sync" in text and "pull ok 3 records" in text
    finally:
        _close_app_handlers()


def test_setup_logging_twice_adds_no_duplicate_handler(tmp_path):
    try:
        setup_logging(tmp_path)
        setup_logging(tmp_path)
        assert len(logging.getLogger("reminders_widget").handlers) == 1
    finally:
        _close_app_handlers()


def test_rotation_keeps_at_most_four_files(tmp_path):
    try:
        setup_logging(tmp_path, max_bytes=2000)
        log = logging.getLogger("reminders_widget.sync")
        for i in range(400):
            log.info("line %d %s", i, "x" * 40)
        files = list(tmp_path.glob("app.log*"))
        assert 1 < len(files) <= 4
    finally:
        _close_app_handlers()
