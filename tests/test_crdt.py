import base64
import gzip
import zlib

import pytest

from reminders_widget.icloud.crdt import (
    LENGTH_UNIT, Document, Replica, Substring, decode_text, edit_text, encode_text, parse_document,
    serialize_document,
)
from reminders_widget.icloud.errors import UnknownFormat
from reminders_widget.icloud.protobuf import bytes_field, first_bytes_field, int_field, varint

START = (0, 0)
END = (0, 0xFFFFFFFF)
IPHONE = bytes(range(16))
OTHER = bytes(range(16, 32))
PC = bytes(range(32, 48))
WRAPPER = {"outer": {1: 0}, "doc": {1: 0, 2: 0}}


def sub(char_id, length, timestamp, tombstone=False):
    return Substring(char_id, length, timestamp, tombstone, [])


def linked(subs):
    for i, s in enumerate(subs):
        s.children = [] if i == len(subs) - 1 else [i + 1]
    return subs


def make_doc(text, middle, replicas, runs=None):
    subs = linked([sub(START, 0, START), *middle, sub(END, 0, END)])
    visible = sum(s.length for s in middle if not s.tombstone)
    runs = [int_field(1, visible)] if runs is None else runs
    return serialize_document(Document(text, subs, replicas, runs, WRAPPER))


def note_of(b64):
    outer = gzip.decompress(base64.b64decode(b64))
    return first_bytes_field(first_bytes_field(outer, 2), 3)


def wrap_note(note):
    doc = int_field(1, 0) + int_field(2, 0) + bytes_field(3, note)
    return base64.b64encode(gzip.compress(int_field(1, 0) + bytes_field(2, doc))).decode()


def _note_length(document):
    note = first_bytes_field(first_bytes_field(document, 2), 3)
    run = first_bytes_field(note, 5)
    assert run[0] == 0x08 and len(run) == 2  # run = {1: one-byte varint}
    return run[1]


# --- ported from probes/test_probe.py ---

@pytest.mark.parametrize("title", [
    "Zavolať ľudí, kúpiť šťavu",
    "x" * 65,  # 'A' is byte 65: a length prefix that looks like a printable char
    "dlhý názov " * 30,  # multi-byte varint length
])
def test_round_trip(title):
    assert decode_text(encode_text(title)) == title


@pytest.mark.parametrize("value", ["", None, "bm90IGEgZG9jdW1lbnQ=", "%%%"])
def test_unrecognised_input_returns_none(value):
    assert decode_text(value) is None


@pytest.mark.parametrize("compress", [zlib.compress, lambda raw: raw])
def test_decodes_zlib_and_uncompressed_documents(compress):
    document = gzip.decompress(base64.b64decode(encode_text("Zavolať")))
    assert decode_text(base64.b64encode(compress(document)).decode()) == "Zavolať"


def test_emoji_round_trip():
    assert decode_text(encode_text("Kúpiť 🥛 a 🍞")) == "Kúpiť 🥛 a 🍞"


def test_length_fields_use_confirmed_unit():
    doc = gzip.decompress(base64.b64decode(encode_text("a🥛")))
    expected = 2 if LENGTH_UNIT == "codepoints" else 3
    assert _note_length(doc) == expected


# --- encode_text shape ---

def test_encode_text_matches_old_encoder_shape():
    doc = parse_document(encode_text("Ahoj 🥛", PC))
    assert doc.text == "Ahoj 🥛"
    assert doc.substrings == linked([sub(START, 0, START), sub((1, 0), 7, (1, 0)), sub(END, 0, END)])
    assert doc.replicas == [Replica(PC, [{1: 7}, {1: 1}])]
    assert doc.runs == [int_field(1, 7)]
    assert doc.wrapper == WRAPPER


def test_encode_text_writes_gzip_and_random_uuid():
    a, b = encode_text("x"), encode_text("x")
    assert base64.b64decode(a)[:2] == b"\x1f\x8b"
    assert parse_document(a).replicas[0].uuid != parse_document(b).replicas[0].uuid
    assert len(parse_document(a).replicas[0].uuid) == 16


def test_encode_empty_text():
    doc = parse_document(encode_text("", PC))
    assert doc.text == "" and doc.runs == []
    assert [s.char_id for s in doc.substrings] == [START, END]
    assert decode_text(encode_text("", PC)) == ""


# --- edit_text (amendment) ---

def test_edit_single_replica_matches_iphone_shape():
    before = make_doc("Kúpiť mlieko dnes", [sub((1, 0), 17, (1, 0))], [Replica(IPHONE, [{1: 17}, {1: 1}])])
    new = "Kúpiť chlieb"
    n = len(new)
    after = parse_document(edit_text(before, new, PC))
    T = 1
    assert after.substrings == linked([
        sub(START, 0, START),
        sub((2, 0), n, (2, 0)),
        sub((1, 0), 17, (2, T), tombstone=True),
        sub(END, 0, END),
    ])
    assert after.replicas == [Replica(IPHONE, [{1: 17}, {1: 1}]), Replica(PC, [{1: n}, {1: T + 1}])]
    assert after.runs == [int_field(1, n)]
    assert after.wrapper == WRAPPER
    assert after.text == new
    assert decode_text(edit_text(before, new, PC)) == new


def test_edit_two_replica_iphone_document_with_known_replica():
    # start -> (1,28) len14 live ts(1,0) -> (2,0) len28 TOMB ts(1,8) -> end
    before = make_doc("abcdefghijklmn", [sub((1, 28), 14, (1, 0)), sub((2, 0), 28, (1, 8), tombstone=True)],
                      [Replica(IPHONE, [{1: 42}, {1: 9}]), Replica(OTHER, [{1: 28}, {1: 1}])])
    after = parse_document(edit_text(before, "nové", IPHONE))
    assert after.substrings == linked([
        sub(START, 0, START),
        sub((1, 42), 4, (1, 0)),
        sub((1, 28), 14, (1, 9), tombstone=True),
        sub((2, 0), 28, (1, 8), tombstone=True),
        sub(END, 0, END),
    ])
    assert after.replicas == [Replica(IPHONE, [{1: 46}, {1: 10}]), Replica(OTHER, [{1: 28}, {1: 1}])]


def test_edit_reuses_existing_replica():
    before = make_doc("Kúpiť mlieko dnes", [sub((1, 0), 17, (1, 0))], [Replica(IPHONE, [{1: 17}, {1: 1}])])
    once = edit_text(before, "abc", PC)
    twice = parse_document(edit_text(once, "abcd", PC))
    assert len(twice.replicas) == 2
    assert twice.substrings[1] == Substring((2, 3), 4, (2, 0), False, [2])
    assert twice.substrings[2] == Substring((2, 0), 3, (2, 2), True, [3])
    assert twice.substrings[3] == Substring((1, 0), 17, (2, 1), True, [4])
    assert twice.replicas[1] == Replica(PC, [{1: 7}, {1: 3}])
    assert decode_text(edit_text(once, "abcd", PC)) == "abcd"


def test_edit_keeps_already_tombstoned_runs_untouched():
    before = make_doc("Ahoj ty", [
        sub((1, 0), 5, (1, 0)),
        sub((1, 5), 3, (1, 4), tombstone=True),
        sub((1, 8), 2, (1, 0)),
    ], [Replica(IPHONE, [{1: 10}, {1: 5}])])
    after = parse_document(edit_text(before, "Nové", PC))
    T = 5  # 1 + highest timestamp clock (4)
    assert [(s.char_id, s.timestamp, s.tombstone) for s in after.substrings] == [
        (START, START, False),
        ((2, 0), (2, 0), False),
        ((1, 0), (2, T), True),
        ((1, 5), (1, 4), True),
        ((1, 8), (2, T), True),
        (END, END, False),
    ]
    assert after.replicas[1].clocks == [{1: 4}, {1: T + 1}]


def test_edit_timestamp_never_goes_below_own_clock():
    before = make_doc("ab", [sub((1, 0), 2, (1, 0))],
                      [Replica(IPHONE, [{1: 2}, {1: 1}]), Replica(PC, [{1: 0}, {1: 30}])])
    after = parse_document(edit_text(before, "c", PC))
    assert after.substrings[2].timestamp == (2, 30)
    assert after.replicas[1].clocks == [{1: 1}, {1: 31}]


def test_edit_to_empty_text():
    before = make_doc("Kúpiť mlieko dnes", [sub((1, 0), 17, (1, 0))], [Replica(IPHONE, [{1: 17}, {1: 1}])])
    out = edit_text(before, "", PC)
    after = parse_document(out)
    assert not any(not s.tombstone for s in after.substrings[1:-1])
    assert after.runs == []
    assert after.replicas[1].clocks == [{1: 0}, {1: 2}]
    assert decode_text(out) == ""


def test_edit_same_text_returns_input():
    before = make_doc("Kúpiť mlieko dnes", [sub((1, 0), 17, (1, 0))], [Replica(IPHONE, [{1: 17}, {1: 1}])])
    assert edit_text(before, "Kúpiť mlieko dnes", PC) == before


def test_edit_refuses_branching_children():
    subs = linked([sub(START, 0, START), sub((1, 0), 2, (1, 0)), sub(END, 0, END)])
    subs[0].children = [1, 2]
    b64 = serialize_document(Document("ab", subs, [Replica(IPHONE, [{1: 2}, {1: 1}])], [int_field(1, 2)], WRAPPER))
    with pytest.raises(UnknownFormat):
        edit_text(b64, "cd", PC)


@pytest.mark.parametrize("extra", [int_field(1, 0), bytes_field(6, b"x"), int_field(7, 3)])
def test_edit_refuses_unknown_note_field(extra):
    b64 = wrap_note(note_of(encode_text("ab", IPHONE)) + extra)
    with pytest.raises(UnknownFormat):
        edit_text(b64, "cd", PC)
    assert decode_text(b64) == "ab"  # reading still works


def test_lengths_are_utf16():
    before = make_doc("ab", [sub((1, 0), 2, (1, 0))], [Replica(IPHONE, [{1: 2}, {1: 1}])])
    after = parse_document(edit_text(before, "TEST 🥛", PC))
    assert after.substrings[1].length == 7
    assert after.replicas[1].clocks[0] == {1: 7}
    assert after.runs == [int_field(1, 7)]


@pytest.mark.parametrize("label, middle, replicas, text", [
    ("missing start", None, None, None),
    ("length mismatch", [sub((1, 0), 5, (1, 0))], [Replica(IPHONE, [{1: 5}, {1: 1}])], "ab"),
    ("unknown replica index", [sub((3, 0), 2, (1, 0))], [Replica(IPHONE, [{1: 2}, {1: 1}])], "ab"),
    ("sentinel id in the middle", [sub((0, 0), 2, (1, 0))], [Replica(IPHONE, [{1: 2}, {1: 1}])], "ab"),
    ("own clock behind own chars", [sub((1, 0), 2, (1, 0))], [Replica(PC, [{1: 1}, {1: 1}])], "ab"),
    ("own clocks malformed", [sub((1, 0), 2, (1, 0))], [Replica(PC, [{1: 2}])], "ab"),
    ("duplicate uuid", [sub((1, 0), 2, (1, 0))], [Replica(PC, [{1: 2}, {1: 1}]), Replica(PC, [{1: 0}, {1: 1}])], "ab"),
])
def test_edit_refuses_shapes_it_does_not_model(label, middle, replicas, text):
    if middle is None:
        subs = linked([sub((1, 0), 2, (1, 0)), sub(END, 0, END)])
        b64 = serialize_document(Document("ab", subs, [Replica(IPHONE, [{1: 2}, {1: 1}])], [], WRAPPER))
    else:
        b64 = make_doc(text, middle, replicas)
    with pytest.raises(UnknownFormat):
        edit_text(b64, "cd", PC)


@pytest.mark.parametrize("value", ["bm90IGEgZG9jdW1lbnQ=", "%%%", base64.b64encode(b"\x1f\x8b\x08").decode(), "éé"])
def test_parse_refuses_garbage(value):
    with pytest.raises(UnknownFormat):
        parse_document(value)


def test_edit_refuses_non_ascii_document():
    with pytest.raises(UnknownFormat):
        edit_text("éé", "cd", PC)


def test_edit_timestamp_is_above_every_replica_clock():
    # iPhone timestamp clocks jump ahead of the timestamps visible on substrings
    before = make_doc("ab", [sub((1, 0), 2, (1, 0))], [Replica(IPHONE, [{1: 2}, {1: 50}])])
    after = parse_document(edit_text(before, "cd", PC))
    assert after.substrings[2] == Substring((1, 0), 2, (2, 50), True, [3])
    assert after.replicas[1].clocks == [{1: 2}, {1: 51}]


def test_edit_rejects_lone_surrogate_text():
    before = make_doc("ab", [sub((1, 0), 2, (1, 0))], [Replica(IPHONE, [{1: 2}, {1: 1}])])
    with pytest.raises(ValueError, match="surrogate"):
        edit_text(before, "a\ud83e", PC)


@pytest.mark.parametrize("clocks", [[{1: 2, 2: 0}, {1: 1}], [{1: 2}, {1: 1, 2: 4}]])
def test_edit_refuses_extra_fields_in_own_clocks(clocks):
    before = make_doc("ab", [sub((1, 0), 2, (1, 0))], [Replica(PC, clocks)])
    with pytest.raises(UnknownFormat):
        edit_text(before, "cd", PC)


def test_parse_reads_packed_children_and_zlib():
    def pos(replica, clock):
        return int_field(1, replica) + int_field(2, clock)

    def raw_sub(char_id, length, ts, packed_children):
        out = bytes_field(1, pos(*char_id)) + int_field(2, length) + bytes_field(3, pos(*ts))
        if packed_children:
            out += bytes_field(5, b"".join(varint(c) for c in packed_children))
        return out

    entry = bytes_field(1, IPHONE) + bytes_field(2, int_field(1, 2)) + bytes_field(2, int_field(1, 1))
    note = (bytes_field(2, "ab".encode()) + bytes_field(3, raw_sub(START, 0, START, [1]))
            + bytes_field(3, raw_sub((1, 0), 2, (1, 0), [2])) + bytes_field(3, raw_sub(END, 0, END, []))
            + bytes_field(4, bytes_field(1, entry)) + bytes_field(5, int_field(1, 2)))
    outer = int_field(1, 0) + bytes_field(2, int_field(1, 0) + int_field(2, 0) + bytes_field(3, note))
    doc = parse_document(base64.b64encode(zlib.compress(outer)).decode())
    assert [s.children for s in doc.substrings] == [[1], [2], []]
    assert doc == parse_document(encode_text("ab", IPHONE))


def test_parse_serialize_round_trip():
    subs = linked([
        sub(START, 0, START),
        sub((1, 28), 6, (1, 0)),
        sub((2, 0), 28, (1, 8), tombstone=True),
        sub((2, 28), 14, (2, 0)),
        sub((1, 34), 3, (2, 0), tombstone=True),
        sub(END, 0, END),
    ])
    original = Document(
        "šesť🥛" + "a" * 14,
        subs,
        [Replica(IPHONE, [{1: 37}, {1: 9}]), Replica(OTHER, [{1: 42}, {1: 1, 2: 3}])],
        [int_field(1, 6), int_field(1, 14) + int_field(5, 0)],
        {"outer": {1: 0}, "doc": {1: 0, 2: 1}},
    )
    b64 = serialize_document(original)
    assert parse_document(b64) == original
    assert decode_text(b64) == original.text
    assert parse_document(serialize_document(parse_document(b64))) == original
