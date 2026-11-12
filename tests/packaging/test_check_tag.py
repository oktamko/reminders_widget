"""The release tag must match project.VERSION; the workflows keep their rights small."""
import re
import sys
from pathlib import Path

from reminders_widget.project import VERSION

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packaging"))
WORKFLOWS = ROOT / ".github" / "workflows"


def test_tag_must_match_the_version(capsys):
    from check_tag import check
    assert check(f"v{VERSION}") == 0
    assert check("v99.0.0") == 1 and "v99.0.0" in capsys.readouterr().err
    assert check(VERSION) == 1                      # the "v" is required


def test_ci_runs_the_tests_on_windows_for_every_push():
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"^on:\s*\n\s+push:", ci, re.M) and "pull_request" in ci
    assert "windows-latest" in ci and "python -m pytest" in ci


def test_release_checks_the_tag_builds_and_publishes_with_minimal_rights():
    rel = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert '"v*"' in rel and "packaging/check_tag.py" in rel and "build.ps1 -Installer" in rel
    assert "gh release create" in rel and "SHA256SUMS.txt" in rel
    assert re.search(r"^permissions:\s*\n\s+contents: write\s*$", rel, re.M)
    assert "${{ github.ref_name }}\"" not in rel.split("env:")[0]   # the tag reaches scripts through env
    assert not re.findall(r"secrets\.(?!GITHUB_TOKEN)", rel)
