"""What PyInstaller gets: every UI file, a Windows icon, a windowed one-folder app."""
import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging"
sys.path.insert(0, str(PACKAGING))


def test_spec_bundles_every_ui_file():
    from build_inputs import ui_datas
    ui = ROOT / "src" / "reminders_widget" / "app" / "ui"
    datas = ui_datas(ROOT)
    files = sorted(p for p in ui.rglob("*") if p.is_file())
    assert files and sorted(Path(src) for src, _ in datas) == files
    assert all(dest == "reminders_widget/app/ui" for _, dest in datas)
    assert "ui_datas(" in (PACKAGING / "reminders_widget.spec").read_text(encoding="utf-8")


def test_icon_has_the_windows_sizes():
    with Image.open(PACKAGING / "icon.ico") as ico:
        assert {s[0] for s in ico.info["sizes"]} >= {16, 24, 32, 48, 256}


def test_spec_is_windowed_one_folder_named_remindersWidget():
    spec = (PACKAGING / "reminders_widget.spec").read_text(encoding="utf-8")
    assert re.search(r"console\s*=\s*False", spec)
    assert re.search(r"name\s*=\s*['\"]RemindersWidget['\"]", spec)
    assert "COLLECT(" in spec and "exclude_binaries=True" in spec      # one folder, not onefile
    assert "icon.ico" in spec
