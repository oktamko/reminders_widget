"""Sync engine: pushes queued local changes to iCloud, then pulls remote changes.

This is the only code that writes to iCloud. Every write starts from a fresh lookup,
never touches a reminder whose fields could not be decoded, and an entry that is
being sent is always either acked, dropped or released before the run ends.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .icloud.cloudkit import CloudKit
from .icloud.errors import (
    LockConflict, OfflineError, RecordGone, SessionExpired, Transient, UnknownFormat,
    UnknownResponse,
)
from .icloud.records import FIELDS, Reminder, ReminderList, parse_record, to_cloud_fields
from .icloud.tokens import advance_tokens, apple_now
from .store import QueueEntry, Store

log = logging.getLogger("reminders_widget.sync")

NOTICE_GONE = "A reminder you changed was deleted on another device."
NOTICE_READONLY = "This reminder can't be changed on PC yet."
NOTICE_DISCARDED = "A change couldn't be sent and was discarded."
NOTICE_NOT_ADDED = "A reminder couldn't be added to iCloud."

LOCK_RETRIES = 3          # after the first attempt
STOP_AFTER_UNKNOWN = 3    # consecutive runs ending in UnknownResponse or another error
MAX_CREATE_ATTEMPTS = 5   # refused creates of one reminder before it is given up
_DELAYS = (15, 30, 60, 120, 300)
_ZERO_DEFAULT = frozenset({"AllDay", "Completed", "Priority", "Deleted"})
# what a queued value the encoder cannot handle raises (store validation keeps new
# entries out; this guards entries queued by an older version)
_UNSENDABLE = (ValueError, TypeError, KeyError, AttributeError)

Status = Literal["ok", "offline", "session_expired", "transient", "error", "stopped"]


@dataclass(frozen=True)
class SyncResult:
    """Outcome of one ``run_once``.

    status: "ok" the run finished; "offline" the network was unreachable;
    "session_expired" iCloud wants a new sign-in; "transient" iCloud is busy or
    failing for now (throttled, HTTP 429/5xx) and asked to try later; "error" the
    run ended in a server answer this client does not understand or another
    unexpected failure (retry with backoff); "stopped" three "error" runs in a row
    (runs of other statuses in between neither count nor reset), and this engine no
    longer syncs until it is rebuilt.
    Anything other than "ok" counts as a failure for ``next_delay``.
    notices: user-facing English sentences, without duplicates.
    retry_after: for "transient", the seconds the server asked to wait, or None.
    """
    status: Status
    notices: list[str] = field(default_factory=list)
    retry_after: float | None = None


def next_delay(failures: int) -> int:
    """Seconds to wait before the next run after ``failures`` consecutive failed runs."""
    return _DELAYS[min(max(failures, 0), len(_DELAYS) - 1)]


def retry_delay(result: SyncResult, failures: int = 0) -> float:
    """Seconds to wait after ``result``: ``next_delay(failures)``, or longer when the
    server asked for a longer wait (``result.retry_after``)."""
    base = next_delay(failures)
    if result.retry_after is None:
        return base
    return max(base, result.retry_after)


def _frames(exc: BaseException) -> str:
    """Traceback as ``file:line`` pairs: file names only, no messages, no locals."""
    return " ".join(f"{os.path.basename(f.filename)}:{f.lineno}"
                    for f in traceback.extract_tb(exc.__traceback__))


def _h(record_name: str) -> str:
    return hashlib.sha256(record_name.encode("utf-8")).hexdigest()[:8]


def _same(name: str, ours: dict, server_field: object) -> bool:
    """True when the field value we would send equals what the server already has."""
    if name == "CompletionDate":
        return True  # derived from Completed, which is compared on its own
    server = server_field.get("value") if isinstance(server_field, dict) else None
    mine = ours.get("value")
    if server is None and name in _ZERO_DEFAULT:
        server = 0
    if name == "List":
        return (isinstance(mine, dict) and isinstance(server, dict)
                and mine.get("recordName") == server.get("recordName"))
    return mine == server


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
    def __init__(self, cloud: CloudKit, store: Store, *, clock: Callable[[], float] = time.time):
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
            except Transient as exc:
                # neither counts toward "stopped" nor resets the count
                log.info("sync run deferred: iCloud busy (retry_after=%s)", exc.retry_after)
                return self._finish("transient", notices, keep_count=True,
                                    retry_after=exc.retry_after)
            except Exception as exc:
                # UnknownResponse, or anything unexpected; the message may hold content
                log.warning("sync run failed: %s at %s", type(exc).__name__, _frames(exc))
                self._unknown_runs += 1
                if self._unknown_runs >= STOP_AFTER_UNKNOWN:
                    self._stopped = True
                    return self._finish("stopped", notices, keep_count=True)
                return self._finish("error", notices, keep_count=True)
            return self._finish("ok", notices)

    def _finish(self, status: Status, notices: list[str], keep_count: bool = False,
                retry_after: float | None = None) -> SyncResult:
        if not keep_count:
            self._unknown_runs = 0
        unique = list(dict.fromkeys(notices))
        log.info("sync run finished: status=%s notices=%d", status, len(unique))
        return SyncResult(status, unique, retry_after)

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
                log.info("push stopped for this run: %s", _h(snap.record_name))
                break
            except Exception as exc:
                log.info("push failed: %s %s", type(exc).__name__, _h(snap.record_name))
                raise
            finally:
                if not settled:
                    self._store.release(snap.id)
        log.info("push: queued=%d settled=%d", len(entries), sent)

    def _send(self, snap: QueueEntry, notices: list[str]) -> None:
        unknown = sorted(k for k in snap.changes if k not in FIELDS)
        if unknown:
            # change keys are code constants, never user content
            log.warning("dropped entry with unknown change keys %s: %s", unknown, _h(snap.record_name))
            self._store.ack(snap.id)
            return
        if snap.op == "create":
            self._send_create(snap, notices)
        else:
            self._send_change(snap, notices)

    def _send_create(self, snap: QueueEntry, notices: list[str]) -> None:
        try:
            fields, _ = to_cloud_fields(snap.changes, {}, self._uuid, now=self._clock)
        except _UNSENDABLE as exc:
            return self._discard(snap, notices, exc)
        fields.setdefault("Completed", {"value": 0})
        if "List" not in fields:
            # only entries queued by an older version lack a list; groups never qualify
            lists = self._store.lists(include_groups=False)
            if not lists:
                log.warning("create waits for a list: %s", _h(snap.record_name))
                self._store.release(snap.id)
                raise _Skip
            fields["List"] = {"value": {"recordName": lists[0].record_name, "action": "NONE"}}
        operation = {"operationType": "create", "record": {
            "recordName": snap.record_name, "recordType": "Reminder", "fields": fields}}
        try:
            self._cloud.modify(operation)
        except LockConflict as exc:
            if exc.code not in ("EXISTS", "CONFLICT"):
                return self._create_refused(snap, notices, f"create refused ({exc.code})")
            # The record exists: an earlier send of this create landed, maybe with
            # older values. Send whatever still differs as an update.
            log.info("create already applied, sending differences: %s", _h(snap.record_name))
            self._send_change(snap, notices, create_fallback=True)
            return
        except RecordGone:
            return self._create_refused(snap, notices, "create answered NOT_FOUND")
        self._store.ack(snap.id)
        log.debug("created %s", _h(snap.record_name))

    def _create_refused(self, snap: QueueEntry, notices: list[str], why: str) -> None:
        """Release a refused create and go on with the rest of the queue.

        After MAX_CREATE_ATTEMPTS refusals the reminder is given up: its queue entries
        are dropped and the screen shows what the server has (usually nothing).
        """
        name = snap.record_name
        if snap.attempts + 1 >= MAX_CREATE_ATTEMPTS:
            self._store.drop(name)
            notices.append(NOTICE_NOT_ADDED)
            log.warning("%s %d times, given up: %s", why, snap.attempts + 1, _h(name))
            self._refresh(name)
            raise _Skip
        log.info("%s, retry next run: %s", why, _h(name))
        self._store.release(snap.id, attempt=True)
        raise _Skip

    def _discard(self, snap: QueueEntry, notices: list[str], exc: BaseException,
                 server: dict | None = None) -> None:
        """Drop an entry whose values cannot be encoded; never let it block the queue."""
        name = snap.record_name
        self._store.ack(snap.id)
        notices.append(NOTICE_DISCARDED)
        log.warning("dropped entry that cannot be sent (%s): %s", type(exc).__name__, _h(name))
        if server is None:
            self._refresh(name)
        else:
            parsed = self._parse(server)
            if isinstance(parsed, Reminder):
                self._show_server(parsed)

    def _refresh(self, record_name: str) -> None:
        """Show the server's version of a record after a local change was given up."""
        try:
            current = self._cloud.lookup(record_name)
        except RecordGone:
            self._store.drop(record_name)  # nothing on the server to send changes to
            self._show_server(_tombstone(record_name))
            return
        parsed = self._parse(current)
        if isinstance(parsed, Reminder):
            self._show_server(parsed)

    def _send_change(self, snap: QueueEntry, notices: list[str],
                     create_fallback: bool = False) -> None:
        """Send an update/delete entry, or (create_fallback) a create the server already has.

        In the create fallback only fields that differ from the server are sent, and a
        record that cannot be found is never treated as deleted: the create is retried.
        """
        name = snap.record_name
        for attempt in range(1 + LOCK_RETRIES):
            try:
                current = self._cloud.lookup(name)
            except RecordGone:
                if create_fallback:
                    return self._create_refused(snap, notices, "created record not found")
                return self._gone(snap, notices)
            parsed = self._parse(current)
            if isinstance(parsed, Reminder) and parsed.deleted:
                return self._gone(snap, notices)
            if not isinstance(parsed, Reminder) or parsed.readonly:
                return self._readonly(snap, notices, parsed)
            try:
                operation = self._update_operation(snap, current, only_diff=create_fallback)
            except UnknownFormat:
                return self._readonly(snap, notices, parsed)
            except _UNSENDABLE as exc:
                return self._discard(snap, notices, exc, server=current)
            if not operation["record"]["fields"]:
                self._store.ack(snap.id)
                log.debug("nothing to send: %s", _h(name))
                return
            try:
                self._cloud.modify(operation)
            except LockConflict:
                log.info("lock conflict %d: %s", attempt + 1, _h(name))
                continue
            except RecordGone:
                if create_fallback:
                    return self._create_refused(snap, notices, "created record not found")
                return self._gone(snap, notices)
            self._store.ack(snap.id)
            log.debug("%s sent: %s", snap.op, _h(name))
            return
        if create_fallback:
            return self._create_refused(snap, notices, "create fallback lock conflicts")
        self._store.release(snap.id)
        raise _Stop

    def _update_operation(self, snap: QueueEntry, current: dict, only_diff: bool = False) -> dict:
        cur_fields = current.get("fields") or {}
        if only_diff:
            fields, keys = {}, []
            for key, value in snap.changes.items():
                part, part_keys = to_cloud_fields({key: value}, cur_fields, self._uuid,
                                                  now=self._clock)
                if all(_same(n, v, cur_fields.get(n)) for n, v in part.items()):
                    continue  # the server already has this value
                fields.update(part)
                keys.extend(k for k in part_keys if k not in keys)
        else:
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
        self._show_server(_tombstone(snap.record_name))
        notices.append(NOTICE_GONE)
        log.info("dropped change, record deleted remotely: %s", _h(snap.record_name))

    def _readonly(self, snap: QueueEntry, notices: list[str],
                  server: Reminder | ReminderList | None) -> None:
        self._store.drop(snap.record_name)
        if isinstance(server, Reminder):
            self._show_server(server)
        notices.append(NOTICE_READONLY)
        log.info("dropped change, record read-only: %s", _h(snap.record_name))

    def _show_server(self, item: Reminder) -> None:
        """Replace the cached row with the server's version so a dropped edit stops showing.

        The sync token is left as it is; the next pull may deliver the record again.
        """
        self._store.save_remote([item], self._store.sync_token())  # type: ignore[arg-type]

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
                # the type is unknown: remove a reminder or a list of that name
                items += [_tombstone(name), ReminderList(name, "", None, deleted=True)]
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
