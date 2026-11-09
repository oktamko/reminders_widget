"""Apple's "topotext" text CRDT, used for reminder titles (TitleDocument) and notes (NotesDocument).

A document is base64(gzip(protobuf)). Its text is a linked list of substrings, each named by
(replica index, clock); deleted substrings stay as tombstones. Sending a fresh document makes
the iPhone merge it with the old one (old text kept, new appended), so a change must edit the
existing document the way an iPhone does: `edit_text`. Anything this module does not model
raises `UnknownFormat`, so the caller leaves the field read-only instead of corrupting it.

Protobuf layout (field numbers): outer {1: varint, 2: doc}; doc {1, 2: varints, 3: note};
note {2: text, 3: substring*, 4: vector timestamp, 5: attribute run*}; substring {1: char_id,
2: length, 3: timestamp, 4: tombstone, 5: child*}; char_id/timestamp {1: replica, 2: clock};
vector timestamp {1: clock entry*}; clock entry {1: uuid, 2: replica clock*};
replica clock {1: varint, 2?: varint}.
"""
import base64
import gzip
import uuid
import zlib
from dataclasses import dataclass

from .errors import UnknownFormat
from .protobuf import (BYTES, VARINT, bytes_field, decompress, first_bytes_field, int_field,
                       iter_fields, unpack_varints)

LENGTH_UNIT = "utf16"  # confirmed by probe round 1: lengths count UTF-16 code units
START_ID = (0, 0)
END_ID = (0, 0xFFFFFFFF)
WRAPPER = {"outer": {1: 0}, "doc": {1: 0, 2: 0}}


@dataclass
class Substring:
    char_id: tuple[int, int]      # (replica index, clock); START_ID and END_ID are the sentinels
    length: int                   # UTF-16 units
    timestamp: tuple[int, int]
    tombstone: bool
    children: list[int]           # indices into Document.substrings


@dataclass
class Replica:
    uuid: bytes                   # 16 bytes
    clocks: list[dict[int, int]]  # raw ReplicaClock entries in order; [0][1] char clock, [1][1] timestamp clock


@dataclass
class Document:
    text: str                     # visible text only
    substrings: list[Substring]
    replicas: list[Replica]       # replica index i (1-based) = replicas[i-1]
    runs: list[bytes]             # raw attribute-run messages
    wrapper: dict                 # {"outer": {num: varint}, "doc": {num: varint}} written back unchanged


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


# --- reading ---

def _read(buf: bytes, what: str, singular: dict[int, int], repeated: dict[int, int] | None = None,
          packed: tuple[int, ...] = ()) -> dict:
    """Read one message, refusing unknown fields, wrong wire types and repeated singular fields.

    singular/repeated map field number -> wire type; `packed` repeated varints may also be wire 2.
    """
    repeated = repeated or {}
    values = {num: [] for num in repeated}
    try:
        for num, wire, value in iter_fields(buf):
            if singular.get(num) == wire:
                if num in values:
                    raise UnknownFormat(f"{what}: field {num} appears twice")
                values[num] = value
            elif repeated.get(num) == wire:
                values[num].append(value)
            elif num in packed and wire == BYTES:
                values[num].extend(unpack_varints(value))
            else:
                raise UnknownFormat(f"{what}: unexpected field {num} with wire type {wire}")
    except ValueError as exc:
        raise UnknownFormat(f"{what}: {exc}") from exc
    return values


def _required(values: dict, num: int, what: str):
    if num not in values:
        raise UnknownFormat(f"{what}: field {num} missing")
    return values[num]


def _parse_id(buf: bytes, what: str) -> tuple[int, int]:
    values = _read(buf, what, {1: VARINT, 2: VARINT})
    return values.get(1, 0), values.get(2, 0)


def _parse_substring(buf: bytes) -> Substring:
    values = _read(buf, "substring", {1: BYTES, 2: VARINT, 3: BYTES, 4: VARINT}, {5: VARINT}, packed=(5,))
    tombstone = values.get(4, 0)
    if tombstone not in (0, 1):
        raise UnknownFormat(f"substring: tombstone value {tombstone}")
    return Substring(_parse_id(_required(values, 1, "substring"), "char_id"), values.get(2, 0),
                     _parse_id(_required(values, 3, "substring"), "timestamp"), bool(tombstone), values[5])


def _parse_replicas(buf: bytes) -> list[Replica]:
    replicas = []
    for entry in _read(buf, "vector timestamp", {}, {1: BYTES})[1]:
        values = _read(entry, "clock entry", {1: BYTES}, {2: BYTES})
        replica_uuid = _required(values, 1, "clock entry")
        if len(replica_uuid) != 16:
            raise UnknownFormat(f"clock entry: uuid of {len(replica_uuid)} bytes")
        clocks = [_read(c, "replica clock", {1: VARINT, 2: VARINT}) for c in values[2]]
        replicas.append(Replica(replica_uuid, clocks))
    return replicas


def parse_document(b64: str) -> Document:
    """Parse a whole document; raises UnknownFormat on anything it does not model."""
    try:
        raw = decompress(base64.b64decode(b64, validate=True))
    except (TypeError, ValueError, OSError, EOFError, zlib.error) as exc:  # ValueError covers binascii.Error
        raise UnknownFormat(f"document: {exc}") from exc
    outer = _read(raw, "outer", {1: VARINT, 2: BYTES})
    doc = _read(_required(outer, 2, "outer"), "doc", {1: VARINT, 2: VARINT, 3: BYTES})
    note = _read(_required(doc, 3, "doc"), "note", {2: BYTES, 4: BYTES}, {3: BYTES, 5: BYTES})
    try:
        text = note.get(2, b"").decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnknownFormat("note: text is not UTF-8") from exc
    return Document(
        text=text,
        substrings=[_parse_substring(s) for s in note[3]],
        replicas=_parse_replicas(note[4]) if 4 in note else [],
        runs=note[5],
        wrapper={"outer": {k: v for k, v in outer.items() if k != 2},
                 "doc": {k: v for k, v in doc.items() if k != 3}},
    )


def decode_text(b64: str | None) -> str | None:
    """Visible text of a document, or None when it is not recognised. Reading is lenient:
    it only needs the text field, so documents `edit_text` would refuse still display."""
    if not b64:
        return None
    try:
        node = decompress(base64.b64decode(b64))
        for field in (2, 3, 2):  # outer -> doc -> note -> text
            node = first_bytes_field(node, field)
            if node is None:
                return None
        return node.decode("utf-8")
    except (ValueError, IndexError, OSError, EOFError, zlib.error):
        return None


# --- writing ---

def _position(replica: int, offset: int) -> bytes:
    """A char_id/timestamp message; offset -1 means 0xFFFFFFFF (kept for the probes)."""
    return int_field(1, replica) + int_field(2, 0xFFFFFFFF if offset == -1 else offset)


def _varints(values: dict[int, int]) -> bytes:
    return b"".join(int_field(num, value) for num, value in values.items())


def _substring_bytes(s: Substring) -> bytes:
    out = bytes_field(1, _position(*s.char_id)) + int_field(2, s.length) + bytes_field(3, _position(*s.timestamp))
    if s.tombstone:
        out += int_field(4, 1)
    return out + b"".join(int_field(5, child) for child in s.children)


def _replica_bytes(r: Replica) -> bytes:
    return bytes_field(1, r.uuid) + b"".join(bytes_field(2, _varints(clock)) for clock in r.clocks)


def serialize_document(doc: Document) -> str:
    """base64(gzip(protobuf)); children are written unpacked."""
    note = bytes_field(2, doc.text.encode("utf-8"))
    note += b"".join(bytes_field(3, _substring_bytes(s)) for s in doc.substrings)
    if doc.replicas:
        note += bytes_field(4, b"".join(bytes_field(1, _replica_bytes(r)) for r in doc.replicas))
    note += b"".join(bytes_field(5, run) for run in doc.runs)
    inner = _varints(doc.wrapper["doc"]) + bytes_field(3, note)
    outer = _varints(doc.wrapper["outer"]) + bytes_field(2, inner)
    return base64.b64encode(gzip.compress(outer, mtime=0)).decode("ascii")


def _link(substrings: list[Substring]) -> None:
    for i, s in enumerate(substrings):
        s.children = [i + 1] if i + 1 < len(substrings) else []


def _runs(length: int) -> list[bytes]:
    return [int_field(1, length)] if length else []


def encode_text(text: str, replica_uuid: bytes | None = None) -> str:
    """A new document with one replica: start -> text -> end."""
    replica_uuid = uuid.uuid4().bytes if replica_uuid is None else replica_uuid
    if len(replica_uuid) != 16:
        raise ValueError("replica uuid must be 16 bytes")
    length = utf16_len(text)
    substrings = [Substring(START_ID, 0, START_ID, False, [])]
    if length:
        substrings.append(Substring((1, 0), length, (1, 0), False, []))
    substrings.append(Substring(END_ID, 0, END_ID, False, []))
    _link(substrings)
    replicas = [Replica(replica_uuid, [{1: length}, {1: 1}])]
    wrapper = {"outer": dict(WRAPPER["outer"]), "doc": dict(WRAPPER["doc"])}
    return serialize_document(Document(text, substrings, replicas, _runs(length), wrapper))


# --- editing ---

def _check_editable(doc: Document) -> None:
    """Step 1: only the linear shape observed on real documents is edited."""
    subs = doc.substrings
    if len(subs) < 2 or subs[0].char_id != START_ID or subs[-1].char_id != END_ID:
        raise UnknownFormat("substrings do not run from the start to the end sentinel")
    for sentinel in (subs[0], subs[-1]):
        if sentinel.length or sentinel.tombstone:
            raise UnknownFormat("sentinel with length or tombstone")
    for i, s in enumerate(subs):
        if s.children != ([i + 1] if i + 1 < len(subs) else []):
            raise UnknownFormat("substring children are not linear")
    for s in subs[1:-1]:
        if not (1 <= s.char_id[0] <= len(doc.replicas) and 1 <= s.timestamp[0] <= len(doc.replicas)):
            raise UnknownFormat("substring names a replica outside the vector timestamp")
    if sum(s.length for s in subs[1:-1] if not s.tombstone) != utf16_len(doc.text):
        raise UnknownFormat("live substring lengths do not match the text")


def _own_replica(doc: Document, replica_uuid: bytes) -> int:
    """Step 2: 1-based index of our replica, appended when the document has not seen it."""
    matches = [i for i, r in enumerate(doc.replicas, 1) if r.uuid == replica_uuid]
    if len(matches) > 1:
        raise UnknownFormat("replica uuid listed twice")
    if not matches:
        doc.replicas.append(Replica(replica_uuid, [{1: 0}, {1: 1}]))
        return len(doc.replicas)
    index = matches[0]
    clocks = doc.replicas[index - 1].clocks
    if len(clocks) < 2 or set(clocks[0]) != {1} or set(clocks[1]) != {1}:
        raise UnknownFormat("own replica clocks have an unknown shape")
    for s in doc.substrings[1:-1]:
        if s.char_id[0] == index and s.char_id[1] + s.length > clocks[0][1]:
            raise UnknownFormat("own char clock is behind characters already written")
    return index


def edit_text(b64: str, new_text: str, replica_uuid: bytes) -> str:
    """Replace the whole visible text the way an iPhone does for a full rename."""
    if len(replica_uuid) != 16:
        raise ValueError("replica uuid must be 16 bytes")
    try:
        new_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("new_text contains a lone surrogate and cannot be stored") from exc
    if decode_text(b64) == new_text:
        return b64  # step 7: nothing to write
    doc = parse_document(b64)
    _check_editable(doc)
    r = _own_replica(doc, replica_uuid)
    clocks = doc.replicas[r - 1].clocks
    middle = doc.substrings[1:-1]
    # step 3, Lamport bound: above every substring timestamp and every replica's timestamp clock
    # (iPhone timestamp clocks run ahead of the timestamps visible on substrings)
    replica_clocks = [rep.clocks[1].get(1, 0) for rep in doc.replicas if len(rep.clocks) > 1]
    t = max([1 + s.timestamp[1] for s in middle] + replica_clocks)
    for s in middle:  # step 4
        if not s.tombstone:
            s.tombstone, s.timestamp = True, (r, t)
    length = utf16_len(new_text)
    if length:  # step 5
        doc.substrings.insert(1, Substring((r, clocks[0][1]), length, (r, 0), False, []))
        clocks[0][1] += length
    clocks[1][1] = t + 1  # step 6
    _link(doc.substrings)
    doc.text = new_text
    doc.runs = _runs(length)
    return serialize_document(doc)
