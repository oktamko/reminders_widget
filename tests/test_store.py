from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from reminders_widget.icloud.records import Reminder, ReminderList, new_reminder_name
from reminders_widget.store import QueueEntry, Store

N1 = new_reminder_name()


def rem(name="r1", **kw):
    base = dict(
        record_name=name, list_id="L1", title="Milk", notes=None, completed=False,
        priority=0, due=None, deleted=False, change_tag="t1", readonly=False,
        created=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    base.update(kw)
    return Reminder(**base)


@pytest.fixture
def store():
    s = Store(":memory:", now=lambda: 1_700_000_000.0)
    yield s
    s.close()


def test_save_remote_roundtrip_and_token(store):
    lst = ReminderList("L1", "Home", "#ff0000")
    r = rem(due=datetime(2026, 5, 1, 9, 30, tzinfo=timezone.utc))
    assert store.sync_token() is None
    store.save_remote([lst, r], "tok1")
    assert store.sync_token() == "tok1"
    assert store.lists() == [lst]
    assert store.reminders() == [r]


def test_due_date_vs_datetime_preserved(store):
    a = rem("a", due=date(2026, 5, 1))
    b = rem("b", due=datetime(2026, 5, 1, tzinfo=timezone.utc))
    store.save_remote([a, b], "t")
    got = {x.record_name: x for x in store.reminders()}
    assert got["a"].due == date(2026, 5, 1) and type(got["a"].due) is date
    assert isinstance(got["b"].due, datetime) and got["b"].due.tzinfo is not None


def test_reminders_hides_deleted_and_completed(store):
    store.save_remote([rem("a"), rem("b", completed=True), rem("c", deleted=True)], "t")
    assert [r.record_name for r in store.reminders()] == ["a"]


def test_save_remote_replaces_and_removes_deleted(store):
    store.save_remote([rem("a"), rem("b")], "t1")
    store.save_remote([rem("a", title="New"), rem("b", deleted=True)], "t2")
    assert [(r.record_name, r.title) for r in store.reminders()] == [("a", "New")]


def test_apply_local_update_visible_and_queued(store):
    store.save_remote([rem()], "t")
    store.apply_local("r1", "update", {"priority": 1})
    assert store.reminders()[0].priority == 1
    (e,) = store.pending()
    assert (e.record_name, e.op, e.changes) == ("r1", "update", {"priority": 1})
    assert isinstance(e, QueueEntry)


def test_complete_removes_from_reminders(store):
    store.save_remote([rem()], "t")
    store.apply_local("r1", "update", {"completed": True})
    assert store.reminders() == []


def test_three_priority_clicks_one_entry(store):
    store.save_remote([rem()], "t")
    for p in (1, 5, 9):
        store.apply_local("r1", "update", {"priority": p})
    (e,) = store.pending()
    assert e.changes == {"priority": 9}


def test_create_inserts_row_with_defaults(store):
    store.apply_local(N1, "create", {"title": "New", "list_id": "L1"})
    (r,) = store.reminders()
    assert (r.record_name, r.title, r.list_id) == (N1, "New", "L1")
    assert (r.completed, r.priority, r.readonly, r.change_tag, r.deleted) == (False, 0, False, "", False)
    assert r.created == datetime.fromtimestamp(1_700_000_000.0, timezone.utc)
    (e,) = store.pending()
    assert e.op == "create"


def test_create_then_update_merges(store):
    store.apply_local(N1, "create", {"title": "A", "list_id": "L1"})
    store.apply_local(N1, "update", {"title": "B", "priority": 5})
    (e,) = store.pending()
    assert e.op == "create"
    assert e.changes == {"title": "B", "list_id": "L1", "priority": 5}
    assert store.reminders()[0].title == "B"


def test_create_then_delete_cancels(store):
    store.apply_local(N1, "create", {"title": "A", "list_id": "L1"})
    store.apply_local(N1, "delete", {})
    assert store.pending() == []
    assert store.reminders() == []


def test_update_then_update_newer_wins(store):
    store.save_remote([rem()], "t")
    store.apply_local("r1", "update", {"title": "A", "priority": 1})
    store.apply_local("r1", "update", {"title": "B"})
    (e,) = store.pending()
    assert e.op == "update" and e.changes == {"title": "B", "priority": 1}


def test_update_then_delete_becomes_delete(store):
    store.save_remote([rem()], "t")
    store.apply_local("r1", "update", {"title": "A"})
    store.apply_local("r1", "delete", {})
    (e,) = store.pending()
    assert e.op == "delete"
    assert e.changes == {"deleted": True}
    assert store.reminders() == []


def test_delete_alone_queues_and_hides(store):
    store.save_remote([rem()], "t")
    store.apply_local("r1", "delete", {})
    (e,) = store.pending()
    assert (e.op, e.changes) == ("delete", {"deleted": True})
    assert store.reminders() == []


def test_different_records_separate_entries_oldest_first(store):
    store.save_remote([rem("a"), rem("b")], "t")
    store.apply_local("b", "update", {"priority": 1})
    store.apply_local("a", "update", {"priority": 5})
    assert [e.record_name for e in store.pending()] == ["b", "a"]
    assert [e.id for e in store.pending()] == sorted(e.id for e in store.pending())


def test_pending_keys(store):
    store.save_remote([rem()], "t")
    assert store.pending_keys("r1") == set()
    store.apply_local("r1", "update", {"title": "x", "priority": 1})
    assert store.pending_keys("r1") == {"title", "priority"}


def test_ack_and_drop(store):
    store.save_remote([rem("a"), rem("b")], "t")
    store.apply_local("a", "update", {"priority": 1})
    store.apply_local("b", "update", {"priority": 1})
    first = store.pending()[0]
    store.ack(first.id)
    assert [e.record_name for e in store.pending()] == ["b"]
    store.drop("b")
    assert store.pending() == []


def test_readonly_raises(store):
    store.save_remote([rem(readonly=True)], "t")
    with pytest.raises(ValueError):
        store.apply_local("r1", "update", {"title": "x"})
    with pytest.raises(ValueError):
        store.apply_local("r1", "delete", {})
    assert store.pending() == []


def test_save_remote_keeps_pending_local_fields(store):
    store.save_remote([rem()], "t1")
    store.apply_local("r1", "update", {"title": "Mine", "priority": 1})
    store.save_remote([rem(title="Theirs", priority=9, notes="remote notes", change_tag="t2")], "t2")
    (r,) = store.reminders()
    assert r.title == "Mine" and r.priority == 1
    assert r.notes == "remote notes" and r.change_tag == "t2"
    assert store.sync_token() == "t2"


def test_save_remote_pending_delete_stays_hidden(store):
    store.save_remote([rem()], "t1")
    store.apply_local("r1", "delete", {})
    store.save_remote([rem(change_tag="t2")], "t2")
    assert store.reminders() == []


def test_save_remote_pending_create_row_kept(store):
    store.apply_local(N1, "create", {"title": "A", "list_id": "L1"})
    store.save_remote([rem("other")], "t")
    assert {r.record_name for r in store.reminders()} == {N1, "other"}


def test_due_roundtrips_through_queue(store):
    store.save_remote([rem()], "t")
    d = datetime(2026, 5, 1, 9, 30, tzinfo=timezone.utc)
    store.apply_local("r1", "update", {"due": d})
    assert store.pending()[0].changes["due"] == d
    store.apply_local("r1", "update", {"due": date(2026, 6, 1)})
    v = store.pending()[0].changes["due"]
    assert v == date(2026, 6, 1) and type(v) is date
    store.apply_local("r1", "update", {"due": None})
    assert store.pending()[0].changes["due"] is None
    assert store.reminders()[0].due is None


def test_queue_and_data_survive_reopen(tmp_path):
    path = tmp_path / "s.db"
    s = Store(path)
    s.save_remote([ReminderList("L1", "Home", None), rem()], "tok")
    s.apply_local("r1", "update", {"priority": 5, "due": date(2026, 5, 1)})
    s.apply_local(N1, "create", {"title": "A", "list_id": "L1"})
    before = s.pending()
    s.close()
    s2 = Store(path)
    assert s2.pending() == before
    assert s2.sync_token() == "tok"
    assert {r.record_name for r in s2.reminders()} == {"r1", N1}
    s2.close()


def test_settings_roundtrip(store):
    assert store.get_setting("lists", ["x"]) == ["x"]
    store.set_setting("lists", ["a", "b"])
    assert store.get_setting("lists", None) == ["a", "b"]
    store.set_setting("n", 3)
    assert store.get_setting("n", 0) == 3


def test_unknown_op_raises(store):
    with pytest.raises(ValueError):
        store.apply_local("r1", "explode", {})


def test_edit_between_begin_send_and_ack_survives(store):
    store.save_remote([rem()], "t")
    store.apply_local("r1", "update", {"priority": 1})
    snap = store.begin_send(store.pending()[0].id)
    assert snap.in_flight and snap.changes == {"priority": 1}
    assert store.pending() == []
    store.apply_local("r1", "update", {"priority": 5})
    store.ack(snap.id)
    (e,) = store.pending()
    assert e.id != snap.id and e.changes == {"priority": 5} and not e.in_flight


def test_create_in_flight_then_delete_queues_delete(store):
    store.apply_local(N1, "create", {"title": "A", "list_id": "L1"})
    snap = store.begin_send(store.pending()[0].id)
    store.apply_local(N1, "delete", {})
    store.ack(snap.id)
    (e,) = store.pending()
    assert (e.op, e.changes) == ("delete", {"deleted": True})
    assert store.reminders() == []


def test_pending_keys_includes_in_flight(store):
    store.save_remote([rem()], "t")
    store.apply_local("r1", "update", {"title": "x"})
    store.begin_send(store.pending()[0].id)
    store.apply_local("r1", "update", {"priority": 1})
    assert store.pending_keys("r1") == {"title", "priority"}


def test_release_without_newer_restores_entry(store):
    store.save_remote([rem()], "t")
    store.apply_local("r1", "update", {"title": "x"})
    snap = store.begin_send(store.pending()[0].id)
    store.release(snap.id)
    (e,) = store.pending()
    assert e.id == snap.id and e.changes == {"title": "x"} and not e.in_flight


def test_release_merges_under_newer(store):
    store.save_remote([rem()], "t")
    store.apply_local("r1", "update", {"title": "old", "priority": 1})
    snap = store.begin_send(store.pending()[0].id)
    store.apply_local("r1", "update", {"title": "new"})
    store.release(snap.id)
    (e,) = store.pending()
    assert e.op == "update" and e.changes == {"title": "new", "priority": 1}


def test_release_create_merges_update_keeps_create(store):
    store.apply_local(N1, "create", {"title": "A", "list_id": "L1"})
    snap = store.begin_send(store.pending()[0].id)
    store.apply_local(N1, "update", {"priority": 5})
    store.release(snap.id)
    (e,) = store.pending()
    assert e.op == "create" and e.changes == {"title": "A", "list_id": "L1", "priority": 5}


def test_release_update_then_delete_becomes_delete(store):
    store.save_remote([rem()], "t")
    store.apply_local("r1", "update", {"title": "x"})
    snap = store.begin_send(store.pending()[0].id)
    store.apply_local("r1", "delete", {})
    store.release(snap.id)
    (e,) = store.pending()
    assert (e.op, e.changes) == ("delete", {"deleted": True})


def test_release_create_then_delete_keeps_both(store):
    store.apply_local(N1, "create", {"title": "A", "list_id": "L1"})
    snap = store.begin_send(store.pending()[0].id)
    store.apply_local(N1, "delete", {})
    store.release(snap.id)
    assert [e.op for e in store.pending()] == ["create", "delete"]


def test_restart_clears_in_flight(tmp_path):
    path = tmp_path / "s.db"
    s = Store(path)
    s.save_remote([rem()], "t")
    s.apply_local("r1", "update", {"priority": 1})
    eid = s.pending()[0].id
    s.begin_send(eid)
    assert s.pending() == []
    s.close()
    s2 = Store(path)
    (e,) = s2.pending()
    assert e.id == eid and not e.in_flight
    s2.close()


def test_delete_then_update_dropped(store):
    store.save_remote([rem()], "t")
    store.apply_local("r1", "delete", {})
    store.apply_local("r1", "update", {"title": "ghost"})
    (e,) = store.pending()
    assert (e.op, e.changes) == ("delete", {"deleted": True})
    row = store._get(store._db, "r1")
    assert row.title == "Milk" and row.deleted


def test_save_remote_leaves_queue_for_deleted_record(store):
    store.save_remote([rem()], "t")
    store.apply_local("r1", "update", {"priority": 1})
    store.save_remote([rem(deleted=True)], "t2")
    assert store.reminders() == []
    assert len(store.pending()) == 1


def test_threads_concurrent(store):
    import threading

    store.save_remote([rem("a"), rem("b")], "t")
    errors = []

    def work(name):
        try:
            for i in range(200):
                store.apply_local(name, "update", {"priority": (0, 1, 5)[i % 3]})
                store.pending()
                store.reminders()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in ("a", "b")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len(store.pending()) == 2


def test_save_remote_deleted_list_is_removed(store):
    store.save_remote([ReminderList("L1", "Home", None), ReminderList("L2", "Work", None)], "t1")
    store.save_remote([ReminderList("L1", "", None, deleted=True)], "t2")
    assert [l.record_name for l in store.lists()] == ["L2"]
    assert store.sync_token() == "t2"


# --- final review fixes: input validation -------------------------------------

NAIVE = datetime(2026, 5, 1, 9, 30)


@pytest.mark.parametrize("changes", [
    {"due": NAIVE},
    {"due": "2026-05-01"},
    {"due": 5},
    {"title": 5},
    {"title": None},
    {"title": "bad \ud800 surrogate"},
    {"notes": b"bytes"},
    {"notes": "lone \udfff"},
    {"priority": 2},
    {"priority": "5"},
    {"priority": True},
    {"priority": 5.0},
    {"completed": 1},
    {"completed": "yes"},
    {"deleted": 0},
    {"list_id": 7},
    {"list_id": ""},
    {"colour": "red"},
])
def test_invalid_update_is_refused_before_queueing(store, changes):
    store.save_remote([rem()], "t")
    with pytest.raises(ValueError):
        store.apply_local("r1", "update", changes)
    assert store.pending() == []
    assert store.reminders() == [rem()]


def test_valid_values_are_accepted(store):
    store.save_remote([rem()], "t")
    for changes in ({"title": "Ok ✓ \U0001F600"}, {"notes": ""}, {"priority": 0},
                    {"priority": 1}, {"priority": 9}, {"completed": False}, {"due": None},
                    {"due": date(2026, 1, 1)},
                    {"due": datetime(2026, 1, 1, tzinfo=timezone.utc)}, {"list_id": "L2"}):
        store.apply_local("r1", "update", changes)
    assert len(store.pending()) == 1


def test_create_requires_list_id(store):
    with pytest.raises(ValueError):
        store.apply_local(N1, "create", {"title": "A"})
    assert store.pending() == [] and store.reminders() == []


def test_create_validates_values(store):
    with pytest.raises(ValueError):
        store.apply_local(N1, "create", {"title": "A", "list_id": "L1", "due": NAIVE})
    assert store.pending() == [] and store.reminders() == []


@pytest.mark.parametrize("changes", [{"title": "x"}, {"deleted": False}, {"priority": 1}])
def test_delete_takes_no_other_keys(store, changes):
    store.save_remote([rem()], "t")
    with pytest.raises(ValueError):
        store.apply_local("r1", "delete", changes)
    assert store.pending() == []


def test_delete_accepts_empty_or_deleted_true(store):
    store.save_remote([rem("a"), rem("b")], "t")
    store.apply_local("a", "delete", {})
    store.apply_local("b", "delete", {"deleted": True})
    assert [e.op for e in store.pending()] == ["delete", "delete"]


def test_changes_must_be_a_dict(store):
    store.save_remote([rem()], "t")
    with pytest.raises(ValueError):
        store.apply_local("r1", "update", [("priority", 1)])


# --- recurring reminders -------------------------------------------------------

@pytest.mark.parametrize("value", [True, False])
def test_completing_recurring_reminder_is_refused(store, value):
    store.save_remote([rem(recurring=True)], "t")
    with pytest.raises(ValueError, match="Complete repeating reminders on iPhone."):
        store.apply_local("r1", "update", {"completed": value})
    assert store.pending() == []
    store.apply_local("r1", "update", {"priority": 5})  # other edits still work
    assert store.pending()[0].changes == {"priority": 5}


def test_recurring_roundtrips(store):
    store.save_remote([rem("a", recurring=True), rem("b")], "t")
    got = {r.record_name: r.recurring for r in store.reminders()}
    assert got == {"a": True, "b": False}


def test_recurring_fixture_records_refuse_completion(store):
    import json
    from pathlib import Path
    from reminders_widget.icloud.records import parse_record
    records = json.loads((Path(__file__).parent / "fixtures" / "records.json").read_text("utf-8"))
    parsed = [parse_record(r) for r in records]
    store.save_remote([p for p in parsed if p is not None], "t")
    recurring = [p for p in parsed if isinstance(p, Reminder) and p.recurring]
    assert len(recurring) == 2
    for r in recurring:
        with pytest.raises(ValueError, match="repeating"):
            store.apply_local(r.record_name, "update", {"completed": True})
    plain = next(p for p in parsed if isinstance(p, Reminder) and not p.recurring
                 and not p.completed)
    store.apply_local(plain.record_name, "update", {"completed": True})
    assert [e.record_name for e in store.pending()] == [plain.record_name]


# --- list groups ----------------------------------------------------------------

def test_lists_exclude_groups_by_default(store):
    group = ReminderList("G", "Group", None, is_group=True)
    store.save_remote([ReminderList("L1", "Home", None), group,
                       ReminderList("L2", "Work", None)], "t")
    assert [l.record_name for l in store.lists()] == ["L1", "L2"]
    assert store.lists(include_groups=True) == [
        ReminderList("L1", "Home", None), group, ReminderList("L2", "Work", None)]


# --- stable row order -------------------------------------------------------------

def test_list_order_survives_rename(store):
    store.save_remote([ReminderList("L1", "One", None), ReminderList("L2", "Two", None),
                       ReminderList("L3", "Three", None)], "t1")
    store.save_remote([ReminderList("L1", "One renamed", "#112233")], "t2")
    assert [(l.record_name, l.name) for l in store.lists()] == [
        ("L1", "One renamed"), ("L2", "Two"), ("L3", "Three")]


def test_reminder_order_survives_update(store):
    store.save_remote([rem("a"), rem("b"), rem("c")], "t1")
    store.save_remote([rem("a", title="New")], "t2")
    store.apply_local("b", "update", {"priority": 1})
    assert [r.record_name for r in store.reminders()] == ["a", "b", "c"]


# --- attempts --------------------------------------------------------------------

def test_release_counts_attempts_only_when_asked(store):
    store.apply_local(N1, "create", {"title": "A", "list_id": "L1"})
    eid = store.pending()[0].id
    assert store.pending()[0].attempts == 0
    store.release(store.begin_send(eid).id)
    assert store.pending()[0].attempts == 0
    store.release(store.begin_send(eid).id, attempt=True)
    store.release(store.begin_send(eid).id, attempt=True)
    assert store.pending()[0].attempts == 2


def test_attempts_carry_over_when_release_merges(store):
    store.apply_local(N1, "create", {"title": "A", "list_id": "L1"})
    snap = store.begin_send(store.pending()[0].id)
    store.release(snap.id, attempt=True)
    snap = store.begin_send(store.pending()[0].id)
    store.apply_local(N1, "update", {"priority": 5})
    store.release(snap.id, attempt=True)
    (e,) = store.pending()
    assert e.op == "create" and e.attempts == 2


# --- schema versioning -------------------------------------------------------------

_V0 = """
CREATE TABLE lists (record_name TEXT PRIMARY KEY, name TEXT NOT NULL, color TEXT);
CREATE TABLE reminders (
    record_name TEXT PRIMARY KEY, list_id TEXT NOT NULL, title TEXT, notes TEXT,
    completed INTEGER NOT NULL, priority INTEGER NOT NULL, due TEXT, deleted INTEGER NOT NULL,
    change_tag TEXT NOT NULL, readonly INTEGER NOT NULL, created TEXT);
CREATE TABLE queue (id INTEGER PRIMARY KEY AUTOINCREMENT, record_name TEXT NOT NULL,
    op TEXT NOT NULL, changes TEXT NOT NULL);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO lists VALUES ('L1', 'Home', NULL);
INSERT INTO reminders VALUES ('r1', 'L1', 'Milk', NULL, 0, 0, NULL, 0, 't1', 0, NULL);
INSERT INTO queue (record_name, op, changes) VALUES ('r1', 'update', '{"priority": 5}');
INSERT INTO meta VALUES ('sync_token', 'tok');
"""


@pytest.mark.parametrize("with_in_flight", [False, True])
def test_old_database_is_migrated(tmp_path, with_in_flight):
    import sqlite3
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(_V0)
    if with_in_flight:  # version 1: in_flight already added
        db.execute("ALTER TABLE queue ADD COLUMN in_flight INTEGER NOT NULL DEFAULT 0")
        db.execute("PRAGMA user_version = 1")
    db.commit()
    db.close()
    s = Store(path)
    assert s._db.execute("PRAGMA user_version").fetchone()[0] == 3
    assert s.lists() == [ReminderList("L1", "Home", None)]
    (r,) = s.reminders()
    assert r.title == "Milk" and r.recurring is False
    (e,) = s.pending()
    assert e.changes == {"priority": 5} and e.attempts == 0 and not e.in_flight
    assert s.sync_token() is None   # version 3 downloads everything once
    s.apply_local("r1", "update", {"title": "Oat milk"})
    s.release(s.begin_send(e.id).id, attempt=True)
    assert s.pending()[0].attempts == 1
    s.close()
    s2 = Store(path)  # reopening a current database changes nothing
    assert s2.pending()[0].attempts == 1
    s2.close()


def test_new_database_has_current_version(store):
    assert store._db.execute("PRAGMA user_version").fetchone()[0] == 3


def test_list_reminder_order_is_stored(store):   # probe round 4: ReminderIDs
    store.save_remote([ReminderList("L1", "Home", "#007AFF", order=("B", "A"))], sync_token="t")
    assert store.lists()[0].order == ("B", "A")


def test_alarmed_roundtrips(store):
    store.save_remote([rem("a", alarmed=True), rem("b")], "t")
    assert {r.record_name: r.alarmed for r in store.reminders()} == {"a": True, "b": False}


def test_moving_to_version_3_downloads_everything_again(tmp_path):   # new fields need a full fetch
    path = tmp_path / "v2.db"
    s = Store(path)
    s.save_remote([rem("a")], "tok")
    s._db.execute("PRAGMA user_version = 2")
    s.close()
    s2 = Store(path)
    assert s2.sync_token() is None and [r.record_name for r in s2.reminders()] == ["a"]
    s2.close()
