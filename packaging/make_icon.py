"""Write packaging/icon.ico from the tray icon drawing. Run: python packaging/make_icon.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from build_inputs import ICON_SIZES  # noqa: E402
from reminders_widget.shell.tray import _icon_image  # noqa: E402


def main() -> None:
    big = _icon_image().resize((256, 256))
    big.save(HERE / "icon.ico", sizes=[(s, s) for s in ICON_SIZES])


if __name__ == "__main__":
    main()
