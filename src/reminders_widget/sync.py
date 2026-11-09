"""Sync engine: pushes queued local changes to iCloud, then pulls remote changes.

This is the only code that writes to iCloud. Every write starts from a fresh lookup,
never touches a reminder whose fields could not be decoded, and an entry that is
being sent is always either acked, dropped or released before the run ends.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .icloud.cloudkit import CloudKit
from .icloud.errors import (
    LockConflict, OfflineError, RecordGone, SessionExpired, UnknownFormat, UnknownResponse,
)
from .icloud.records import Reminder, ReminderList, parse_record, to_cloud_fields
from .icloud.tokens import advance_tokens, apple_now
from .store import QueueEntry, Store

log = logging.getLogger("reminders_widget.sync")

NOTICE_GONE = "A reminder you changed was deleted on another device."
NOTICE_READONLY = "This reminder can't be changed on PC yet."

LOCK_RETRIES = 3          # after the first attempt
STOP_AFTER_UNKNOWN = 3    # consecutive runs ending in UnknownResponse
_DELAYS = (15, 30, 60, 120, 300)

Status = Literal["ok", "offline", "session_expired", "error", "stopped"]


@dataclass(frozen=True)
class SyncResult:
    # "error": this run ended in an UnknownResponse; retry with backoff
    status: Status
    notices: list[str] = field(default_factory=list)


def next_delay(failures: int) -> int:
    """Seconds to wait before the next run after ``failures`` consecutive failed runs."""
    return _DELAYS[min(max(failures, 0), len(_DELAYS) - 1)]


def _h(record_name: str) -> str:
    return hashlib.sha256(record_name.encode("utf-8")).hexdigest()[:8]


class _Stop(Exception):
    """Pushing stops for this run; the entry was released and the pull still runs."""


class _Skip(Exception):
    """The entry was released and waits for a later run; pushing continues."""


def replica_uuid(store: Store) -> bytes:
    """This PC's CRDT replica uuid: 16 random bytes created once, kept as hex in settings."""
    raw = store.get_setting("replica_uuid", None)
    if isinstance(raw, str):
        try:
            value = bytes.fromhex(raw)
        except ValueError:
            value = b""
        if len(value) == 16:
            return value
        log.warning("replica_uuid setting unreadable; creating a new one")
    value = secrets.token_bytes(16)
    store.set_setting("replica_uuid", value.hex())
    return value


class SyncEngine:
    def __init__(self, cloud: CloudKit, store: Store, clock: Callable[[], float] = time.time):
        self._cloud = cloud
        self._store = store
        self._clock = clock
        self._uuid = replica_uuid(store)
        self._replica_id = str(uuid.UUID(bytes=self._uuid)).upper()
        self._unknown_runs = 0
        self._stopped = False
        self._run_lock = threading.Lock()

    # -- run ---------------------------------------------------------------

    def run_once(self) -> SyncResult:
        """Push queued changes, then pull remote changes."""
        with self._run_lock:
            if self._stopped:
                log.info("sync run skipped: stopped")
                return SyncResult("stopped", [])
            notices: list[str] = []
            try:
                self._push(notices)
                self._pull()
            except OfflineError:
                return self._finish("offline", notices)
            except SessionExpired:
                return self._finish("session_expired", notices)
            except UnknownResponse:
                self._unknown_runs += 1
                if self._unknown_runs >= STOP_AFTER_UNKNOWN:
                    self._stopped = True
                    return self._finish("stopped", notices, keep_count=True)
                return self._finish("error", notices, keep_count=True)
            return self._finish("ok", notices)

    def _finish(self, status: Status, notices: list[str], keep_count: bool = False) -> SyncResult:
        if not keep_count:
            self._unknown_runs = 0
        unique = list(dict.fromkeys(notices))
        log.info("sync run finished: status=%s notices=%d", status, len(unique))
        return SyncResult(status, unique)

    # -- push --------------------------------------------------------------

    def _push(self, notices: list[str]) -> None:
        # Entries queued while this run is sending wait for the next run.
        entries = self._store.pending()
        sent = 0
        for entry in entries:
            try:
                snap = self._store.begin_send(entry.id)
            except KeyError:
                continue  # dropped or merged since pending() was read
            settled = False
            try:
                self._send(snap, notices)
                settled = True
                sent += 1
            except _Skip:
                settled = True  # _send released it
            except _Stop:
                settled = True  # _send released it
                log.info("push stopped after lock conflicts: %s", _h(snap.record_name))
                break
            except Exception as exc:
                log.info("push failed: %s %s", type(exc).__name__, _h(snap.record_name))
                raise
            finally:
                if not settled:
                    self._store.release(snap.id)
        log.info("push: queued=%d settled=%d", len(entries), sent)

    def _send(self, snap: QueueEntry, notices: list[str]) -> None:
        if snap.op == "create":
            self._send_create(snap)
        else:
            self._send_change(snap, notices)

    def _send_create(self, snap: QueueEntry) -> None:
        fields, _ = to_cloud_fields(snap.changes, {}, self._uuid, now=self._clock)
        fields.setdefault("Completed", {"value": 0})
        if "List" not in fields:
            lists = self._store.lists()
            if not lists:
                log.warning("create waits for a list: %s", _h(snap.record_name))
                self._store.release(snap.id)
                raise _Skip
            fields["List"] = {"value": {"recordName": lists[0].record_name, "action": "NONE"}}
        operation = {"operationType": "create", "record": {
            "recordName": snap.record_name, "recordType": "Reminder", "fields": fields}}
        try:
            self._cloud.modify(operation)
            log.debug("created %s", _h(snap.record_name))
        except LockConflict:
            # the record already exists: an earlier send of this create landed
            log.info("create already applied: %s", _h(snap.record_name))
        self._store.ack(snap.id)

    def _send_change(self, snap: QueueEntry, notices: list[str]) -> None:
        name = snap.record_name
        is_delete = snap.op == "delete"
        for attempt in range(1 + LOCK_RETRIES):
            try:
                current = self._cloud.lookup(name)
            except RecordGone:
                return self._gone(snap, notices)
            parsed = self._parse(current)
            if isinstance(parsed, Reminder) and parsed.deleted:
                return self._gone(snap, notices)
            if not isinstance(parsed, Reminder) or parsed.readonly:
                return self._readonly(snap, notices)
            try:
                operation = self._update_operation(snap, current)
            except UnknownFormat:
                return self._readonly(snap, notices)
            try:
                self._cloud.modify(operation)
            except LockConflict:
                log.info("lock conflict %d: %s", attempt + 1, _h(name))
                continue
            except RecordGone:
                return self._gone(snap, notices)
            self._store.ack(snap.id)
            log.debug("%s sent: %s", "delete" if is_delete else "update", _h(name))
            return
        self._store.release(snap.id)
        raise _Stop

    def _update_operation(self, snap: QueueEntry, current: dict) -> dict:
        cur_fields = current.get("fields") or {}
        fields, keys = to_cloud_fields(snap.changes, cur_fields, self._uuid, now=self._clock)
        if keys:
            token_field = cur_fields.get("ResolutionTokenMap")
            if token_field is None:
                raw = None
            elif isinstance(token_field, dict):
                raw = token_field.get("value")
            else:
                raise UnknownFormat("ResolutionTokenMap field has an unknown shape")
            fields["ResolutionTokenMap"] = {
                "value": advance_tokens(raw, keys, apple_now(self._clock()), self._replica_id),
                "type": "STRING",
            }
        return {"operationType": "update", "record": {
            "recordName": snap.record_name, "recordType": "Reminder",
            "recordChangeTag": current.get("recordChangeTag"), "fields": fields}}

    def _gone(self, snap: QueueEntry, notices: list[str]) -> None:
        if snap.op == "delete":
            self._store.ack(snap.id)
            log.debug("delete already applied: %s", _h(snap.record_name))
            return
        self._store.drop(snap.record_name)
        notices.append(NOTICE_GONE)
        log.info("dropped change, record deleted remotely: %s", _h(snap.record_name))

    def _readonly(self, snap: QueueEntry, notices: list[str]) -> None:
        self._store.drop(snap.record_name)
        notices.append(NOTICE_READONLY)
        log.info("dropped change, record read-only: %s", _h(snap.record_name))

    # -- pull --------------------------------------------------------------

    def _parse(self, rec: object) -> Reminder | ReminderList | None:
        if not isinstance(rec, dict):
            raise UnknownResponse("record is not an object")
        try:
            return parse_record(rec, self._uuid)
        except (KeyError, TypeError, AttributeError):
            raise UnknownResponse("malformed record") from None

    def _pull(self) -> None:
        records, token = self._cloud.changes(self._store.sync_token())
        items: list[Reminder | ReminderList] = []
        tombstones = 0
        for rec in records:
            if isinstance(rec, dict) and rec.get("deleted") is True and not rec.get("fields"):
                name = rec.get("recordName")
                if not isinstance(name, str):
                    raise UnknownResponse("tombstone without recordName")
                items.append(_tombstone(name))
                tombstones += 1
                continue
            parsed = self._parse(rec)
            if parsed is not None:
                items.append(parsed)
        self._store.save_remote(items, token)  # type: ignore[arg-type]
        log.info("pull: records=%d saved=%d tombstones=%d", len(records), len(items), tombstones)


def _tombstone(record_name: str) -> Reminder:
    return Reminder(record_name=record_name, list_id="", title=None, notes=None,
                    completed=False, priority=0, due=None, deleted=True, change_tag="",
                    readonly=True, created=None)
