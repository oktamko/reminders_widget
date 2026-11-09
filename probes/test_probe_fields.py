import json

import pytest

from probe import decode_title, encode_title
from probe_fields import (advance_token, whitelist_record, sanitize_fixture, edit_arrived, anonymise_records, describe_field, detect_note_fields,
                          detect_due_fields, encode_title_len, new_fields, pick_token_key,
                          utf16_len)

UUID_A = "11111111-AAAA-BBBB-CCCC-111111111111"


# --- advance_token ---

def test_advance_token_bumps_existing_entry():
    raw = json.dumps({"map": {"dueDate": {"counter": 3, "modificationTime": 100.0, "replicaID": "R"}}})
    data = json.loads(advance_token(raw, "dueDate", 50.0, "NEW"))
    assert data["map"]["dueDate"] == {"counter": 4, "modificationTime": 101.0, "replicaID": "R"}


def test_advance_token_uses_now_when_later():
    raw = json.dumps({"map": {"x": {"counter": 1, "modificationTime": 10, "replicaID": "R"}}})
    assert json.loads(advance_token(raw, "x", 500, "NEW"))["map"]["x"]["modificationTime"] == 500


def test_advance_token_creates_missing_entry_and_missing_map():
    data = json.loads(advance_token(None, "notes", 42, "NEW"))
    assert data["map"]["notes"] == {"counter": 1, "modificationTime": 42, "replicaID": "NEW"}


def test_advance_token_keeps_other_entries():
    raw = json.dumps({"map": {"a": {"counter": 1, "modificationTime": 1, "replicaID": "R"}}})
    data = json.loads(advance_token(raw, "b", 5, "NEW"))
    assert data["map"]["a"]["counter"] == 1 and "b" in data["map"]


@pytest.mark.parametrize("raw", ["not json", json.dumps({"other": {}}), json.dumps([1])])
def test_advance_token_rejects_unknown_format(raw):
    with pytest.raises(ValueError):
        advance_token(raw, "x", 1, "NEW")


# --- emoji encoder ---

def test_utf16_len_counts_surrogate_pairs():
    assert utf16_len("TEST 🥛 mlieko") == len("TEST 🥛 mlieko") + 1
    assert utf16_len("abc") == 3


@pytest.mark.parametrize("title", ["TEST 🥛 mlieko", "Poznámka z PC", "abc"])
def test_encode_title_len_round_trips_with_both_rules(title):
    for length in (len(title), utf16_len(title)):
        assert decode_title(encode_title_len(title, length)) == title


def test_encode_title_len_embeds_the_given_length():
    # The length shows up as a varint in the uncompressed document; 200 needs two bytes.
    import base64, gzip
    short = gzip.decompress(base64.b64decode(encode_title_len("abc", 3)))
    long = gzip.decompress(base64.b64decode(encode_title_len("abc", 200)))
    assert len(long) > len(short)


def test_encode_title_len_matches_probe_encoder_for_bmp_text():
    import base64, gzip
    a = gzip.decompress(base64.b64decode(encode_title("Ahoj")))
    b = gzip.decompress(base64.b64decode(encode_title_len("Ahoj", 4)))
    assert len(a) == len(b)


# --- detection ---

def f(type_, value):
    return {"type": type_, "value": value}


def test_new_fields_lists_only_added_fields():
    before = {"Completed": f("INT64", 0), "TitleDocument": f("STRING", "x")}
    after = dict(before, DueDate=f("TIMESTAMP", 1700000000000), NotesDocument=f("STRING", "zz"))
    assert sorted(new_fields(before, after)) == ["DueDate", "NotesDocument"]


def test_describe_field_shows_value_only_for_numbers():
    assert describe_field("DueDate", f("TIMESTAMP", 1700000000000)) == \
        "DueDate: type=TIMESTAMP, python=int, hodnota=1700000000000"
    text = describe_field("NotesDocument", f("STRING", "secret note"))
    assert "secret" not in text and "python=str" in text
    assert "secret" not in describe_field("X", f("ENCRYPTED_BYTES", b"secret".decode()))
    assert "python=dict" in describe_field("Ref", f("REFERENCE", {"recordName": "secret"}))
    assert "secret" not in describe_field("Ref", f("REFERENCE", {"recordName": "secret"}))


def test_detect_due_fields_picks_new_timestamps():
    new = {"DueDate": f("TIMESTAMP", 1), "NotesDocument": f("STRING", "a"), "TimeZone": f("STRING", "b")}
    assert detect_due_fields(new) == ["DueDate"]
    assert detect_due_fields({"A": f("TIMESTAMP", 1), "B": f("TIMESTAMP", 2)}) == ["A", "B"]
    assert detect_due_fields({"N": f("STRING", "a")}) == []


def test_detect_note_fields_prefers_name_hint_then_single_candidate():
    new = {"DueDate": f("TIMESTAMP", 1), "NotesDocument": f("STRING", "a"), "TimeZone": f("STRING", "b")}
    assert detect_note_fields(new) == ["NotesDocument"]
    assert detect_note_fields({"Body": f("STRING", "a")}) == ["Body"]
    assert detect_note_fields({"A": f("STRING", "a"), "B": f("STRING", "b")}) == ["A", "B"]
    assert detect_note_fields({"DueDate": f("TIMESTAMP", 1)}) == []


def tokens(**kw):
    return {k: {"counter": c, "modificationTime": t, "replicaID": "R"} for k, (c, t) in kw.items()}


def test_pick_token_key_by_hint_among_changed_entries():
    before = tokens(title=(1, 10))
    after = tokens(title=(1, 10), dueDate=(1, 20), notes=(1, 20))
    assert pick_token_key(before, after, ["DueDate"], ["due"]) == ["dueDate"]
    assert pick_token_key(before, after, ["NotesDocument"], ["note"]) == ["notes"]


def test_pick_token_key_ignores_unchanged_when_requested():
    before = tokens(title=(1, 10), dueDate=(1, 20))
    after = tokens(title=(1, 10), dueDate=(1, 20))
    assert pick_token_key(before, after, ["DueDate"], ["due"]) == []
    assert pick_token_key(before, after, ["TitleDocument"], ["title"], changed_only=False) == ["title"]


def test_pick_token_key_prefers_exact_name_match_over_substring():
    after = tokens(dueDate=(1, 1), dueDateIncludesTime=(1, 1))
    assert pick_token_key({}, after, ["DueDate"], ["due"]) == ["dueDate"]


def test_pick_token_key_reports_ambiguity():
    after = tokens(dueA=(1, 1), dueB=(1, 1))
    assert pick_token_key({}, after, ["Whatever"], ["due"]) == ["dueA", "dueB"]


# --- anonymiser ---

def reminder(name, title, list_name, extra=None):
    fields = {
        "TitleDocument": f("STRING", encode_title(title)),
        "NotesDocument": f("STRING", encode_title("Tajná poznámka " + title)),
        "Completed": f("INT64", 0),
        "Priority": f("INT64", 5),
        "DueDate": f("TIMESTAMP", 1700000000000),
        "Hashtag": f("STRING", "#tajne"),
        "Url": f("STRING", "https://example.com/tajne"),
        "List": f("REFERENCE", {"recordName": list_name, "action": "NONE"}),
        "ResolutionTokenMap": f("STRING", json.dumps(
            {"map": {"title": {"counter": 2, "modificationTime": 5.5, "replicaID": "REP"}}})),
    }
    fields.update(extra or {})
    return {"recordName": name, "recordType": "Reminder", "recordChangeTag": "abc1",
            "created": {"timestamp": 1, "userRecordName": "_userSecret", "deviceID": "DEVSECRET"},
            "modified": {"timestamp": 2, "userRecordName": "_userSecret", "deviceID": "DEVSECRET"},
            "fields": fields}


def listrec(name, title):
    return {"recordName": name, "recordType": "List", "recordChangeTag": "def2",
            "fields": {"Name": f("STRING", title), "Color": f("STRING", "blue")}}


def sample_records():
    lists = [listrec(f"List/{UUID_A}", "Nákupy"), listrec("List/22222222-AAAA-BBBB-CCCC-222222222222", "Práca")]
    reminders = [reminder("Reminder/33333333-AAAA-BBBB-CCCC-333333333333", "Mlieko", lists[0]["recordName"]),
                 reminder("Reminder/44444444-AAAA-BBBB-CCCC-444444444444", "Chlieb", lists[1]["recordName"])]
    return reminders, lists


def test_anonymise_removes_every_original_string():
    reminders, lists = sample_records()
    dump = json.dumps(reminders + lists, ensure_ascii=False)
    out = anonymise_records(reminders, lists, note_fields=["NotesDocument"])
    text = json.dumps(out, ensure_ascii=False)
    for secret in ("Nákupy", "Práca", "tajne", "userSecret", "DEVSECRET",
                   UUID_A, "33333333", "44444444", "22222222", "example.com"):
        assert secret in dump, secret  # the sample really contains it
        assert secret not in text, secret
    # encoded documents are opaque in the dump, so check them through the decoder
    for rec in out:
        for field in rec["fields"].values():
            decoded = decode_title(field["value"]) if isinstance(field["value"], str) else None
            assert decoded is None or decoded.startswith("Sample ")


def test_anonymise_replaces_titles_and_names():
    reminders, lists = sample_records()
    out = anonymise_records(reminders, lists, note_fields=["NotesDocument"])
    rems = [r for r in out if r["recordType"] == "Reminder"]
    lsts = [r for r in out if r["recordType"] == "List"]
    assert [decode_title(r["fields"]["TitleDocument"]["value"]) for r in rems] == ["Sample 1", "Sample 2"]
    assert [decode_title(r["fields"]["NotesDocument"]["value"]) for r in rems] == ["Sample 1", "Sample 2"]
    assert [r["fields"]["Name"]["value"] for r in lsts] == ["List 1", "List 2"]


def test_anonymise_keeps_structure_numbers_and_tokens():
    reminders, lists = sample_records()
    out = anonymise_records(reminders, lists, note_fields=["NotesDocument"])
    first = out[0]
    assert set(first["fields"]) == set(reminders[0]["fields"])
    assert {k: v["type"] for k, v in first["fields"].items()} == \
        {k: v["type"] for k, v in reminders[0]["fields"].items()}
    assert first["fields"]["DueDate"]["value"] == 1700000000000
    assert first["fields"]["Priority"]["value"] == 5
    assert first["recordChangeTag"] == "abc1"
    assert first["created"]["timestamp"] == 1
    assert first["fields"]["ResolutionTokenMap"] == reminders[0]["fields"]["ResolutionTokenMap"]


def test_anonymise_record_names_keep_prefix_and_references_stay_consistent():
    reminders, lists = sample_records()
    out = anonymise_records(reminders, lists, note_fields=["NotesDocument"])
    names = [r["recordName"] for r in out]
    assert len(set(names)) == len(names)
    assert all(n.split("/")[0] in ("Reminder", "List") and "/" in n for n in names)
    list_names = {r["recordName"] for r in out if r["recordType"] == "List"}
    for rec in (r for r in out if r["recordType"] == "Reminder"):
        ref = rec["fields"]["List"]["value"]
        assert ref["recordName"] in list_names and ref["action"] == "NONE"
    # reminders pointing at different lists keep pointing at different lists
    refs = [r["fields"]["List"]["value"]["recordName"] for r in out if r["recordType"] == "Reminder"]
    assert refs[0] != refs[1]


def test_anonymise_maps_unknown_reference_targets_to_fresh_names():
    reminders, lists = sample_records()
    out = anonymise_records(reminders, lists[:1], note_fields=["NotesDocument"])
    assert "22222222" not in json.dumps(out)


# --- fix round 1 ---

def test_detect_due_prefers_due_named_timestamp():
    new = {"LastModifiedDate": f("TIMESTAMP", 1), "DueDate": f("TIMESTAMP", 2)}
    assert detect_due_fields(new) == ["DueDate"]


def test_detect_due_falls_back_to_changed_token_key():
    new = {"AlarmStamp": f("TIMESTAMP", 1), "CreationDate": f("TIMESTAMP", 2)}
    assert detect_due_fields(new, ["alarmstamp"]) == ["AlarmStamp"]
    assert detect_due_fields(new, []) == ["AlarmStamp", "CreationDate"]


def test_edit_arrived_waits_for_the_note_not_just_timezone():
    base = {"TitleDocument": f("STRING", "x")}
    due_tz = dict(base, DueDate=f("TIMESTAMP", 1), TimeZone=f("STRING", "Europe/Prague"))
    assert not edit_arrived(base, due_tz, True)
    assert edit_arrived(base, dict(due_tz, NotesDocument=f("STRING", "n")), True)
    assert not edit_arrived(base, dict(due_tz, NotesDocument=f("STRING", "n")), False)


def test_edit_arrived_accepts_a_new_note_token_key():
    base = {"TitleDocument": f("STRING", "x")}
    tok = json.dumps({"map": {"notes": {"counter": 1, "modificationTime": 1, "replicaID": "R"}}})
    after = dict(base, DueDate=f("TIMESTAMP", 1), ResolutionTokenMap=f("STRING", tok))
    assert edit_arrived(base, after, True)


def test_anonymise_scrubs_parent_share_and_keeps_zone_name():
    reminders, lists = sample_records()
    zone = {"zoneName": "Reminders", "ownerRecordName": "_ownerSecret", "zoneType": "REGULAR_CUSTOM_ZONE"}
    reminders[0]["parent"] = {"recordName": lists[0]["recordName"], "zoneID": dict(zone)}
    reminders[0]["share"] = {"recordName": "Share-SECRETSHARE", "zoneID": dict(zone)}
    reminders[0]["zoneID"] = dict(zone)
    reminders[0]["fields"]["Extra"] = f("REFERENCE_LIST", [{"recordName": "Reminder/SECRETCHILD", "zoneID": dict(zone)}])
    originals = [r["recordName"] for r in reminders + lists] + ["Share-SECRETSHARE", "SECRETCHILD", "_ownerSecret"]
    out = anonymise_records(reminders, lists, note_fields=["NotesDocument"])
    text = json.dumps(out)
    for original in originals:
        assert original not in text, original
    assert out[0]["parent"]["zoneID"]["zoneName"] == "Reminders"
    assert out[0]["zoneID"]["zoneName"] == "Reminders"
    assert out[0]["parent"]["recordName"] in {r["recordName"] for r in out}


ASSET_SECRETS = ["https://cvws.icloud-content.com/B/SECRETURL?e=1&k=SECRETK", "SECRETFILESUM",
                 "SECRETREFSUM", "SECRETWRAP", "SECRETRECEIPT"]


def asset_field():
    return f("ASSETID", {"downloadURL": ASSET_SECRETS[0], "fileChecksum": ASSET_SECRETS[1],
                         "referenceChecksum": ASSET_SECRETS[2], "wrappingKey": ASSET_SECRETS[3],
                         "receipt": ASSET_SECRETS[4], "size": 1234})


def test_anonymise_scrubs_asset_values_and_urls():
    reminders, lists = sample_records()
    reminders[0]["fields"]["BlobAsData"] = asset_field()
    reminders[0]["fields"]["ListOfBlobs"] = f("ASSETID_LIST", [asset_field()["value"]])
    reminders[0]["fields"]["Other"] = f("STRING", "http://example.org/secretpage")
    reminders[0]["fields"]["OtherList"] = f("STRING_LIST", ["https://x.example/SECRETLIST"])
    out = anonymise_records(reminders, lists, note_fields=["NotesDocument"])
    text = json.dumps(out)
    for secret in ASSET_SECRETS + ["secretpage", "SECRETLIST"]:
        assert secret not in text, secret
    blob = out[0]["fields"]["BlobAsData"]
    assert blob["type"] == "ASSETID"
    assert set(blob["value"]) == {"downloadURL", "fileChecksum", "referenceChecksum", "wrappingKey",
                                  "receipt", "size"}
    assert blob["value"]["size"] == 0 and blob["value"]["downloadURL"] == "x"


def test_sanitize_fixture_is_idempotent_and_keeps_tokens(tmp_path):
    reminders, lists = sample_records()
    reminders[0]["fields"]["BlobAsData"] = asset_field()
    path = tmp_path / "records.json"
    path.write_text(json.dumps(reminders + lists), encoding="utf-8")
    assert sanitize_fixture(path) == 4
    first = path.read_text(encoding="utf-8")
    assert not any(s in first for s in ASSET_SECRETS) and "http" not in first
    sanitize_fixture(path)
    assert path.read_text(encoding="utf-8") == first
    data = json.loads(first)
    assert data[0]["fields"]["ResolutionTokenMap"] == reminders[0]["fields"]["ResolutionTokenMap"]
    assert data[0]["recordName"] == reminders[0]["recordName"]


def test_anonymise_drops_unknown_top_level_keys_and_blanks_unknown_bytes():
    reminders, lists = sample_records()
    rec = reminders[0]
    rec.update({"chainPrivateKey": "SECRETKEY", "chainParentKey": "SECRETPARENT",
                "chainProtectionInfo": {"bytes": "SECRETPCS", "pcsChangeTag": "SECRETTAG"},
                "pluginFields": {"a": 1}, "somethingNew": "SECRETNEW"})
    rec["created"]["extra"] = "SECRETEXTRA"
    rec["fields"]["Blob"] = f("ENCRYPTED_BYTES", "SECRETBLOB")
    rec["fields"]["Enc"] = f("ENCRYPTED_STRING", "SECRETENC")
    out = anonymise_records(reminders, lists, note_fields=["NotesDocument"])
    text = json.dumps(out)
    for secret in ("SECRET", "chainPrivateKey", "chainParentKey", "chainProtectionInfo",
                   "pluginFields", "somethingNew", "pcsChangeTag"):
        assert secret not in text.replace("DEVSECRET", ""), secret
    assert set(out[0]) <= {"recordName", "recordType", "recordChangeTag", "fields", "deleted",
                           "zoneID", "parent", "share", "created", "modified"}
    assert out[0]["fields"]["Blob"]["value"] == "x" and out[0]["fields"]["Enc"]["value"] == "x"
    assert set(out[0]["created"]) == {"timestamp", "userRecordName", "deviceID"}
    assert decode_title(out[0]["fields"]["NotesDocument"]["value"]) == "Sample 1"
    assert out[0]["fields"]["Priority"]["value"] == 5


def test_whitelist_record_keeps_token_map_and_numbers():
    reminders, _ = sample_records()
    out = whitelist_record(reminders[0])
    assert out["fields"]["ResolutionTokenMap"] == reminders[0]["fields"]["ResolutionTokenMap"]
    assert out["fields"]["DueDate"]["value"] == 1700000000000
    assert out["fields"]["Hashtag"]["value"] == "x"


def test_sanitize_fixture_applies_whitelist(tmp_path):
    reminders, lists = sample_records()
    reminders[0]["chainPrivateKey"] = "KEY"
    reminders[0]["fields"]["Blob"] = f("ENCRYPTED_BYTES", "BLOBVALUE")
    path = tmp_path / "r.json"
    path.write_text(json.dumps(reminders + lists), encoding="utf-8")
    sanitize_fixture(path)
    text = path.read_text(encoding="utf-8")
    assert "chainPrivateKey" not in text and "BLOBVALUE" not in text
