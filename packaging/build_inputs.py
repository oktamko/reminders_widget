"""Inputs shared by the PyInstaller spec and its tests."""
from pathlib import Path

ICON_SIZES = (16, 24, 32, 48, 256)


def ui_datas(root: Path) -> list[tuple[str, str]]:
    """Every file of the window's page, placed where app/host.py looks for it."""
    ui = Path(root) / "src" / "reminders_widget" / "app" / "ui"
    return [(str(p), "reminders_widget/app/ui") for p in sorted(ui.rglob("*")) if p.is_file()]
