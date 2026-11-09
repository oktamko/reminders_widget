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
    assert keys == ["completed"]


def test_completed_false():
    f, keys = to_cloud_fields({"completed": False}, {}, UUID)
    assert f == {"Completed": {"value": 0}, "CompletionDate": {"value": None}}
    assert keys == ["completed"]


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
