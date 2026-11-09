class UnknownFormat(Exception):
    """A synced value has a shape this widget does not recognise, so it must not be rewritten."""


class OfflineError(Exception):
    """The network request could not be made."""


class SessionExpired(Exception):
    """iCloud rejected the session (HTTP 401 or 421); sign in again."""


class LockConflict(Exception):
    """The record changed on the server since it was read (OP_LOCK_FAILURE or CONFLICT)."""


class RecordGone(Exception):
    """The record does not exist on the server (NOT_FOUND)."""


class UnknownResponse(Exception):
    """The server answered with a status or body shape this client does not understand."""
