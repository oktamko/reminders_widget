class UnknownFormat(Exception):
    """A synced value has a shape this widget does not recognise, so it must not be rewritten."""


class OfflineError(Exception):
    """The network request could not be made."""


class SessionExpired(Exception):
    """iCloud rejected the session (HTTP 401 or 421, or AUTHENTICATION_REQUIRED /
    AUTHENTICATION_FAILED at any status); sign in again."""


class Transient(Exception):
    """iCloud is busy or failing for now (HTTP 429 or 5xx, or THROTTLED, TRY_AGAIN_LATER,
    INTERNAL_ERROR, SERVICE_UNAVAILABLE); try again later.

    ``retry_after`` is the wait in seconds the server asked for, or None.
    """

    def __init__(self, message: str = "", retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class LockConflict(Exception):
    """The record changed on the server since it was read, or a create found it existing.

    ``code`` is the server error code: "EXISTS" (or "CONFLICT") answers a create of a
    record that already exists; "OP_LOCK_FAILURE" answers a write with a stale change tag.
    """

    def __init__(self, code: str = "OP_LOCK_FAILURE"):
        super().__init__(code)
        self.code = code


class RecordGone(Exception):
    """The record does not exist on the server (NOT_FOUND)."""


class UnknownResponse(Exception):
    """The server answered with a status or body shape this client does not understand."""
