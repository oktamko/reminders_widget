"""README, license and privacy notes say what the spec (§8) requires."""
import re
from pathlib import Path

from reminders_widget.project import REPO

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_has_the_required_sections():
    headings = set(re.findall(r"^##\s+(.+?)\s*$", README, re.M))
    assert {"What it does", "Not affiliated with Apple", "It can break", "Install",
            "What stays on your PC", "Build from source", "License"} <= headings
    install = README[README.index("## Install"):]
    assert "More info" in install and "Run anyway" in install          # SmartScreen, unsigned installer


def test_readme_links_use_the_project_repo():
    links = re.findall(r"https://github\.com/[^\s)\]]+", README)
    assert links and all(f"github.com/{REPO}" in link for link in links)


def test_readme_links_the_privacy_notes():
    assert "docs/privacy.md" in README and (ROOT / "docs" / "privacy.md").is_file()


def test_license_is_mit():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License") and "Permission is hereby granted, free of charge" in text


SIGNPATH = "Free code signing provided by SignPath.io, certificate by SignPath Foundation."


def _policy_ok(text):
    return SIGNPATH in text and "oktamko" in text and "docs/privacy.md" in text and \
        all(role in text for role in ("Author", "Reviewer", "Approver"))


def test_readme_has_a_code_signing_policy():   # plan 3b Task 4 (SignPath condition)
    section = README[README.index("## Code signing policy"):]
    section = section[:section.find("\n## ", 5) if "\n## " in section[5:] else len(section)]
    assert _policy_ok(section)


def test_release_notes_carry_the_policy():
    notes = (ROOT / ".github" / "release-notes.md").read_text(encoding="utf-8")
    assert "Code signing policy" in notes and _policy_ok(notes)
    rel = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "--notes-file .github/release-notes.md" in rel and "--generate-notes" in rel


def test_readme_explains_smart_app_control():
    install = README[README.index("## Install"):README.index("## What stays on your PC")]
    assert "Smart App Control" in install and "signed" in install
