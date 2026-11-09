import json

import pytest

from reminders_widget.icloud.errors import UnknownFormat
from reminders_widget.icloud.tokens import advance_tokens, apple_now


def test_advances_existing_entry_only():
    raw = json.dumps({"map": {
        "priority": {"counter": 1, "modificationTime": 100.5, "replicaID": "A"},
        "completed": {"counter": 7, "modificationTime": 5, "replicaID": "A"},
    }})
    result = json.loads(advance_tokens(raw, ["priority"], 500.0, "B"))["map"]
    assert result["priority"] == {"counter": 2, "modificationTime": 500.0, "replicaID": "A"}
    assert result["completed"]["counter"] == 7


def test_time_always_moves_forward():
    raw = json.dumps({"map": {"priority": {"counter": 1, "modificationTime": 900.0, "replicaID": "A"}}})
    result = json.loads(advance_tokens(raw, ["priority"], 500.0, "B"))["map"]
    assert result["priority"]["modificationTime"] == 901.0


def test_creates_missing_entry():
    raw = json.dumps({"map": {"completed": {"counter": 0}}})
    result = json.loads(advance_tokens(raw, ["priority"], 500.0, "B"))["map"]
    assert result["priority"] == {"counter": 1, "modificationTime": 500.0, "replicaID": "B"}


def test_empty_entry_gets_full_token():
    raw = json.dumps({"map": {"priority": {}}})
    result = json.loads(advance_tokens(raw, ["priority"], 500.0, "B"))["map"]
    assert result["priority"] == {"counter": 1, "modificationTime": 500.0, "replicaID": "B"}


def test_existing_replica_id_is_never_overwritten():
    raw = json.dumps({"map": {"priority": {"counter": 1, "modificationTime": 1.0, "replicaID": "A"}}})
    result = json.loads(advance_tokens(raw, ["priority"], 500.0, "B"))["map"]
    assert result["priority"]["replicaID"] == "A"


@pytest.mark.parametrize("field", ["counter", "modificationTime"])
@pytest.mark.parametrize("bad", ["3", None, True, [1]])
def test_non_numeric_token_field_raises(field, bad):
    token = {"counter": 1, "modificationTime": 1.0, "replicaID": "A"}
    token[field] = bad
    with pytest.raises(UnknownFormat):
        advance_tokens(json.dumps({"map": {"priority": token}}), ["priority"], 500.0, "B")


def test_none_starts_from_empty_map():
    result = json.loads(advance_tokens(None, ["priority"], 500.0, "B"))["map"]
    assert result == {"priority": {"counter": 1, "modificationTime": 500.0, "replicaID": "B"}}


@pytest.mark.parametrize("raw", ["not json", json.dumps({"other": {}}), json.dumps({"map": [1, 2]})])
def test_unknown_format_raises(raw):
    with pytest.raises(UnknownFormat):
        advance_tokens(raw, ["priority"], 500.0, "B")


def test_advances_several_keys_once_each():
    raw = json.dumps({"map": {"priority": {"counter": 2, "modificationTime": 10, "replicaID": "A"}}})
    out = json.loads(advance_tokens(raw, ["priority", "dueDate"], 50.0, "B"))["map"]
    assert out["priority"]["counter"] == 3
    assert out["dueDate"] == {"counter": 1, "modificationTime": 50.0, "replicaID": "B"}


def test_key_listed_twice_advances_once():
    raw = json.dumps({"map": {"priority": {"counter": 2, "modificationTime": 10, "replicaID": "A"}}})
    out = json.loads(advance_tokens(raw, ["priority", "priority"], 50.0, "B"))["map"]
    assert out["priority"]["counter"] == 3


def test_clock_behind_phone_still_moves_forward():
    raw = json.dumps({"map": {"priority": {"counter": 4, "modificationTime": 815935172.2, "replicaID": "A"}}})
    out = json.loads(advance_tokens(raw, ["priority"], 815935000.0, "B"))["map"]
    assert out["priority"]["modificationTime"] == 815935173.2
    assert out["priority"]["counter"] == 5


def test_keeps_unknown_top_level_keys():
    raw = json.dumps({"map": {}, "extra": 1})
    assert json.loads(advance_tokens(raw, ["priority"], 1.0, "B"))["extra"] == 1


def test_apple_now():
    assert apple_now(978307200.0) == 0.0
