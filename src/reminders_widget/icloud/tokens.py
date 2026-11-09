"""Advance the ResolutionTokenMap entries that a PC-side field change must bump."""
import json
from collections.abc import Iterable

from reminders_widget.icloud.errors import UnknownFormat

APPLE_EPOCH_OFFSET_S = 978307200  # token times count seconds from 2001-01-01


def apple_now(unix_time: float) -> float:
    """Convert a Unix timestamp to Apple's reference time (seconds since 2001-01-01)."""
    return unix_time - APPLE_EPOCH_OFFSET_S


def _number(token: dict, field: str, key: str) -> float:
    """Read a numeric token field; a missing field counts as 0, any other non-number is refused."""
    value = token.get(field, 0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnknownFormat(f"ResolutionTokenMap entry {key!r} has a non-numeric {field}")
    return value


def advance_tokens(raw: str | None, keys: Iterable[str], now: float, replica_id: str) -> str:
    """Return the map JSON with each key's token advanced, or created if missing.

    A None input starts from an empty map. Raises UnknownFormat for text that is not JSON,
    JSON without a "map" object, or a token that is not an object. Nothing is mutated in place.
    """
    if raw is None:
        data = {"map": {}}
    else:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise UnknownFormat("ResolutionTokenMap is not JSON") from exc
    entries = data.get("map") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        raise UnknownFormat('ResolutionTokenMap has no "map" object')

    for key in dict.fromkeys(keys):  # a repeated key advances once
        token = entries.get(key)
        if token is None:
            entries[key] = {"counter": 1, "modificationTime": now, "replicaID": replica_id}
        elif isinstance(token, dict):
            counter = _number(token, "counter", key)
            modified = _number(token, "modificationTime", key)
            token["counter"] = counter + 1
            token["modificationTime"] = max(modified + 1, now)
            token.setdefault("replicaID", replica_id)
        else:
            raise UnknownFormat(f"ResolutionTokenMap entry {key!r} is not an object")
    return json.dumps(data)
