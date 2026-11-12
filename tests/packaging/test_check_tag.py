"""The release tag must match project.VERSION; the workflows keep their rights small."""
import re
import sys
from pathlib import Path

from reminders_widget.project import VERSION

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packaging"))
WORKFLOWS = ROOT / ".github" / "workflows"


def test_tag_must_match_the_version(capsys, monkeypatch):
    from check_tag import check
    monkeypatch.setattr("reminders_widget.project.REPO", "Someone/apple-reminders-widget")
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
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


def test_release_refuses_the_placeholder_or_another_repository(monkeypatch, capsys):   # final review 2
    from check_tag import check
    monkeypatch.setattr("reminders_widget.project.REPO", "OWNER/apple-reminders-widget")
    assert check(f"v{VERSION}") == 1 and "REPO" in capsys.readouterr().err
    monkeypatch.setattr("reminders_widget.project.REPO", "martincimbala/apple-reminders-widget")
    monkeypatch.setenv("GITHUB_REPOSITORY", "MartinCimbala/apple-reminders-widget")
    assert check(f"v{VERSION}") == 0                  # GitHub names ignore case
    monkeypatch.setenv("GITHUB_REPOSITORY", "MartinCimbala/other")
    assert check(f"v{VERSION}") == 1


def test_release_checkout_keeps_no_write_token_on_disk():   # final review 13
    rel = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    checkout = rel[rel.index("actions/checkout"):]
    assert re.search(r"persist-credentials:\s*false", checkout.split("- uses:")[0])
