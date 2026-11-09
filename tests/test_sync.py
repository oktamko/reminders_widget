import json
import logging
import uuid

import pytest

from reminders_widget.icloud.cloudkit import CloudKit
from reminders_widget.icloud.crdt import decode_text, encode_text
from reminders_widget.icloud.tokens import apple_now
from reminders_widget.store import Store
from reminders_widget.sync import SyncEngine, SyncResult, next_delay
from tests.fakes.fake_cloudkit import FakeCloudKit

NOW = 1_800_000_000.0
GONE = "A reminder you changed was deleted on another device."
READONLY = "This reminder can't be changed on PC yet."
LIST = "List/L1"
IPHONE_ID = "C20AA380-F3E5-4F0A-BDF1-C454606DD15E"


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
    store.apply_local("New1", "create", {"title": "Call mum", "list_id": LIST})
    assert engine.run_once().status == "ok"
    [w] = fake.writes
    assert w["operationType"] == "create"
    rec = w["record"]
    assert rec["recordName"] == "New1" and rec["recordType"] == "Reminder"
    assert decode_text(rec["fields"]["TitleDocument"]["value"]) == "Call mum"
    assert rec["fields"]["Completed"]["value"] == 0
    assert rec["fields"]["List"]["value"]["recordName"] == LIST
    assert "ResolutionTokenMap" not in rec["fields"]
    assert store.pending() == []
    assert by_name(store, "New1").title == "Call mum"


def test_create_then_edit_before_sync_sends_one_merged_create(env):
    fake, store, engine = synced(env)
    store.apply_local("New1", "create", {"title": "Call mum", "list_id": LIST})
    store.apply_local("New1", "update", {"priority": 5})
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


def test_create_conflict_after_lost_response_sends_later_edit(env):
    fake, store, engine = synced(env)
    store.apply_local("New1", "create", {"title": "Call mum", "list_id": LIST})
    _lose_first_create_response(
        fake, during=lambda: store.apply_local("New1", "update", {"title": "Call dad"}))
    assert engine.run_once().status == "offline"
    assert [(e.op, e.in_flight) for e in store.pending()] == [("create", False)]
    assert engine.run_once().status == "ok"
    ops = [w["operationType"] for w in fake.writes]
    assert ops == ["create", "create", "update"]
    upd = fake.writes[-1]["record"]["fields"]
    assert set(upd) == {"TitleDocument", "ResolutionTokenMap"}  # list_id already equal
    assert decode_text(fake._records["New1"]["fields"]["TitleDocument"]["value"]) == "Call dad"
    token = json.loads(upd["ResolutionTokenMap"]["value"])["map"]
    assert set(token) == {"titleDocument"}
    assert store.pending() == []
    assert by_name(store, "New1").title == "Call dad"


def test_duplicate_create_without_edits_writes_nothing_more(env):
    fake, store, engine = synced(env)
    store.apply_local("New1", "create", {"title": "Call mum", "list_id": LIST,
                                         "priority": 5, "completed": False})
    _lose_first_create_response(fake)
    assert engine.run_once().status == "offline"
    result = engine.run_once()
    assert result == SyncResult("ok", [])
    assert [w["operationType"] for w in fake.writes] == ["create", "create"]
    assert store.pending() == []
    assert by_name(store, "New1").title == "Call mum"


def test_create_conflict_for_record_gone_on_lookup_drops_with_notice(env):
    fake, store, engine = synced(env)
    store.apply_local("New1", "create", {"title": "Call mum", "list_id": LIST})
    fake.fail_next = ["CONFLICT"]  # the fake does not store it, so lookup finds nothing
    result = engine.run_once()
    assert result.notices == [GONE]
    assert [w["operationType"] for w in fake.writes] == ["create"]
    assert store.pending() == []
    assert by_name(store, "New1") is None


def test_unknown_change_key_is_dropped_without_blocking(env, caplog):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"colour_of_sky": "SECRET-VALUE"})
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
    store.apply_local("New1", "create", {"title": "Call mum"})
    engine.run_once()
    [w] = fake.writes
    assert w["record"]["fields"]["List"]["value"]["recordName"] == LIST


def test_create_without_any_known_list_waits_for_pull(env):
    fake, store, engine = env  # fresh store: no lists cached yet
    store.apply_local("New1", "create", {"title": "Call mum"})
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


def test_phone_edit_on_other_field_keeps_pending_local_value(env):
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
    store.apply_local("New1", "create", {"title": "x", "list_id": LIST})
    orig = fake.post
    fake.post = lambda url, body: (421, {}) if url.endswith("records/modify") else orig(url, body)
    assert engine.run_once().status == "session_expired"
    assert [(e.op, e.in_flight) for e in store.pending()] == [("create", False)]


def test_unexpected_exception_releases_entry(env):
    fake, store, engine = synced(env)
    store.apply_local("R1", "update", {"priority": 5})
    _fail_modify_with(fake, lambda: RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        engine.run_once()
    assert [(e.changes, e.in_flight) for e in store.pending()] == [({"priority": 5}, False)]


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
    with caplog.at_level(logging.DEBUG):
        engine.run_once()
        store.apply_local("R1", "update", {"title": "SECRET-TITLE 2", "notes": "SECRET-NOTE 2"})
        store.apply_local("N1", "create", {"title": "SECRET-TITLE 3", "list_id": LIST})
        fake.phone_edit("R1", Priority=9)
        engine.run_once()
        fake.phone_delete("R1")
        store.apply_local("R1", "update", {"title": "SECRET-TITLE 4"})
        engine.run_once()
        fake.offline = True
        engine.run_once()
    assert caplog.records  # the engine does log
    for secret in ("SECRET-TITLE", "SECRET-NOTE", "SECRET-LIST", "R1", "N1"):
        assert secret not in caplog.text
