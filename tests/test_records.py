import json
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from reminders_widget.icloud.crdt import decode_text, encode_text
from reminders_widget.icloud.errors import UnknownFormat
from reminders_widget.icloud.records import (
    Reminder,
    ReminderList,
    parse_record,
    to_cloud_fields,
)

RECORDS = json.loads((Path(__file__).parent / "fixtures" / "records.json").read_text("utf-8"))
UUID = bytes(range(16))
NOW = lambda: 1_700_000_000.5  # noqa: E731


def _reminder_rec(**fields):
    base = {"Completed": {"value": 0, "type": "NUMBER_INT64"}}
    base.update(fields)
    return {"recordName": "Reminder/X", "recordType": "Reminder", "recordChangeTag": "t1",
            "fields": base}


def test_every_fixture_record_parses():
    parsed = [parse_record(r) for r in RECORDS]
    assert all(p is not None for p in parsed)
    reminders = [p for p in parsed if isinstance(p, Reminder)]
    lists = [p for p in parsed if isinstance(p, ReminderList)]
    assert len(reminders) == 10 and len(lists) == 3
    for r in reminders:
        assert r.title.startswith("Sample ")
        assert not r.readonly
        assert r.list_id.startswith("List/")
        assert r.created is not None and r.created.tzinfo is not None
    assert sorted(l.name for l in lists) == ["List 1", "List 2", "List 3"]
    assert all(l.color is None for l in lists)  # fixture colours are anonymised


def test_fixture_due_dates_and_notes():
    by_all_day = [p for p in map(parse_record, RECORDS) if isinstance(p, Reminder) and p.due]
    timed = [p for p in by_all_day if isinstance(p.due, datetime)]
    allday = [p for p in by_all_day if not isinstance(p.due, datetime)]
    assert timed and allday
    assert all(p.due.tzinfo is not None for p in timed)
    assert all(isinstance(p.due, date) for p in allday)
    assert any(p.notes is not None for p in map(parse_record, RECORDS) if isinstance(p, Reminder))


def test_other_record_type_is_none():
    assert parse_record({"recordName": "Alarm/1", "recordType": "Alarm", "fields": {}}) is None


def test_list_accepts_reminderlist_type_and_hex_colour():
    rec = {"recordName": "List/1", "recordType": "ReminderList", "recordChangeTag": "a",
           "fields": {"Name": {"value": "N"}, "Color": {"value": "#FF8800"}}}
    lst = parse_record(rec)
    assert lst == ReminderList("List/1", "N", "#FF8800")


def test_list_colour_from_icloud_json():   # probe round 4: Color is a JSON string
    colour = json.dumps({"red": 0, "green": 0.478, "blue": 1, "alpha": 1, "colorRGBSpace": 1,
                         "daHexString": "#007AFF", "ckSymbolicColorName": "blue", "daSymbolicColorName": "blue"})
    rec = {"recordName": "List/2", "recordType": "List", "recordChangeTag": "a",
           "fields": {"Name": {"value": "N"}, "Color": {"value": colour}}}
    assert parse_record(rec).color == "#007AFF"
    for bad in ('{"daHexString": "blue"}', "{not json", '["#007AFF"]'):
        rec["fields"]["Color"]["value"] = bad
        assert parse_record(rec).color is None


def test_undecodable_title_is_readonly():
    rec = _reminder_rec(TitleDocument={"value": "bm90IGEgZG9jdW1lbnQ=", "type": "ENCRYPTED_BYTES"})
    r = parse_record(rec)
    assert r.readonly is True and r.title is None


def test_undecodable_notes_is_readonly():
    rec = _reminder_rec(TitleDocument={"value": encode_text("T")},
                        NotesDocument={"value": "bm90IGEgZG9jdW1lbnQ="})
    r = parse_record(rec)
    assert r.readonly and r.title == "T" and r.notes is None


def test_no_notes_not_readonly():
    r = parse_record(_reminder_rec(TitleDocument={"value": encode_text("T")}))
    assert not r.readonly and r.notes is None and r.priority == 0


def test_all_day_and_timed_due_parse():
    r = parse_record(_reminder_rec(DueDate={"value": 1797292800000}, AllDay={"value": 1}))
    assert r.due == date(2026, 12, 15)
    assert not isinstance(r.due, datetime)
    t = parse_record(_reminder_rec(DueDate={"value": 1794772800000}, AllDay={"value": 0}))
    assert t.due == datetime.fromtimestamp(1794772800, timezone.utc)


def test_priority_and_completed_parse():
    r = parse_record(_reminder_rec(Priority={"value": 5}, Completed={"value": 1},
                                   Deleted={"value": 1}))
    assert r.priority == 5 and r.completed and r.deleted and r.change_tag == "t1"


def test_priority():
    assert to_cloud_fields({"priority": 5}, {}, UUID) == ({"Priority": {"value": 5}}, ["priority"])


def test_completed_true():
    f, keys = to_cloud_fields({"completed": True}, {}, UUID, now=NOW)
    assert f == {"Completed": {"value": 1}, "CompletionDate": {"value": 1_700_000_000_500}}
    assert keys == ["completed", "completionDate"]


def test_completed_false():
    f, keys = to_cloud_fields({"completed": False}, {}, UUID)
    assert f == {"Completed": {"value": 0}, "CompletionDate": {"value": None}}
    assert keys == ["completed", "completionDate"]


def test_due_none_datetime_date():
    f, k = to_cloud_fields({"due": None}, {}, UUID)
    assert f == {"DueDate": {"value": None}, "AllDay": {"value": 0}}
    assert k == ["dueDate", "allDay"]
    f, _ = to_cloud_fields({"due": datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)}, {}, UUID)
    assert f["DueDate"]["value"] == int(datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc).timestamp() * 1000)
    assert f["AllDay"] == {"value": 0}
    f, _ = to_cloud_fields({"due": date(2026, 12, 15)}, {}, UUID)
    assert f == {"DueDate": {"value": 1797292800000}, "AllDay": {"value": 1}}


def test_deleted_and_list():
    assert to_cloud_fields({"deleted": True}, {}, UUID) == ({"Deleted": {"value": 1}}, [])
    f, k = to_cloud_fields({"list_id": "List/9"}, {}, UUID)
    assert f == {"List": {"value": {"recordName": "List/9", "action": "NONE"}}}
    assert k == ["list"]


def test_title_rename_keeps_tombstone():
    old = encode_text("Old", UUID)
    f, k = to_cloud_fields({"title": "New"}, {"TitleDocument": {"value": old}}, bytes([7] * 16))
    new = f["TitleDocument"]["value"]
    assert decode_text(new) == "New" and k == ["titleDocument"]
    assert new != encode_text("New", UUID)
    assert len(new) > len(encode_text("New", UUID))  # old run retained as tombstone


def test_title_and_notes_without_existing_field():
    f, k = to_cloud_fields({"title": "A", "notes": "B"}, {}, UUID)
    assert decode_text(f["TitleDocument"]["value"]) == "A"
    assert decode_text(f["NotesDocument"]["value"]) == "B"
    assert k == ["titleDocument", "notesDocument"]


def test_unknown_format_propagates():
    cur = {"TitleDocument": {"value": "bm90IGEgZG9jdW1lbnQ="}}
    with pytest.raises(UnknownFormat):
        to_cloud_fields({"title": "x"}, cur, UUID)


def test_unknown_key_raises():
    with pytest.raises(KeyError):
        to_cloud_fields({"color": "x"}, {}, UUID)


# --- fix round 1: parse_record never raises; readonly mirrors the editor ---
def test_parse_never_raises_on_odd_shapes():
    r = parse_record(_reminder_rec(DueDate={"value": 1e20}))
    assert r.due is None and r.readonly
    rec = _reminder_rec()
    rec["created"] = {"timestamp": 1e20}
    r = parse_record(rec)
    assert r.created is None
    rec = _reminder_rec()
    rec["fields"] = None
    r = parse_record(rec)
    assert r.title == "" and not r.readonly
    lst = parse_record({"recordName": "List/1", "recordType": "List", "fields": None})
    assert lst.name == ""
    r = parse_record(_reminder_rec(Priority={"value": "x"}))
    assert r.priority == 0 and r.readonly


def test_null_documents_are_readonly():
    assert parse_record(_reminder_rec(TitleDocument={"value": None})).readonly
    assert parse_record(_reminder_rec(NotesDocument={"value": None})).readonly


def test_document_editor_would_refuse_is_readonly():
    from reminders_widget.icloud.crdt import Document, Replica, Substring, serialize_document
    from reminders_widget.icloud.protobuf import int_field
    start, end = (0, 0), (0, 0xFFFFFFFF)
    subs = [Substring(start, 0, start, False, [1]), Substring((1, 0), 1, (1, 0), False, [2]),
            Substring(end, 0, end, False, [])]
    reps = [Replica(bytes([9] * 16), [{1: 1}, {1: 1}]), Replica(bytes([1] * 16), [{1: 0}, {1: 1}])]
    doc = Document("a", subs, reps, [int_field(1, 1)], {"outer": {1: 0}, "doc": {1: 0, 2: 0}})
    b64 = serialize_document(doc)
    assert decode_text(b64) == "a"  # structurally readable, but unsorted replicas
    assert parse_record(_reminder_rec(TitleDocument={"value": b64})).readonly


# --- fix round 2 ---
def test_overflow_priority_is_readonly():
    r = parse_record(_reminder_rec(Priority={"value": float("inf")}))
    assert r.priority == 0 and r.readonly


def test_non_dict_field_shapes():
    r = parse_record(_reminder_rec(TitleDocument=None))
    assert r.readonly and r.title is None
    r = parse_record(_reminder_rec(NotesDocument="abc"))
    assert r.readonly
    r = parse_record(_reminder_rec(Priority=None, DueDate="x", AllDay=3, Completed=None))
    assert r.priority == 0 and r.due is None and not r.completed and not r.readonly


def test_parse_record_passes_replica_uuid_to_editor_checks():
    from reminders_widget.icloud.crdt import Replica
    import tests.test_crdt as tc
    bad = tc.make_doc("ab", [tc.sub((1, 0), 2, (1, 0))],
                      [Replica(tc.IPHONE, [{1: 2}, {1: 1}]), Replica(tc.PC, [{1: 0, 2: 5}, {1: 1}])])
    rec = _reminder_rec(TitleDocument={"value": bad})
    assert not parse_record(rec).readonly
    assert parse_record(rec, tc.PC).readonly


# --- fix round 2 (task 10 review): never None into NOT NULL columns ----------

def test_null_change_tag_becomes_empty():
    rec = _reminder_rec(TitleDocument={"value": encode_text("T")})
    rec["recordChangeTag"] = None
    r = parse_record(rec)
    assert r.change_tag == "" and not r.readonly
    rec["recordChangeTag"] = 7
    assert parse_record(rec).change_tag == ""


@pytest.mark.parametrize("ref", [{"action": "NONE"}, {"recordName": None}, {"recordName": 5}, "List/1"])
def test_unreadable_list_reference_is_readonly(ref):
    r = parse_record(_reminder_rec(TitleDocument={"value": encode_text("T")}, List={"value": ref}))
    assert r.readonly and r.list_id == ""


def test_readable_list_reference_and_missing_list():
    r = parse_record(_reminder_rec(TitleDocument={"value": encode_text("T")},
                                   List={"value": {"recordName": "List/1", "action": "NONE"}}))
    assert r.list_id == "List/1" and not r.readonly
    r = parse_record(_reminder_rec(TitleDocument={"value": encode_text("T")}))
    assert r.list_id == "" and not r.readonly


def test_deleted_list_record():
    rec = {"recordName": "List/1", "recordType": "List",
           "fields": {"Name": {"value": "N"}, "Deleted": {"value": 1}}}
    assert parse_record(rec).deleted
    rec["fields"]["Deleted"]["value"] = 0
    assert not parse_record(rec).deleted


# --- final review fixes -------------------------------------------------------

def _fixture(kind):
    return [r for r in RECORDS if r["recordType"] == kind]


def test_recurring_from_fixture():
    parsed = {r["recordName"]: parse_record(r) for r in _fixture("Reminder")}
    recurring = {name for name, p in parsed.items() if p.recurring}
    expected = {r["recordName"] for r in _fixture("Reminder")
                if (r["fields"].get("RecurrenceRuleIDs") or {}).get("value")}
    assert len(expected) == 2 and recurring == expected


@pytest.mark.parametrize("field, recurring", [
    (None, False),
    ({"value": [], "type": "EMPTY_LIST"}, False),
    ({"value": None}, False),
    ({"value": "x"}, False),
    ({"value": ["a"], "type": "STRING_LIST"}, True),
])
def test_recurring_needs_non_empty_list(field, recurring):
    fields = {"TitleDocument": {"value": encode_text("T")}}
    if field is not None:
        fields["RecurrenceRuleIDs"] = field
    assert parse_record(_reminder_rec(**fields)).recurring is recurring


def test_list_is_group():
    assert all(parse_record(r).is_group is False for r in _fixture("List"))
    rec = {"recordName": "List/G", "recordType": "List",
           "fields": {"Name": {"value": "G"}, "IsGroup": {"value": 1}}}
    assert parse_record(rec).is_group is True
    del rec["fields"]["IsGroup"]
    assert parse_record(rec).is_group is False


def test_completed_advances_completion_date_token():
    _, keys = to_cloud_fields({"completed": True}, {}, UUID, now=NOW)
    assert keys == ["completed", "completionDate"]


def test_new_reminder_name():
    import uuid
    from reminders_widget.icloud.records import new_reminder_name
    a, b = new_reminder_name(), new_reminder_name()
    assert a != b and a.startswith("Reminder/")
    tail = a[len("Reminder/"):]
    assert tail == tail.upper() and str(uuid.UUID(tail)).upper() == tail


def test_list_reminder_order_from_reminder_ids():   # probe round 4: JSON list, not always complete
    rec = {"recordName": "List/2", "recordType": "List", "recordChangeTag": "a",
           "fields": {"Name": {"value": "N"}, "ReminderIDs": {"value": '["B-2", "Reminder/A-1"]'}}}
    assert parse_record(rec).order == ("B-2", "A-1")
    for bad in ("x", '{"a": 1}', "[1, 2]"):
        rec["fields"]["ReminderIDs"]["value"] = bad
        assert parse_record(rec).order == ()


def test_reminder_with_alarm_ids_has_an_alert():   # probe round 4: the alert does not follow the due date
    assert parse_record(_reminder_rec(AlarmIDs={"value": ["Alarm/1"]})).alarmed is True
    assert parse_record(_reminder_rec(AlarmIDs={"value": []})).alarmed is False
    assert parse_record(_reminder_rec()).alarmed is False
