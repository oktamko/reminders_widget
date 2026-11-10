from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from reminders_widget.app.api import Api, banner_for, parse_due
from reminders_widget.icloud.records import Reminder, ReminderList
from reminders_widget.store import Store
from reminders_widget.sync import SyncResult

L1, L2 = ReminderList("List/L1", "Work", None), ReminderList("List/L2", "School", "#F0997B")


def rem(name, list_id="List/L1", title="T", priority=0, **kw):
    base = Reminder(record_name=name, list_id=list_id, title=title, notes=None, completed=False,
                    priority=priority, due=None, deleted=False, change_tag="t1", readonly=False,
                    created=datetime(2026, 1, 1, tzinfo=timezone.utc))
    return replace(base, **kw)


class FakeScheduler:
    def __init__(self):
        self.pokes = 0

    def poke(self):
        self.pokes += 1


@pytest.fixture
def setup():
    store = Store(":memory:")
    store.save_remote([L1, L2, rem("Reminder/A", priority=9), rem("Reminder/B", "List/L2", priority=1),
                       rem("Reminder/RO", readonly=True), rem("Reminder/REC", recurring=True)], "s1")
    sched = FakeScheduler()
    pushed = []
    return store, sched, Api(store, sched, push=pushed.append), pushed


def ids(state):
    return [r["id"] for r in state["reminders"]]


def test_state_shape_and_default_selection(setup):
    _, _, api, _ = setup
    s = api.state()
    assert [l["id"] for l in s["lists"]] == ["List/L1", "List/L2"]
    assert set(s["selected"]) == {"List/L1", "List/L2"} and s["title"] == "All"
    assert s["sort"] == "priority" and ids(s)[0] == "Reminder/B"
    assert s["status"] == "ok" and s["banner"] is None


def test_select_and_sort_persist_across_api_instances(setup):
    store, sched, api, _ = setup
    api.select(["List/L2"])
    api.set_sort("icloud")
    s = Api(store, sched, push=lambda s: None).state()
    assert s["selected"] == ["List/L2"] and s["title"] == "School" and s["sort"] == "icloud"


def test_add_goes_to_first_selected_list_and_pokes(setup):
    _, sched, api, _ = setup
    api.select(["List/L2"])
    out = api.add("Buy milk")
    assert out["ok"] and sched.pokes == 1
    new = [r for r in api.state()["reminders"] if r["title"] == "Buy milk"][0]
    assert new["list"] == "List/L2" and new["id"].startswith("Reminder/")


def test_complete_hides_reminder(setup):
    _, sched, api, _ = setup
    assert api.complete("Reminder/A", True)["ok"]
    assert "Reminder/A" not in ids(api.state()) and sched.pokes == 1


def test_priority_rename_notes(setup):
    _, sched, api, _ = setup
    assert api.set_priority("Reminder/A", 1)["ok"]
    assert api.rename("Reminder/A", "New name")["ok"]
    assert api.set_notes("Reminder/A", "a note")["ok"]
    a = [r for r in api.state()["reminders"] if r["id"] == "Reminder/A"][0]
    assert (a["priority"], a["title"], a["notes"]) == (1, "New name", "a note")
    assert sched.pokes == 3


def test_readonly_and_recurring_refusals_queue_nothing(setup):
    store, sched, api, _ = setup
    ro = api.set_priority("Reminder/RO", 1)
    rec = api.complete("Reminder/REC", True)
    assert ro == {"ok": False, "message": "This reminder can't be changed on PC yet."}
    assert rec == {"ok": False, "message": "Complete repeating reminders on iPhone."}
    assert store.pending() == [] and sched.pokes == 0


def test_bad_input_never_raises_to_js(setup):
    _, _, api, _ = setup
    assert api.set_priority("Reminder/A", 7) == {"ok": False, "message": "That change couldn't be saved."}
    assert api.set_due("Reminder/A", "not a date", False)["ok"] is False
    assert api.rename("Reminder/A", "")["ok"] is False


def test_parse_due_naive_local_becomes_aware():   # Review Focus 2
    due = parse_due("2026-11-12T09:00", all_day=False)
    assert due.tzinfo is not None
    local = datetime(2026, 11, 12, 9, 0).astimezone()
    assert due == local


def test_parse_due_all_day_and_empty():
    assert parse_due("2026-11-12", all_day=True) == date(2026, 11, 12)
    assert parse_due("2026-11-12T09:00", all_day=True) == date(2026, 11, 12)
    assert parse_due(None, all_day=False) is None and parse_due("", all_day=True) is None


def test_set_due_stores_aware_and_labels(setup):
    _, _, api, _ = setup
    tomorrow = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    assert api.set_due("Reminder/A", tomorrow.strftime("%Y-%m-%dT%H:%M"), False)["ok"]
    a = [r for r in api.state()["reminders"] if r["id"] == "Reminder/A"][0]
    assert a["due_label"] == "Tomorrow 09:00" and a["overdue"] is False and a["all_day"] is False
    assert api.set_due("Reminder/A", "2020-01-01", True)["ok"]
    a = [r for r in api.state()["reminders"] if r["id"] == "Reminder/A"][0]
    assert a["all_day"] is True and a["overdue"] is True and a["due"] == "2020-01-01"


def test_state_never_contains_completed_or_deleted(setup):
    store, _, api, _ = setup
    store.save_remote([rem("Reminder/DONE", completed=True), rem("Reminder/GONE", deleted=True)], "s2")
    assert {"Reminder/DONE", "Reminder/GONE"}.isdisjoint(ids(api.state()))


def test_on_sync_records_status_and_pushes(setup):
    _, _, api, pushed = setup
    api.on_sync(SyncResult("offline"))
    assert api.state()["status"] == "offline" and pushed and pushed[-1]["status"] == "offline"


@pytest.mark.xfail(reason="banner_for is Martin's contribution", strict=False)
def test_banner_for_session_expired_offers_sign_in():
    b = banner_for("session_expired", [])
    assert b["text"] == "Sign in again to keep syncing." and b["action"] == "sign_in"


@pytest.mark.xfail(reason="banner_for is Martin's contribution", strict=False)
def test_banner_for_stopped_and_ok_and_notices():
    assert banner_for("stopped", [])["text"] == "iCloud changed something. Check for an update."
    assert banner_for("ok", []) is None
    b = banner_for("ok", ["A reminder you changed was deleted on another device."])
    assert b["text"].startswith("A reminder you changed") and b["action"] is None
