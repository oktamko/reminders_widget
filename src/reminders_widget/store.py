"""Local SQLite cache of reminders plus a durable queue of pending local changes."""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from .icloud.records import Reminder, ReminderList

_SCHEMA = """
CREATE TABLE IF NOT EXISTS lists (
    record_name TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT
);
CREATE TABLE IF NOT EXISTS reminders (
    record_name TEXT PRIMARY KEY,
    list_id TEXT NOT NULL,
    title TEXT,
    notes TEXT,
    completed INTEGER NOT NULL,
    priority INTEGER NOT NULL,
    due TEXT,
    deleted INTEGER NOT NULL,
    change_tag TEXT NOT NULL,
    readonly INTEGER NOT NULL,
    created TEXT
);
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_name TEXT NOT NULL,
    op TEXT NOT NULL,
    changes TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS queue_record ON queue(record_name);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_OPS = ("create", "update", "delete")


@dataclass(frozen=True)
class QueueEntry:
    id: int
    record_name: str
    op: Literal["create", "update", "delete"]
    changes: dict[str, object]


def _enc_due(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return json.dumps(["dt", value.isoformat()])
    return json.dumps(["d", value.isoformat()])


def _dec_due(text: str | None) -> datetime | date | None:
    if text is None:
        return None
    kind, iso = json.loads(text)
    return datetime.fromisoformat(iso) if kind == "dt" else date.fromisoformat(iso)


def _enc_changes(changes: dict[str, object]) -> str:
    out = dict(changes)
    if "due" in out:
        out["due"] = _enc_due(out["due"])  # type: ignore[arg-type]
    return json.dumps(out)


def _dec_changes(text: str) -> dict[str, object]:
    out = json.loads(text)
    if "due" in out:
        out["due"] = _dec_due(out["due"])
    return out


def _enc_dt(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _row_to_reminder(row: sqlite3.Row | tuple) -> Reminder:
    (record_name, list_id, title, notes, completed, priority, due, deleted,
     change_tag, readonly, created) = row
    return Reminder(
        record_name=record_name, list_id=list_id, title=title, notes=notes,
        completed=bool(completed), priority=priority, due=_dec_due(due),
        deleted=bool(deleted), change_tag=change_tag, readonly=bool(readonly),
        created=None if created is None else datetime.fromisoformat(created),
    )


_COLUMNS = ("record_name, list_id, title, notes, completed, priority, due, deleted, "
            "change_tag, readonly, created")


def _apply_changes(r: Reminder, changes: dict[str, object]) -> Reminder:
    fields: dict[str, object] = {}
    for key, value in changes.items():
        if key in ("title", "notes", "due", "list_id"):
            fields[key] = value
        elif key in ("completed", "deleted"):
            fields[key] = bool(value)
        elif key == "priority":
            fields[key] = int(value)  # type: ignore[call-overload]
    return replace(r, **fields)


class Store:
    def __init__(self, path: Path | str, *, now: Callable[[], float] = time.time):
        self._now = now
        target = str(path)
        self._db = sqlite3.connect(target, isolation_level=None)
        if target != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _tx(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        else:
            self._db.execute("COMMIT")

    # -- reads -------------------------------------------------------------

    def lists(self) -> list[ReminderList]:
        rows = self._db.execute("SELECT record_name, name, color FROM lists ORDER BY rowid")
        return [ReminderList(*row) for row in rows]

    def reminders(self) -> list[Reminder]:
        rows = self._db.execute(
            f"SELECT {_COLUMNS} FROM reminders WHERE deleted = 0 AND completed = 0 ORDER BY rowid"
        )
        return [_row_to_reminder(row) for row in rows]

    def _get(self, db, record_name: str) -> Reminder | None:
        row = db.execute(
            f"SELECT {_COLUMNS} FROM reminders WHERE record_name = ?", (record_name,)
        ).fetchone()
        return None if row is None else _row_to_reminder(row)

    def _put(self, db, r: Reminder) -> None:
        db.execute(
            f"INSERT OR REPLACE INTO reminders ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (r.record_name, r.list_id, r.title, r.notes, int(r.completed), r.priority,
             _enc_due(r.due), int(r.deleted), r.change_tag, int(r.readonly), _enc_dt(r.created)),
        )

    # -- remote ------------------------------------------------------------

    def save_remote(self, items: list[Reminder | ReminderList], sync_token: str) -> None:
        with self._tx() as db:
            for item in items:
                if isinstance(item, ReminderList):
                    db.execute(
                        "INSERT OR REPLACE INTO lists (record_name, name, color) VALUES (?,?,?)",
                        (item.record_name, item.name, item.color),
                    )
                elif item.deleted:
                    db.execute("DELETE FROM reminders WHERE record_name = ?", (item.record_name,))
                else:
                    merged = item
                    for _, op, changes in self._queued(db, item.record_name):
                        merged = _apply_changes(merged, changes)
                    self._put(db, merged)
            db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('sync_token', ?)", (sync_token,)
            )

    def sync_token(self) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = 'sync_token'").fetchone()
        return None if row is None else row[0]

    # -- queue -------------------------------------------------------------

    def _queued(self, db, record_name: str) -> list[tuple[int, str, dict[str, object]]]:
        rows = db.execute(
            "SELECT id, op, changes FROM queue WHERE record_name = ? ORDER BY id", (record_name,)
        ).fetchall()
        return [(i, op, _dec_changes(ch)) for i, op, ch in rows]

    def apply_local(self, record_name: str, op: str, changes: dict) -> None:
        if op not in _OPS:
            raise ValueError(f"unknown op {op!r}")
        with self._tx() as db:
            row = self._get(db, record_name)
            if row is not None and row.readonly:
                raise ValueError("reminder is read-only")
            if op == "delete":
                changes = {**changes, "deleted": True}
            queued = self._queued(db, record_name)
            if queued:
                entry_id, q_op, q_changes = queued[0]
            else:
                entry_id, q_op, q_changes = None, None, {}

            if q_op == "create" and op == "delete":
                db.execute("DELETE FROM queue WHERE record_name = ?", (record_name,))
                db.execute("DELETE FROM reminders WHERE record_name = ?", (record_name,))
                return

            if entry_id is None:
                db.execute(
                    "INSERT INTO queue (record_name, op, changes) VALUES (?,?,?)",
                    (record_name, op, _enc_changes(changes)),
                )
            elif op == "delete":
                db.execute(
                    "UPDATE queue SET op = 'delete', changes = ? WHERE id = ?",
                    (_enc_changes({"deleted": True}), entry_id),
                )
            else:
                # create stays create, update stays update; edits after a queued
                # delete cannot revive the record, so the delete is kept as is
                new_changes = q_changes if q_op == "delete" else {**q_changes, **changes}
                db.execute(
                    "UPDATE queue SET changes = ? WHERE id = ?",
                    (_enc_changes(new_changes), entry_id),
                )

            if row is None and op == "create":
                row = Reminder(
                    record_name=record_name, list_id="", title=None, notes=None,
                    completed=False, priority=0, due=None, deleted=False, change_tag="",
                    readonly=False,
                    created=datetime.fromtimestamp(self._now(), timezone.utc),
                )
            if row is not None:
                self._put(db, _apply_changes(row, changes))

    def pending(self) -> list[QueueEntry]:
        rows = self._db.execute("SELECT id, record_name, op, changes FROM queue ORDER BY id")
        return [QueueEntry(i, name, op, _dec_changes(ch)) for i, name, op, ch in rows]

    def pending_keys(self, record_name: str) -> set[str]:
        keys: set[str] = set()
        for _, _, changes in self._queued(self._db, record_name):
            keys.update(changes)
        return keys

    def ack(self, entry_id: int) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM queue WHERE id = ?", (entry_id,))

    def drop(self, record_name: str) -> None:
        with self._tx() as db:
            db.execute("DELETE FROM queue WHERE record_name = ?", (record_name,))

    # -- settings ----------------------------------------------------------

    def get_setting(self, key: str, default: object) -> object:
        row = self._db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])

    def set_setting(self, key: str, value: object) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, json.dumps(value)),
            )
