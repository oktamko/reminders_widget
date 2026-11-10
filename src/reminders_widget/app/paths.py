"""Where the app keeps its files: %LOCALAPPDATA%\\RemindersWidget."""
import os
from pathlib import Path

APP_FOLDER = "RemindersWidget"


def data_dir(base: Path | None = None) -> Path:
    """The app's data folder, created if missing. `base` replaces %LOCALAPPDATA% (tests)."""
    root = Path(base) if base is not None else Path(
        os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    folder = root / APP_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    logs_dir(folder)
    return folder


def db_path(d: Path) -> Path:
    return d / "reminders.db"


def session_path(d: Path) -> Path:
    return d / "session.bin"


def webview_dir(d: Path) -> Path:
    return d / "webview"


def logs_dir(d: Path) -> Path:
    path = d / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
