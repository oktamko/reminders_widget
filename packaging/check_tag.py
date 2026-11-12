"""Refuse a release whose tag or repository does not match project.py.

Usage (GitHub Actions sets GITHUB_REPOSITORY): python packaging/check_tag.py v0.1.0
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from reminders_widget import project  # noqa: E402


def check(tag: str) -> int:
    problems = []
    if tag != f"v{project.VERSION}":
        problems.append(f"tag {tag!r} does not match project.VERSION {project.VERSION!r} (expected 'v{project.VERSION}')")
    if not project.repo_configured():
        problems.append(f"project.REPO is still the placeholder {project.REPO!r}")
    actual = os.environ.get("GITHUB_REPOSITORY")
    if actual and actual.lower() != project.REPO.lower():
        problems.append(f"project.REPO {project.REPO!r} is not this repository {actual!r}")
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(check(sys.argv[1] if len(sys.argv) > 1 else ""))
