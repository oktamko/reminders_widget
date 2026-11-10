"""Rotating log file for the app. Callers log counts, statuses and hashes only."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_logging(logs: Path, max_bytes: int = 1_000_000) -> None:
    """Send the `reminders_widget` loggers to logs/app.log (max_bytes × 3 backups)."""
    logger = logging.getLogger("reminders_widget")
    for handler in list(logger.handlers):
        if getattr(handler, "_rw_app_log", False):
            logger.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(Path(logs) / "app.log", maxBytes=max_bytes,
                                  backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    handler._rw_app_log = True
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
