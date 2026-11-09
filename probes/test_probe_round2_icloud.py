import json
import uuid

import pytest

from probe_round2_icloud import (NOTES_FIELD, TITLE_FIELD, TOKEN_MAP_FIELD, matches_expected,
                                 text_edit_fields)
from reminders_widget.icloud.crdt import UnknownFormat, decode_text, edit_text, encode_text

IPHONE = uuid.UUID("11111111-2222-3333-4444-555555555555").bytes
PC = uuid.UUID("99999999-8888-7777-6666-555555555555").bytes
NOW = 1000.0


def record_fields(title="Old", notes=None, tokens=None):
    fields = {TITLE_FIELD: {"value": encode_text(title, IPHONE)}}
    if notes is not None:
        fields[NOTES_FIELD] = {"value": encode_text(notes, IPHONE)}
    if tokens is not None:
        fields[TOKEN_MAP_FIELD] = {"value": json.dumps({"map": tokens})}
    return fields


def test_title_edit_replaces_text_and_advances_title_token():
    tokens = {"titleDocument": {"counter": 2, "modificationTime": 5.0, "replicaID": "R"}}
    fields = record_fields(tokens=tokens)
    out = text_edit_fields(fields, TITLE_FIELD, "titleDocument", "New 🥛", PC, NOW)
    assert decode_text(out[TITLE_FIELD]) == "New 🥛"
    token = json.loads(out[TOKEN_MAP_FIELD])["map"]["titleDocument"]
    assert token["counter"] == 3 and token["modificationTime"] == NOW


def test_edit_matches_edit_text_directly():
    fields = record_fields()
    out = text_edit_fields(fields, TITLE_FIELD, "titleDocument", "New", PC, NOW)
    assert decode_text(edit_text(fields[TITLE_FIELD]["value"], "New", PC)) == decode_text(out[TITLE_FIELD])


def test_notes_edit_creates_token_when_missing_and_keeps_other_entries():
    other = {"dueDate": {"counter": 1, "modificationTime": 1.0, "replicaID": "R"}}
    fields = record_fields(notes="from phone", tokens=other)
    out = text_edit_fields(fields, NOTES_FIELD, "notesDocument", "from pc", PC, NOW)
    assert decode_text(out[NOTES_FIELD]) == "from pc"
    entries = json.loads(out[TOKEN_MAP_FIELD])["map"]
    assert entries["dueDate"] == other["dueDate"]
    assert entries["notesDocument"]["counter"] == 1


def test_missing_token_map_is_started():
    out = text_edit_fields(record_fields(), TITLE_FIELD, "titleDocument", "New", PC, NOW)
    assert "titleDocument" in json.loads(out[TOKEN_MAP_FIELD])["map"]


def test_missing_note_field_gets_a_fresh_document():
    out = text_edit_fields(record_fields(), NOTES_FIELD, "notesDocument", "note", PC, NOW)
    assert decode_text(out[NOTES_FIELD]) == "note"


def test_unknown_format_propagates():
    fields = {TITLE_FIELD: {"value": "bm90IGEgZG9jdW1lbnQ="}}
    with pytest.raises(UnknownFormat):
        text_edit_fields(fields, TITLE_FIELD, "titleDocument", "New", PC, NOW)


def test_matches_expected():
    fields = record_fields(title="TEST z iPhone 1", notes="poznámka z iPhone")
    assert matches_expected(fields, title="TEST z iPhone 1", notes="poznámka z iPhone")
    assert matches_expected(fields, title="TEST z iPhone 1")
    assert not matches_expected(fields, title="TEST z iPhone 1", notes="iná")
    assert not matches_expected(fields, title="x")
    assert not matches_expected({}, title="x")
