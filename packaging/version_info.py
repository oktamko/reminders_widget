"""The Windows version resource of RemindersWidget.exe, from project.py (name and version in one place)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reminders_widget.project import NAME, VERSION  # noqa: E402

TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(filevers=({nums}), prodvers=({nums}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{name}'),
      StringStruct('FileDescription', '{name}'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'RemindersWidget'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'RemindersWidget.exe'),
      StringStruct('ProductName', '{name}'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write_version_file(path) -> None:
    parts = [int(p) for p in VERSION.split(".")] + [0]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(TEMPLATE.format(nums=", ".join(map(str, parts)), ver=".".join(map(str, parts)), name=NAME),
                          encoding="utf-8")
