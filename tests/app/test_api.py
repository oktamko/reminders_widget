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
    api._on_sync(SyncResult("offline"))
    assert api.state()["status"] == "offline" and pushed and pushed[-1]["status"] == "offline"


def test_banner_for_session_expired_offers_sign_in():
    b = banner_for("session_expired", [])
    assert b["text"] == "Sign in again to keep syncing." and b["action"] == "sign_in"


def test_banner_for_stopped_and_ok_and_notices():
    assert banner_for("stopped", [])["text"] == "iCloud changed something. Check for an update."
    assert banner_for("ok", []) is None
    b = banner_for("ok", ["A reminder you changed was deleted on another device."])
    assert b["text"].startswith("A reminder you changed") and b["action"] is None


def test_banner_for_offline_says_changes_wait_and_quiet_states_show_nothing():
    assert banner_for("offline", []) == {"text": "Offline. Your changes will sync when you're back online.",
                                         "action": None, "tone": "info"}
    assert banner_for("transient", []) is None and banner_for("error", []) is None


def test_banner_for_needs_action_beats_notices_and_notices_beat_offline():
    note = "A reminder you changed was deleted on another device."
    assert banner_for("session_expired", [note])["action"] == "sign_in"
    assert banner_for("offline", [note])["text"] == note
    assert banner_for("ok", [note, "Another."])["text"] == note + " Another."


def test_internal_hooks_are_not_exposed_to_js(setup):   # final review 15
    _, _, api, _ = setup
    public = {n for n in dir(api) if not n.startswith("_") and callable(getattr(api, n))}
    assert public == {"state", "select", "set_sort", "add", "complete", "set_priority",
                      "rename", "set_notes", "set_due", "sign_in", "set_mode", "open_update"}


def test_read_methods_never_raise_to_js(setup, monkeypatch):   # final review 15
    store, _, api, _ = setup
    monkeypatch.setattr(store, "lists", lambda **kw: (_ for _ in ()).throw(RuntimeError("db")))
    assert api.state()["ok"] is False
    assert api.select(["List/L1"])["ok"] is False
    assert api.set_sort("icloud")["ok"] is False


def test_state_reports_signed_in(setup):
    store, sched, _, _ = setup
    api = Api(store, sched, push=lambda s: None, signed_in=lambda: False)
    assert api.state()["signed_in"] is False
    assert Api(store, sched, push=lambda s: None).state()["signed_in"] is True


def test_state_version_increases_and_syncing_flag(setup):
    _, _, api, pushed = setup
    v1 = api.state()["version"]
    api._on_sync_start()
    assert pushed[-1]["syncing"] is True and pushed[-1]["version"] > v1
    api._on_sync(SyncResult("ok"))
    assert pushed[-1]["syncing"] is False and pushed[-1]["version"] > pushed[-2]["version"]


def test_state_marks_reminders_with_an_alert(setup):   # probe round 4
    store, _, api, _ = setup
    store.save_remote([rem("Reminder/AL", alarmed=True)], "s2")
    alarm = {r["id"]: r["alarm"] for r in api.state()["reminders"]}
    assert alarm["Reminder/AL"] is True and alarm["Reminder/A"] is False


UPDATE = {"version": "0.2.0", "url": "https://github.com/OWNER/apple-reminders-widget/releases/tag/v0.2.0"}


def test_banner_for_update_comes_last():
    assert banner_for("ok", [], UPDATE) == {"text": "Reminders Widget 0.2.0 is available.",
                                            "action": "open_update", "tone": "info"}
    assert banner_for("offline", [], UPDATE)["text"].startswith("Offline")


def test_update_reaches_the_state_and_opens_only_the_releases_page(setup, monkeypatch):
    from reminders_widget.project import releases_url
    _, _, api, pushed = setup
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url))
    api._set_update(dict(UPDATE, url=f"{releases_url()}/tag/v0.2.0"))
    assert pushed and pushed[-1]["banner"]["action"] == "open_update"
    assert api.open_update() == {"ok": True} and opened == [f"{releases_url()}/tag/v0.2.0"]
    api._set_update({"version": "0.2.0", "url": "https://evil.example/x"})
    assert api.open_update()["ok"] is False and len(opened) == 1


def test_open_update_accepts_other_capitalisation(setup, monkeypatch):   # final review 1
    from reminders_widget.project import REPO, releases_url
    _, _, api, _ = setup
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url))
    url = f"{releases_url()}/tag/v0.2.0".replace(REPO, REPO.upper())
    api._set_update({"version": "0.2.0", "url": url})
    assert api.open_update() == {"ok": True} and opened == [url]
