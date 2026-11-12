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
