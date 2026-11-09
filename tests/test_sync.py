import hashlib
import json
import logging
import re
import uuid
from datetime import datetime

import pytest

from reminders_widget.icloud.cloudkit import CloudKit
from reminders_widget.icloud.crdt import decode_text, encode_text
from reminders_widget.icloud.tokens import apple_now
from reminders_widget.icloud.records import new_reminder_name
from reminders_widget.store import Store, _enc_changes
from reminders_widget.sync import SyncEngine, SyncResult, next_delay, retry_delay
from tests.fakes.fake_cloudkit import FakeCloudKit

NOW = 1_800_000_000.0
GONE = "A reminder you changed was deleted on another device."
READONLY = "This reminder can't be changed on PC yet."
LIST = "List/L1"
IPHONE_ID = "C20AA380-F3E5-4F0A-BDF1-C454606DD15E"
DISCARDED = "A change couldn't be sent and was discarded."
NOT_ADDED = "A reminder couldn't be added to iCloud."
NEW = new_reminder_name()


def rem(name, title="Buy milk", notes=None, token_map=None, **fields):
    out = {"TitleDocument": {"value": encode_text(title)},
           "Completed": {"value": 0}, "Priority": {"value": 0},
           "List": {"value": {"recordName": LIST, "action": "NONE"}}}
    if notes is not None:
        out["NotesDocument"] = {"value": encode_text(notes)}
    if token_map is not None:
        out["ResolutionTokenMap"] = {"value": json.dumps(token_map), "type": "STRING"}
    out.update({k: {"value": v} for k, v in fields.items()})
    return {"recordName": name, "recordType": "Reminder", "fields": out}


def lst(name=LIST, title="Home"):
    return {"recordName": name, "recordType": "List", "fields": {"Name": {"value": title}}}


@pytest.fixture
def env():
    fake = FakeCloudKit([lst(), rem("R1"), rem("R2", title="Walk dog")])
    store = Store(":memory:", now=lambda: NOW)
    engine = SyncEngine(CloudKit(fake, "https://example.test/"), store, clock=lambda: NOW)
    yield fake, store, engine
    store.close()


def synced(env):
    fake, store, engine = env
    assert engine.run_once() == SyncResult("ok", [])
    fake.writes.clear()
    return fake, store, engine


def updates(fake, name=None):
    return [w for w in fake.writes if w["operationType"] == "update"
            and (name is None or w["record"]["recordName"] == name)]


def by_name(store, name):
    return next((r for r in store.reminders() if r.record_name == name), None)


def replica(store):
    return bytes.fromhex(store.get_setting("replica_uuid", None))


def queue_raw(store, name, op, changes):
    """Queue an entry as an older version could have, past apply_local's checks."""
    store._db.execute("INSERT INTO queue (record_name, op, changes) VALUES (?, ?, ?)",
                      (name, op, _enc_changes(changes)))


def poison(store, name, changes):
    """Replace the changes of the record's queued entry, past apply_local's checks."""
    store._db.execute("UPDATE queue SET changes = ? WHERE record_name = ?",
                      (_enc_changes(changes), name))


def h(name):
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]


# --- push ---------------------------------------------------------------

def test_local_priority_change_reaches_cloud_with_advanced_token(env):
    fake, store, engine = env
    fake.phone_edit("R1", ResolutionTokenMap=json.dumps({"map": {
        "priority": {"counter": 3, "modificationTime": 100.0, "replicaID": IPHONE_ID}}}))
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    assert engine.run_once().status == "ok"
    [w] = updates(fake, "R1")
    fields = w["record"]["fields"]
    assert fields["Priority"]["value"] == 5
    token = json.loads(fields["ResolutionTokenMap"]["value"])["map"]["priority"]
    assert token["counter"] == 4
    assert token["modificationTime"] == apple_now(NOW)
    assert store.pending() == []
    assert by_name(store, "R1").priority == 5


def test_token_map_uses_pc_replica_id(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 1})
    engine.run_once()
    raw = store.get_setting("replica_uuid", None)
    assert isinstance(raw, str) and len(bytes.fromhex(raw)) == 16
    expected = str(uuid.UUID(bytes=bytes.fromhex(raw))).upper()
    [w] = updates(fake, "R1")
    token = json.loads(w["record"]["fields"]["ResolutionTokenMap"]["value"])["map"]["priority"]
    assert token["replicaID"] == expected and token["counter"] == 1


def test_replica_uuid_is_kept_across_engines(env):
    fake, store, engine = synced(env)
    first = store.get_setting("replica_uuid", None)
    SyncEngine(CloudKit(fake, "https://example.test/"), store, clock=lambda: NOW).run_once()
    assert store.get_setting("replica_uuid", None) == first


def test_title_edit_uses_pc_replica_in_document(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"title": "Buy oat milk"})
    engine.run_once()
    [w] = updates(fake, "R1")
    assert decode_text(w["record"]["fields"]["TitleDocument"]["value"]) == "Buy oat milk"
    assert by_name(store, "R1").title == "Buy oat milk"


def test_update_sends_looked_up_change_tag(env):
    fake, store, engine = synced(env)
    fake.phone_edit("R1", Flagged=1)  # server tag moves on after our last pull
    tag = fake._records["R1"]["recordChangeTag"]
    store.apply_local("R1", "update", {"priority": 9})
    engine.run_once()
    [w] = updates(fake, "R1")
    assert w["record"]["recordChangeTag"] == tag


def test_create_sends_one_create_with_defaults_and_no_token_map(env):
    fake, store, engine = synced(env)
    store.apply_local(NEW, "create", {"title": "Call mum", "list_id": LIST})
    assert engine.run_once().status == "ok"
    [w] = fake.writes
    assert w["operationType"] == "create"
    rec = w["record"]
    assert rec["recordName"] == NEW and rec["recordType"] == "Reminder"
    assert decode_text(rec["fields"]["TitleDocument"]["value"]) == "Call mum"
    assert rec["fields"]["Completed"]["value"] == 0
    assert rec["fields"]["List"]["value"]["recordName"] == LIST
    assert "ResolutionTokenMap" not in rec["fields"]
    assert store.pending() == []
    assert by_name(store, NEW).title == "Call mum"


def test_create_then_edit_before_sync_sends_one_merged_create(env):
    fake, store, engine = synced(env)
    store.apply_local(NEW, "create", {"title": "Call mum", "list_id": LIST})
    store.apply_local(NEW, "update", {"priority": 5})
    engine.run_once()
    assert len(fake.writes) == 1
    [w] = fake.writes
    assert w["operationType"] == "create"
    assert w["record"]["fields"]["Priority"]["value"] == 5
    assert decode_text(w["record"]["fields"]["TitleDocument"]["value"]) == "Call mum"


def _lose_first_create_response(fake, during=None):
    """The first create lands on the fake, but the client sees a network failure."""
    from reminders_widget.icloud.errors import OfflineError
    orig = fake.post
    fired = []

    def post(url, body):
        out = orig(url, body)
        if (url.endswith("records/modify") and not fired
                and body["operations"][0]["operationType"] == "create"):
            fired.append(1)
            if during:
                during()
            raise OfflineError("response lost")
        return out

    fake.post = post


@pytest.mark.parametrize("code", ["EXISTS", "CONFLICT"])
def test_create_conflict_after_lost_response_sends_later_edit(env, code):
    fake, store, engine = synced(env)
    fake.duplicate_create_code = code
    store.apply_local(NEW, "create", {"title": "Call mum", "list_id": LIST})
    _lose_first_create_response(
        fake, during=lambda: store.apply_local(NEW, "update", {"title": "Call dad"}))
    assert engine.run_once().status == "offline"
    assert [(e.op, e.in_flight) for e in store.pending()] == [("create", False)]
    assert engine.run_once().status == "ok"
    ops = [w["operationType"] for w in fake.writes]
    assert ops == ["create", "create", "update"]
    upd = fake.writes[-1]["record"]["fields"]
    assert set(upd) == {"TitleDocument", "ResolutionTokenMap"}  # list_id already equal
    assert decode_text(fake._records[NEW]["fields"]["TitleDocument"]["value"]) == "Call dad"
    token = json.loads(upd["ResolutionTokenMap"]["value"])["map"]
    assert set(token) == {"titleDocument"}
    assert store.pending() == []
    assert by_name(store, NEW).title == "Call dad"


@pytest.mark.parametrize("code", ["EXISTS", "CONFLICT"])
def test_duplicate_create_without_edits_writes_nothing_more(env, code):
    fake, store, engine = synced(env)
    fake.duplicate_create_code = code
    store.apply_local(NEW, "create", {"title": "Call mum", "list_id": LIST,
                                         "priority": 5, "completed": False})
    _lose_first_create_response(fake)
    assert engine.run_once().status == "offline"
    result = engine.run_once()
    assert result == SyncResult("ok", [])
    assert [w["operationType"] for w in fake.writes] == ["create", "create"]
    assert store.pending() == []
    assert by_name(store, NEW).title == "Call mum"


@pytest.mark.parametrize("code", ["EXISTS", "CONFLICT"])
def test_create_exists_then_not_found_skips_create_until_next_run(env, code):
    fake, store, engine = synced(env)
    store.apply_local(NEW, "create", {"title": "Call mum", "list_id": LIST})
    store.apply_local("R1", "update", {"priority": 5})
    fake.fail_next = [code]  # the fake does not store it, so lookup finds nothing
    result = engine.run_once()
    assert result == SyncResult("ok", [])
    assert [w["operationType"] for w in fake.writes] == ["create", "update"]  # R1 still sent
    assert [(e.op, e.in_flight, e.attempts) for e in store.pending()] == [("create", False, 1)]
    assert by_name(store, NEW).title == "Call mum"
    assert engine.run_once() == SyncResult("ok", [])
    assert [w["operationType"] for w in fake.writes] == ["create", "update", "create"]
    assert decode_text(fake._records[NEW]["fields"]["TitleDocument"]["value"]) == "Call mum"
    assert store.pending() == []


@pytest.mark.parametrize("code", ["OP_LOCK_FAILURE", "NOT_FOUND"])
def test_create_lock_failure_or_not_found_skips_to_next_entry(env, code):
    fake, store, engine = synced(env)
    store.apply_local(NEW, "create", {"title": "Call mum", "list_id": LIST})
    store.apply_local("R1", "update", {"priority": 5})
    fake.fail_next = [code]
    assert engine.run_once() == SyncResult("ok", [])
    assert [w["operationType"] for w in fake.writes] == ["create", "update"]  # no fallback
    assert [(e.op, e.in_flight, e.attempts) for e in store.pending()] == [("create", False, 1)]
    assert by_name(store, NEW) is not None
    engine.run_once()
    assert [w["operationType"] for w in fake.writes] == ["create", "update", "create"]
    assert NEW in fake._records and store.pending() == []


def _refuse_creates(fake, code):
    """Every create of NEW is refused with ``code``; other writes go through."""
    orig = fake._modify

    def modify(body):
        operation = body["operations"][0]
        if operation["operationType"] == "create" and operation["record"]["recordName"] == NEW:
            fake.writes.append(operation)
            return {"records": [fake._error(operation["record"], code)]}
        return orig(body)

    fake._modify = modify


@pytest.mark.parametrize("code", ["OP_LOCK_FAILURE", "NOT_FOUND"])
def test_create_refused_every_run_does_not_block_other_records(env, code):
    fake, store, engine = synced(env)
    store.apply_local(NEW, "create", {"title": "Call mum", "list_id": LIST})
    _refuse_creates(fake, code)
    for priority in (1, 5, 9):
        store.apply_local("R1", "update", {"priority": priority})
        assert engine.run_once() == SyncResult("ok", [])
        assert fake._records["R1"]["fields"]["Priority"]["value"] == priority
    assert [(e.op, e.attempts) for e in store.pending()] == [("create", 3)]


def test_create_refused_five_times_is_dropped(env, caplog):
    fake, store, engine = synced(env)
    store.apply_local(NEW, "create", {"title": "SECRET-TITLE", "list_id": LIST})
    _refuse_creates(fake, "OP_LOCK_FAILURE")
    for run in range(4):
        assert engine.run_once() == SyncResult("ok", [])
        assert store.pending()[0].attempts == run + 1
    with caplog.at_level(logging.DEBUG):
        assert engine.run_once() == SyncResult("ok", [NOT_ADDED])
    assert store.pending() == []
    assert by_name(store, NEW) is None  # the local-only reminder stops showing
    assert len(fake.writes) == 5
    assert h(NEW) in caplog.text
    assert NEW not in caplog.text and "SECRET-TITLE" not in caplog.text
    assert engine.run_once() == SyncResult("ok", [])
    assert len(fake.writes) == 5


def test_delete_during_refused_create_never_resurrects_the_reminder(env):
    fake, store, engine = synced(env)
    store.apply_local(NEW, "create", {"title": "Call mum", "list_id": LIST})
    orig = fake._modify
    refused = []

    def modify(body):
        operation = body["operations"][0]
        if operation["operationType"] == "create" and operation["record"]["recordName"] == NEW:
            if not refused:
                store.apply_local(NEW, "delete", {})  # the user deletes it mid-send
            if len(refused) < 2:
                refused.append(1)
                fake.writes.append(operation)
                return {"records": [fake._error(operation["record"], "OP_LOCK_FAILURE")]}
        return orig(body)

    fake._modify = modify
    for _ in range(4):
        engine.run_once()
        # the delete is never acked before the create has landed
        if NEW not in fake._records:
            assert [e.op for e in store.pending()][:1] == ["create"]
    record = fake._records.get(NEW)
    assert record is None or record["fields"].get("Deleted", {}).get("value") == 1
    row = by_name(store, NEW)
    assert row is None or row.deleted
    assert store.pending() == []


def test_skipped_create_does_not_block_update_of_other_record(env):
    fake, store, engine = synced(env)
    store.apply_local(NEW, "create", {"title": "Call mum", "list_id": LIST})
    store.apply_local("R1", "update", {"priority": 5})
    fake.fail_next = ["OP_LOCK_FAILURE"]
    assert engine.run_once() == SyncResult("ok", [])
    assert fake._records["R1"]["fields"]["Priority"]["value"] == 5
    assert [w["operationType"] for w in fake.writes] == ["create", "update"]


def test_create_cap_with_failing_lookup_leaves_no_local_row(env):
    from reminders_widget.icloud.errors import Transient
    fake, store, engine = synced(env)
    store.apply_local(NEW, "create", {"title": "Call mum", "list_id": LIST})
    _refuse_creates(fake, "OP_LOCK_FAILURE")
    for _ in range(4):
        engine.run_once()
    orig_post = fake.post

    def post(url, body):
        if url.endswith("records/lookup"):
            raise Transient("busy")
        return orig_post(url, body)

    fake.post = post
    result = engine.run_once()
    assert NOT_ADDED in result.notices
    assert store.pending() == []
    assert by_name(store, NEW) is None


def test_create_fallback_lock_failures_skip_the_create(env):
    fake, store, engine = synced(env)
    store.apply_local(NEW, "create", {"title": "Call dad", "list_id": LIST})
    store.apply_local("R1", "update", {"priority": 5})
    fake._store(rem(NEW, title="Call mum"))  # an earlier send landed
    fake.fail_next = ["EXISTS"] + ["OP_LOCK_FAILURE"] * 4
    assert engine.run_once() == SyncResult("ok", [])
    assert [w["operationType"] for w in fake.writes] == ["create"] + ["update"] * 5
    assert fake._records["R1"]["fields"]["Priority"]["value"] == 5
    assert [(e.op, e.attempts) for e in store.pending()] == [("create", 1)]


def test_unknown_change_key_is_dropped_without_blocking(env, caplog):
    fake, store, engine = synced(env)
    queue_raw(store, "R1", "update", {"colour_of_sky": "SECRET-VALUE"})
    store.apply_local("R2", "update", {"priority": 5})
    with caplog.at_level(logging.DEBUG):
        result = engine.run_once()
    assert result == SyncResult("ok", [])
    assert updates(fake, "R1") == []
    assert [w["record"]["fields"]["Priority"]["value"] for w in updates(fake, "R2")] == [5]
    assert store.pending() == []
    assert "colour_of_sky" in caplog.text and "SECRET-VALUE" not in caplog.text


def test_engine_clock_is_keyword_only(env):
    fake, store, _ = env
    with pytest.raises(TypeError):
        SyncEngine(CloudKit(fake, "https://example.test/"), store, lambda: NOW)


def test_create_without_list_uses_first_known_list(env):
    fake, store, engine = synced(env)
    queue_raw(store, NEW, "create", {"title": "Call mum"})  # queued by an older version
    engine.run_once()
    [w] = fake.writes
    assert w["record"]["fields"]["List"]["value"]["recordName"] == LIST


def test_create_without_any_known_list_waits_for_pull(env):
    fake, store, engine = env  # fresh store: no lists cached yet
    queue_raw(store, NEW, "create", {"title": "Call mum"})
    store.apply_local("R1", "update", {"priority": 5})  # other entries still go out
    assert engine.run_once().status == "ok"
    assert [w["record"]["recordName"] for w in fake.writes] == ["R1"]
    assert [(e.op, e.in_flight) for e in store.pending()] == [("create", False)]
    engine.run_once()
    assert fake.writes[-1]["operationType"] == "create"
    assert fake.writes[-1]["record"]["fields"]["List"]["value"]["recordName"] == LIST
    assert store.pending() == []


def test_delete_sends_deleted_flag(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "delete", {})
    assert engine.run_once().status == "ok"
    [w] = updates(fake, "R1")
    assert w["record"]["fields"] == {"Deleted": {"value": 1}}
    assert fake._records["R1"]["fields"]["Deleted"]["value"] == 1
    assert store.pending() == []
    assert by_name(store, "R1") is None


def test_edit_during_send_is_sent_on_next_run(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    orig = fake.post
    fired = []

    def hooked(url, body):
        if url.endswith("records/modify") and not fired:
            fired.append(1)
            store.apply_local("R1", "update", {"priority": 9})
        return orig(url, body)

    fake.post = hooked
    engine.run_once()
    assert [w["record"]["fields"]["Priority"]["value"] for w in updates(fake, "R1")] == [5]
    assert [e.changes for e in store.pending()] == [{"priority": 9}]
    assert by_name(store, "R1").priority == 9  # pull keeps the newer local value on screen
    engine.run_once()
    assert [w["record"]["fields"]["Priority"]["value"] for w in updates(fake, "R1")] == [5, 9]
    assert store.pending() == []
    assert fake._records["R1"]["fields"]["Priority"]["value"] == 9


# --- lock conflicts --------------------------------------------------------

def test_lock_failure_succeeds_on_retry(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    fake.fail_next = ["OP_LOCK_FAILURE"]
    assert engine.run_once().status == "ok"
    assert len(updates(fake, "R1")) == 2
    assert store.pending() == []
    assert fake._records["R1"]["fields"]["Priority"]["value"] == 5


def test_four_lock_failures_leave_entry_pending(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    store.apply_local("R2", "update", {"priority": 1})
    fake.fail_next = ["OP_LOCK_FAILURE"] * 4
    result = engine.run_once()
    assert result.status == "ok"
    assert len(updates(fake, "R1")) == 4
    assert updates(fake, "R2") == []  # pushing stops for this run
    pend = store.pending()
    assert [(e.record_name, e.changes, e.in_flight) for e in pend] == [
        ("R1", {"priority": 5}, False), ("R2", {"priority": 1}, False)]
    assert engine.run_once().status == "ok"
    assert store.pending() == []


# --- record gone / readonly ------------------------------------------------

def test_phone_delete_while_pc_edit_pending(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    fake.phone_delete("R1")
    result = engine.run_once()
    assert result.status == "ok"
    assert GONE in result.notices
    assert updates(fake, "R1") == []
    assert store.pending() == []
    assert by_name(store, "R1") is None


def test_hard_deleted_record_while_edit_pending(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    del fake._records["R1"]
    del fake._seq["R1"]
    result = engine.run_once()
    assert result.notices == [GONE]
    assert fake.writes == []
    assert store.pending() == []


def test_delete_entry_for_gone_record_is_acked_without_notice(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "delete", {})
    del fake._records["R1"]
    del fake._seq["R1"]
    result = engine.run_once()
    assert result == SyncResult("ok", [])
    assert fake.writes == []
    assert store.pending() == []


def test_delete_entry_for_record_deleted_on_phone_is_acked_without_notice(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "delete", {})
    fake.phone_delete("R1")
    result = engine.run_once()
    assert result == SyncResult("ok", [])
    assert fake.writes == []
    assert store.pending() == []


def test_readonly_reminder_is_never_written(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    fake.phone_edit("R1", TitleDocument="bm90IGEgZG9jdW1lbnQ=")  # undecodable now
    result = engine.run_once()
    assert READONLY in result.notices
    assert [w for w in fake.writes if w["record"]["recordName"] == "R1"] == []
    assert store.pending() == []


def _fail_pull(fake):
    from reminders_widget.icloud.errors import OfflineError
    orig = fake.post

    def post(url, body):
        if url.endswith("changes/zone"):
            raise OfflineError("pull fails")
        return orig(url, body)

    fake.post = post


def test_readonly_drop_shows_server_version_at_once(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"title": "Local title"})
    fake.phone_edit("R1", TitleDocument=encode_text("Server title"),
                    NotesDocument="bm90IGEgZG9jdW1lbnQ=")  # notes undecodable: read-only
    token = store.sync_token()
    _fail_pull(fake)  # only the push can fix the screen in this run
    result = engine.run_once()
    assert READONLY in result.notices
    r = by_name(store, "R1")
    assert r.title == "Server title" and r.readonly
    assert store.sync_token() == token
    assert updates(fake, "R1") == []


def test_refused_token_map_drop_shows_server_version(env):
    fake, store, engine = synced(env)
    fake.phone_edit("R1", ResolutionTokenMap="not json", Priority=1)
    store.apply_local("R1", "update", {"priority": 5})
    _fail_pull(fake)
    assert engine.run_once().notices == [READONLY]
    assert by_name(store, "R1").priority == 1


def test_gone_drop_hides_reminder_at_once(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    fake.phone_delete("R1")
    token = store.sync_token()
    _fail_pull(fake)
    assert engine.run_once().notices == [GONE]
    assert by_name(store, "R1") is None
    assert store.sync_token() == token


def test_unknown_token_map_is_treated_as_readonly(env):
    fake, store, engine = synced(env)
    fake.phone_edit("R1", ResolutionTokenMap="not json")
    store.apply_local("R1", "update", {"priority": 5})
    result = engine.run_once()
    assert result.notices == [READONLY]
    assert updates(fake, "R1") == []
    assert store.pending() == []


# --- pull --------------------------------------------------------------------

def test_first_run_pulls_lists_and_reminders(env):
    fake, store, engine = env
    assert engine.run_once() == SyncResult("ok", [])
    assert [l.name for l in store.lists()] == ["Home"]
    assert sorted(r.title for r in store.reminders()) == ["Buy milk", "Walk dog"]
    assert store.sync_token() is not None


def test_phone_edit_arrives_in_store(env):
    fake, store, engine = synced(env)
    fake.phone_edit("R1", Priority=9)
    assert engine.run_once().status == "ok"
    assert by_name(store, "R1").priority == 9


def test_phone_edit_of_same_field_keeps_pending_local_value(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    fake.phone_edit("R1", Priority=9, Flagged=1)
    fake.fail_next = ["OP_LOCK_FAILURE"] * 4  # local change stays queued this run
    engine.run_once()
    assert by_name(store, "R1").priority == 5


def test_tombstone_in_changes_removes_reminder(env):
    fake, store, engine = synced(env)
    orig = fake._changes_zone

    def with_tombstone(body):
        out = orig(body)
        out["zones"][0]["records"].append({"recordName": "R1", "deleted": True})
        return out

    fake._changes_zone = with_tombstone
    assert engine.run_once().status == "ok"
    assert by_name(store, "R1") is None
    assert by_name(store, "R2") is not None


def test_pull_with_malformed_record_fails_without_advancing_token(env):
    fake, store, engine = synced(env)
    token = store.sync_token()
    orig = fake._changes_zone

    def broken(body):
        out = orig(body)
        out["zones"][0]["records"].append({"recordType": "Reminder"})  # no recordName
        return out

    fake._changes_zone = broken
    assert engine.run_once().status != "ok"
    assert store.sync_token() == token


# --- run-level errors ------------------------------------------------------

def test_offline_leaves_queue_untouched(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    before = store.pending()
    fake.offline = True
    assert engine.run_once() == SyncResult("offline", [])
    assert store.pending() == before
    assert all(not e.in_flight for e in store.pending())


def test_session_expired(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    fake.session_valid = False
    assert engine.run_once().status == "session_expired"
    assert [(e.record_name, e.in_flight) for e in store.pending()] == [("R1", False)]


def _fail_modify_with(fake, exc_factory):
    orig = fake.post

    def post(url, body):
        if url.endswith("records/modify"):
            raise exc_factory()
        return orig(url, body)

    fake.post = post


def test_offline_during_modify_releases_entry(env):
    from reminders_widget.icloud.errors import OfflineError
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    _fail_modify_with(fake, lambda: OfflineError("gone"))
    assert engine.run_once().status == "offline"
    assert [(e.changes, e.in_flight) for e in store.pending()] == [({"priority": 5}, False)]


def test_session_expired_during_create_releases_entry(env):
    fake, store, engine = synced(env)
    store.apply_local(NEW, "create", {"title": "x", "list_id": LIST})
    orig = fake.post
    fake.post = lambda url, body: (421, {}) if url.endswith("records/modify") else orig(url, body)
    assert engine.run_once().status == "session_expired"
    assert [(e.op, e.in_flight) for e in store.pending()] == [("create", False)]


def test_unexpected_exception_is_an_error_run_and_releases_entry(env, caplog):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    _fail_modify_with(fake, lambda: RuntimeError("SECRET-TITLE boom"))
    with caplog.at_level(logging.DEBUG):
        assert engine.run_once().status == "error"
    assert [(e.changes, e.in_flight) for e in store.pending()] == [({"priority": 5}, False)]
    assert "RuntimeError" in caplog.text and "SECRET-TITLE" not in caplog.text
    failed = [r.getMessage() for r in caplog.records if "sync run failed" in r.getMessage()]
    assert len(failed) == 1
    assert re.search(r"test_sync\.py:\d+", failed[0])  # the raising frame
    assert re.search(r"sync\.py:\d+", failed[0])
    assert ":\\" not in failed[0] and "/" not in failed[0]  # file names only, no paths
    assert engine.run_once().status == "error"
    assert engine.run_once().status == "stopped"
    assert [e.in_flight for e in store.pending()] == [False]


def _patch_changes(fake, edit):
    orig = fake._changes_zone

    def patched(body):
        out = orig(body)
        edit(out["zones"][0])
        return out

    fake._changes_zone = patched


def test_null_change_tag_in_changes_is_saved(env):
    fake, store, engine = env

    def null_tag(zone):
        for rec in zone["records"]:
            if rec["recordName"] == "R1":
                rec["recordChangeTag"] = None

    _patch_changes(fake, null_tag)
    assert engine.run_once() == SyncResult("ok", [])
    r = by_name(store, "R1")
    assert r is not None and r.change_tag == ""


def test_null_list_reference_makes_reminder_readonly(env):
    fake, store, engine = env
    fake.phone_edit("R1", List={"recordName": None, "action": "NONE"})
    assert engine.run_once().status == "ok"
    r = by_name(store, "R1")
    assert r.readonly and r.list_id == ""


def test_null_records_in_changes_is_an_error_run(env):
    fake, store, engine = synced(env)
    token = store.sync_token()
    fake.phone_edit("R1", Priority=9)
    _patch_changes(fake, lambda zone: zone.__setitem__("records", None))
    assert engine.run_once().status == "error"
    assert store.sync_token() == token


def test_deleted_list_is_removed_and_not_used_for_creates():
    fake = FakeCloudKit([lst("List/A", "First"), lst("List/B", "Second"), rem("R1")])
    store = Store(":memory:", now=lambda: NOW)
    engine = SyncEngine(CloudKit(fake, "https://example.test/"), store, clock=lambda: NOW)
    engine.run_once()
    assert [l.record_name for l in store.lists()] == ["List/A", "List/B"]
    fake.phone_delete("List/A")
    engine.run_once()
    assert [l.record_name for l in store.lists()] == ["List/B"]
    queue_raw(store, NEW, "create", {"title": "Call mum"})
    engine.run_once()
    assert fake.writes[-1]["record"]["fields"]["List"]["value"]["recordName"] == "List/B"
    store.close()


def test_tombstone_removes_list(env):
    fake, store, engine = synced(env)
    _patch_changes(fake, lambda zone: zone["records"].append(
        {"recordName": LIST, "deleted": True}))
    assert engine.run_once().status == "ok"
    assert store.lists() == []


def test_three_unknown_response_runs_stop_sync(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    fake.fail_next = ["SERVER_WEIRDNESS"] * 10
    first, second = engine.run_once(), engine.run_once()
    assert first.status not in ("ok", "stopped") and second.status not in ("ok", "stopped")
    assert engine.run_once().status == "stopped"
    assert [e.in_flight for e in store.pending()] == [False]
    fake.fail_next = []
    assert engine.run_once().status == "stopped"  # sticky
    assert SyncEngine(CloudKit(fake, "https://example.test/"), store,
                      clock=lambda: NOW).run_once().status == "ok"


def test_unknown_response_count_resets_after_good_run(env):
    fake, store, engine = synced(env)
    for _ in range(2):
        fake.fail_next = ["SERVER_WEIRDNESS"]
        store.apply_local("R1", "update", {"priority": 5})
        assert engine.run_once().status not in ("ok", "stopped")
        fake.fail_next = []
        assert engine.run_once().status == "ok"
    fake.fail_next = ["SERVER_WEIRDNESS"]
    store.apply_local("R1", "update", {"priority": 1})
    assert engine.run_once().status != "stopped"


def test_next_delay():
    assert [next_delay(n) for n in range(6)] == [15, 30, 60, 120, 300, 300]


# --- logging ---------------------------------------------------------------

def test_logs_contain_no_content(caplog):
    fake = FakeCloudKit([lst(title="SECRET-LIST"),
                         rem("R1", title="SECRET-TITLE", notes="SECRET-NOTE")])
    store = Store(":memory:", now=lambda: NOW)
    engine = SyncEngine(CloudKit(fake, "https://example.test/"), store, clock=lambda: NOW)
    new = new_reminder_name()
    with caplog.at_level(logging.DEBUG):
        engine.run_once()
        store.apply_local("R1", "update", {"title": "SECRET-TITLE 2", "notes": "SECRET-NOTE 2"})
        store.apply_local(new, "create", {"title": "SECRET-TITLE 3", "list_id": LIST})
        fake.phone_edit("R1", Priority=9)
        engine.run_once()
        fake.phone_delete("R1")
        store.apply_local("R1", "update", {"title": "SECRET-TITLE 4"})
        engine.run_once()
        fake.offline = True
        engine.run_once()
    assert caplog.records  # the engine does log
    for secret in ("SECRET-TITLE", "SECRET-NOTE", "SECRET-LIST", "R1", new):
        assert secret not in caplog.text


# --- final review fixes ------------------------------------------------------------

NAIVE = datetime(2026, 5, 1, 9, 30)


@pytest.mark.parametrize("bad, exc_name", [
    ({"due": NAIVE}, "ValueError"),
    ({"title": 5}, None),
    ({"notes": 5}, "AttributeError"),
])
def test_unsendable_update_is_discarded_and_queue_moves_on(env, caplog, bad, exc_name):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    poison(store, "R1", bad)
    store.apply_local("R2", "update", {"priority": 1})
    fake.phone_edit("R1", Priority=9)
    _fail_pull(fake)  # only the push can refresh R1 in this run
    with caplog.at_level(logging.DEBUG):
        result = engine.run_once()
    assert result.notices == [DISCARDED]
    assert updates(fake, "R1") == []
    assert [w["record"]["fields"]["Priority"]["value"] for w in updates(fake, "R2")] == [1]
    assert store.pending() == []
    assert by_name(store, "R1").priority == 9  # refreshed from the server
    assert h("R1") in caplog.text and "R1" not in caplog.text
    if exc_name:
        assert exc_name in caplog.text


def test_unsendable_create_is_discarded_and_local_row_removed(env, caplog):
    fake, store, engine = synced(env)
    store.apply_local(NEW, "create", {"title": "SECRET-TITLE", "list_id": LIST})
    poison(store, NEW, {"title": "SECRET-TITLE", "list_id": LIST, "due": NAIVE})
    store.apply_local("R1", "update", {"priority": 5})
    with caplog.at_level(logging.DEBUG):
        result = engine.run_once()
    assert result == SyncResult("ok", [DISCARDED])
    assert [w["record"]["recordName"] for w in fake.writes] == ["R1"]
    assert store.pending() == []
    assert by_name(store, NEW) is None
    assert h(NEW) in caplog.text and "ValueError" in caplog.text
    assert "SECRET-TITLE" not in caplog.text and NEW not in caplog.text


def test_unsendable_entry_never_stops_sync(env):
    fake, store, engine = synced(env)
    for _ in range(5):
        queue_raw(store, "R1", "update", {"due": NAIVE})
        assert engine.run_once().status == "ok"
    assert store.pending() == []


def _answer_modify(fake, status, body):
    orig = fake.post
    fake.post = lambda url, b: (status, body) if url.endswith("records/modify") else orig(url, b)
    return orig


@pytest.mark.parametrize("status, body", [
    (429, {}), (500, {}), (503, {"serverErrorCode": "SERVICE_UNAVAILABLE"}),
    (200, {"records": [{"recordName": "R1", "serverErrorCode": "THROTTLED"}]}),
    (200, {"records": [{"recordName": "R1", "serverErrorCode": "TRY_AGAIN_LATER"}]}),
    (200, {"records": [{"recordName": "R1", "serverErrorCode": "INTERNAL_ERROR"}]}),
])
def test_transient_failure_releases_entry_and_never_stops(env, status, body):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    orig = _answer_modify(fake, status, body)
    for _ in range(5):
        result = engine.run_once()
        assert result.status == "transient"
        assert [(e.changes, e.in_flight) for e in store.pending()] == [({"priority": 5}, False)]
    fake.post = orig
    assert engine.run_once() == SyncResult("ok", [])
    assert fake._records["R1"]["fields"]["Priority"]["value"] == 5


def test_transient_during_pull(env):
    fake, store, engine = synced(env)
    token = store.sync_token()
    orig = fake.post
    fake.post = lambda url, b: (503, {}) if url.endswith("changes/zone") else orig(url, b)
    for _ in range(5):
        assert engine.run_once().status == "transient"
    assert store.sync_token() == token


def test_transient_carries_retry_after():
    from reminders_widget.icloud.errors import Transient
    fake = FakeCloudKit([lst()])
    store = Store(":memory:", now=lambda: NOW)
    engine = SyncEngine(CloudKit(fake, "https://example.test/"), store, clock=lambda: NOW)
    fake.post = lambda url, b: (429, {"retryAfter": 90})
    result = engine.run_once()
    assert result.status == "transient" and result.retry_after == 90.0
    store.close()


def test_transient_runs_neither_count_nor_reset_unknown_runs(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    fake.fail_next = ["SERVER_WEIRDNESS"] * 2
    assert engine.run_once().status == "error"
    assert engine.run_once().status == "error"
    fake.fail_next = ["THROTTLED"] * 5
    assert [engine.run_once().status for _ in range(5)] == ["transient"] * 5
    fake.fail_next = ["SERVER_WEIRDNESS"]
    assert engine.run_once().status == "stopped"


@pytest.mark.parametrize("code", ["AUTHENTICATION_REQUIRED", "AUTHENTICATION_FAILED"])
def test_authentication_code_is_session_expired_run(env, code):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    fake.fail_next = [code]
    assert engine.run_once().status == "session_expired"
    assert [(e.changes, e.in_flight) for e in store.pending()] == [({"priority": 5}, False)]


def test_retry_delay_honours_retry_after():
    assert retry_delay(SyncResult("transient", [], retry_after=None), 0) == next_delay(0)
    assert retry_delay(SyncResult("transient", [], retry_after=5.0), 2) == next_delay(2)
    assert retry_delay(SyncResult("transient", [], retry_after=900.0), 0) == 900.0
    assert retry_delay(SyncResult("offline", []), 4) == next_delay(4)


def test_completing_sends_completion_date_token(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"completed": True})
    assert engine.run_once().status == "ok"
    [w] = updates(fake, "R1")
    fields = w["record"]["fields"]
    assert fields["Completed"]["value"] == 1 and fields["CompletionDate"]["value"] == NOW * 1000
    token = json.loads(fields["ResolutionTokenMap"]["value"])["map"]
    assert set(token) == {"completed", "completionDate"}


def test_recurring_reminder_completion_is_refused_after_pull():
    fake = FakeCloudKit([lst(), rem("R1", RecurrenceRuleIDs=["x"]), rem("R2")])
    store = Store(":memory:", now=lambda: NOW)
    engine = SyncEngine(CloudKit(fake, "https://example.test/"), store, clock=lambda: NOW)
    assert engine.run_once().status == "ok"
    assert by_name(store, "R1").recurring and not by_name(store, "R2").recurring
    with pytest.raises(ValueError, match="Complete repeating reminders on iPhone."):
        store.apply_local("R1", "update", {"completed": True})
    store.apply_local("R2", "update", {"completed": True})
    assert [e.record_name for e in store.pending()] == ["R2"]
    store.close()


def test_group_list_is_never_the_default_for_creates():
    group = {"recordName": "List/G", "recordType": "List",
             "fields": {"Name": {"value": "Folder"}, "IsGroup": {"value": 1}}}
    fake = FakeCloudKit([group, lst("List/A", "First"), rem("R1")])
    store = Store(":memory:", now=lambda: NOW)
    engine = SyncEngine(CloudKit(fake, "https://example.test/"), store, clock=lambda: NOW)
    engine.run_once()
    assert [l.record_name for l in store.lists()] == ["List/A"]
    assert [l.record_name for l in store.lists(include_groups=True)] == ["List/G", "List/A"]
    queue_raw(store, NEW, "create", {"title": "Call mum"})
    engine.run_once()
    assert fake.writes[-1]["record"]["fields"]["List"]["value"]["recordName"] == "List/A"
    store.close()


def test_only_groups_known_create_waits():
    group = {"recordName": "List/G", "recordType": "List",
             "fields": {"Name": {"value": "Folder"}, "IsGroup": {"value": 1}}}
    fake = FakeCloudKit([group])
    store = Store(":memory:", now=lambda: NOW)
    engine = SyncEngine(CloudKit(fake, "https://example.test/"), store, clock=lambda: NOW)
    engine.run_once()
    queue_raw(store, NEW, "create", {"title": "Call mum"})
    assert engine.run_once().status == "ok"
    assert fake.writes == []
    assert [(e.op, e.attempts) for e in store.pending()] == [("create", 0)]
    store.close()
