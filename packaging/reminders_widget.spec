# PyInstaller spec: a windowed one-folder build at dist/RemindersWidget/RemindersWidget.exe
# Build: powershell packaging/build.ps1
import sys
from pathlib import Path

HERE = Path(SPECPATH)
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from build_inputs import ui_datas  # noqa: E402

a = Analysis(
    [str(HERE / "launch.py")],
    pathex=[str(ROOT / "src")],
    datas=ui_datas(ROOT),
    hiddenimports=["clr", "webview.platforms.winforms", "webview.platforms.edgechromium"],
    excludes=["tkinter", "playwright", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RemindersWidget",
    icon=str(HERE / "icon.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="RemindersWidget", upx=False)
