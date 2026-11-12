"""Name, version and GitHub repository of Reminders Widget — the one place they are written."""
NAME = "Reminders Widget"
VERSION = "0.1.0"
REPO = "oktamko/reminders_widget"


def releases_url() -> str:
    return f"https://github.com/{REPO}/releases"


def repo_configured() -> bool:
    """False while REPO is still the placeholder: no update request goes out then."""
    return not REPO.startswith("OWNER/")


def is_release_url(url) -> bool:
    """A page under this project's releases. GitHub owner and repo names ignore case."""
    return isinstance(url, str) and url.lower().startswith(releases_url().lower() + "/")
