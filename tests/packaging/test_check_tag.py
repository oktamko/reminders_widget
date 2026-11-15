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
    assert re.search(r"^permissions: \{\}", rel, re.M)                 # nothing by default; each job asks
    granted = re.findall(r"^\s+(contents|actions|\w+): (read|write)\s", rel, re.M)
    assert set(granted) <= {("contents", "read"), ("actions", "read"), ("contents", "write")}
    assert "${{ github.ref_name }}\"" not in rel.split("env:")[0]   # the tag reaches scripts through env
    assert set(re.findall(r"secrets\.(\w+)", rel)) <= {"GITHUB_TOKEN", "SIGNPATH_API_TOKEN"}


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



def _steps(rel):
    return {chunk.split("\n", 1)[0].strip(): chunk for chunk in rel.split("      - name: ")[1:]}


def test_release_signs_app_before_packing_and_installer_after():   # plan 3b Task 5
    rel = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    order = [rel.index(f"- name: {name}") for name in
             ("Build app", "Sign app", "Build installer", "Sign installer", "Checksums", "Publish")]
    assert order == sorted(order)
    steps = _steps(rel)
    assert "build.ps1 -InstallerOnly" in steps["Build installer"]
    assert "output-artifact-directory: dist/RemindersWidget" in steps["Sign app"]
    assert "output-artifact-directory: packaging/Output" in steps["Sign installer"]


def test_signing_runs_only_when_configured():   # Review Focus 3
    steps = _steps((WORKFLOWS / "release.yml").read_text(encoding="utf-8"))
    signing = [body for name, body in steps.items() if "signpath" in body.lower() or name.startswith("Upload")]
    assert len(signing) == 5                                  # 2 uploads, 2 signing requests, the signature check
    for body in signing:
        assert "if: vars.SIGNPATH_ORGANIZATION_ID != ''" in body
    for name in ("Sign app", "Sign installer"):
        body = steps[name]
        assert "uses: signpath/github-action-submit-signing-request@v2" in body
        assert "api-token: ${{ secrets.SIGNPATH_API_TOKEN }}" in body and "wait-for-completion: true" in body


def test_signing_failure_stops_the_release():   # Review Focus 4
    rel = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "continue-on-error" not in rel


def test_build_script_can_pack_an_already_built_app():
    ps1 = (ROOT / "packaging" / "build.ps1").read_text(encoding="utf-8")
    assert "[switch]$InstallerOnly" in ps1 and "if (-not $InstallerOnly)" in ps1



def test_ci_compiles_the_installer_on_every_push():   # plan 3b final review 4
    ci = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "installer:" in ci and "build.ps1 -Installer" in ci and "innosetup" in ci


def test_release_checks_signatures_before_publishing():   # final review 5
    rel = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    steps = _steps(rel)
    check = steps["Check signatures"]
    assert "if: vars.SIGNPATH_ORGANIZATION_ID != ''" in check
    assert "Get-AuthenticodeSignature" in check and "SignPath Foundation" in check and "Valid" in check
    assert rel.index("- name: Check signatures") < rel.index("- name: Checksums")


def test_signing_job_cannot_write_to_the_repository():   # final review 6
    rel = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    build, publish = rel.split("\n  publish:\n")
    assert "contents: read" in build and "contents: write" not in build
    assert "contents: write" in publish and "needs: build" in publish
    assert rel.count("retention-days: 1") >= 2                # unsigned uploads do not linger
