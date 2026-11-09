"""CloudKit record <-> domain model for reminders and lists."""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .crdt import check_editable, decode_text, edit_text, encode_text
from .errors import UnknownFormat

# change key -> (CloudKit field names, ResolutionTokenMap keys to advance)
FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "title": (("TitleDocument",), ("titleDocument",)),
    "notes": (("NotesDocument",), ("notesDocument",)),
    "completed": (("Completed", "CompletionDate"), ("completed",)),
    "priority": (("Priority",), ("priority",)),
    "due": (("DueDate", "AllDay"), ("dueDate", "allDay")),
    "list_id": (("List",), ("list",)),
    "deleted": (("Deleted",), ()),
}

_HEX = re.compile(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?\Z")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class ReminderList:
    record_name: str
    name: str
    color: str | None
    deleted: bool = False


@dataclass(frozen=True)
class Reminder:
    record_name: str
    list_id: str
    title: str | None
    notes: str | None
    completed: bool
    priority: int
    due: datetime | date | None   # date for all-day reminders
    deleted: bool
    change_tag: str
    readonly: bool
    created: datetime | None


def _val(fields: dict, name: str, default=None):
    field = fields.get(name)
    if not isinstance(field, dict):
        return default  # unknown shape: ignored for scalars
    value = field.get("value")
    return default if value is None else value


def _from_ms(ms) -> datetime | None:
    try:
        return datetime.fromtimestamp(ms / 1000, timezone.utc)
    except (OSError, OverflowError, ValueError, TypeError):
        return None


def _document(fields: dict, name: str, replica_uuid: bytes | None = None) -> tuple[str | None, bool]:
    """Return (text, failed). A missing field is not a failure."""
    if name not in fields:
        return None, False
    field = fields[name]
    b64 = field.get("value") if isinstance(field, dict) else None
    if not isinstance(b64, str):
        return None, True  # present but null / unknown shape: never write it
    try:
        check_editable(b64, replica_uuid)
    except UnknownFormat:
        return None, True
    text = decode_text(b64)
    return text, text is None


def _parse_list(rec: dict) -> ReminderList:
    fields = rec.get("fields") or {}
    name = _val(fields, "Name")
    if name is None:
        name, _ = _document(fields, "TitleDocument")
    color = _val(fields, "Color")
    if not (isinstance(color, str) and _HEX.match(color)):
        color = None
    return ReminderList(rec["recordName"], name or "", color, bool(_val(fields, "Deleted", 0)))


def _parse_reminder(rec: dict, replica_uuid: bytes | None = None) -> Reminder:
    fields = rec.get("fields") or {}
    title, title_bad = _document(fields, "TitleDocument", replica_uuid)
    notes, notes_bad = _document(fields, "NotesDocument", replica_uuid)
    if title is None and not title_bad:
        title = ""
    odd = False
    due: datetime | date | None = None
    due_ms = _val(fields, "DueDate")
    if due_ms is not None:
        due = _from_ms(due_ms)
        odd |= due is None
        if due is not None and _val(fields, "AllDay", 0):
            due = due.date()
    created = rec.get("created")
    created_ms = created.get("timestamp") if isinstance(created, dict) else None
    list_id = ""
    list_ref = _val(fields, "List")
    if list_ref is not None:
        ref_name = list_ref.get("recordName") if isinstance(list_ref, dict) else None
        if isinstance(ref_name, str):
            list_id = ref_name
        else:
            odd = True  # a list reference we cannot read: never write this reminder
    change_tag = rec.get("recordChangeTag")
    try:
        priority = int(_val(fields, "Priority", 0))
    except (TypeError, ValueError, OverflowError):
        priority, odd = 0, True
    return Reminder(
        record_name=rec["recordName"],
        list_id=list_id,
        title=title,
        notes=notes,
        completed=bool(_val(fields, "Completed", 0)),
        priority=priority,
        due=due,
        deleted=bool(_val(fields, "Deleted", 0)),
        change_tag=change_tag if isinstance(change_tag, str) else "",
        readonly=title_bad or notes_bad or odd,
        created=_from_ms(created_ms) if created_ms is not None else None,
    )


def parse_record(rec: dict, replica_uuid: bytes | None = None) -> Reminder | ReminderList | None:
    kind = rec.get("recordType")
    if kind == "Reminder":
        return _parse_reminder(rec, replica_uuid)
    if kind in ("List", "ReminderList"):
        return _parse_list(rec)
    return None


def _document_value(text: str, current: dict, field: str, replica_uuid: bytes) -> str:
    existing = _val(current, field)
    if existing is None:
        return encode_text(text, replica_uuid)
    return edit_text(existing, text, replica_uuid)


def _ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; use a timezone-aware value")
    return round((dt - _EPOCH).total_seconds() * 1000)


def to_cloud_fields(
    changes: dict[str, object],
    current: dict[str, dict],
    replica_uuid: bytes,
    *,
    now: Callable[[], float] = time.time,
) -> tuple[dict[str, dict], list[str]]:
    """Build CloudKit fields for ``changes`` plus the token keys to advance.

    ``current`` is the freshly looked-up record's ``fields``. Unknown change keys
    raise KeyError; UnknownFormat from a CRDT edit propagates to the caller.
    """
    out: dict[str, dict] = {}
    keys: list[str] = []
    for key, value in changes.items():
        _, token_keys = FIELDS[key]
        if key == "title":
            out["TitleDocument"] = {"value": _document_value(value, current, "TitleDocument", replica_uuid)}
        elif key == "notes":
            out["NotesDocument"] = {"value": _document_value(value, current, "NotesDocument", replica_uuid)}
        elif key == "completed":
            out["Completed"] = {"value": 1 if value else 0}
            out["CompletionDate"] = {"value": round(now() * 1000) if value else None}
        elif key == "priority":
            out["Priority"] = {"value": value}
        elif key == "due":
            if value is None:
                out["DueDate"], out["AllDay"] = {"value": None}, {"value": 0}
            elif isinstance(value, datetime):
                out["DueDate"], out["AllDay"] = {"value": _ms(value)}, {"value": 0}
            else:
                midnight = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
                out["DueDate"], out["AllDay"] = {"value": _ms(midnight)}, {"value": 1}
        elif key == "list_id":
            out["List"] = {"value": {"recordName": value, "action": "NONE"}}
        elif key == "deleted":
            out["Deleted"] = {"value": 1 if value else 0}
        keys.extend(k for k in token_keys if k not in keys)
    return out, keys
