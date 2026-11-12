"""Name, version and repository live in one place."""
import tomllib
from pathlib import Path

import reminders_widget
from reminders_widget import project

ROOT = Path(__file__).resolve().parents[1]


def test_version_is_single_sourced():
    assert reminders_widget.__version__ == project.VERSION
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" in meta["project"]["dynamic"] and "version" not in meta["project"]
    assert meta["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "reminders_widget.project.VERSION"}


def test_releases_url_uses_repo():
    assert project.releases_url() == f"https://github.com/{project.REPO}/releases"
    assert project.NAME == "Reminders Widget"
