"""Refuse to release a tag that does not match project.VERSION.  Usage: python packaging/check_tag.py v0.1.0"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reminders_widget.project import VERSION  # noqa: E402


def check(tag: str) -> int:
    if tag == f"v{VERSION}":
        return 0
    print(f"tag {tag!r} does not match project.VERSION {VERSION!r} (expected 'v{VERSION}')", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(check(sys.argv[1] if len(sys.argv) > 1 else ""))
