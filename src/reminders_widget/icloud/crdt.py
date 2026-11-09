"""Apple's "topotext" text CRDT, used for reminder titles (TitleDocument) and notes (NotesDocument).

A document is base64(gzip(protobuf)). Its text is a graph of substrings from a start to an end
sentinel, each named by (replica index, clock); deleted substrings stay as tombstones. Replica
indices are 1-based positions in the vector timestamp, which Apple keeps sorted by uuid bytes. Sending a fresh document makes
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
import bisect
import copy
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

def _check_graph(subs: list[Substring], end: int) -> None:
    """The substrings form a DAG in which every node lies on a path from start (index 0) to end."""
    n = len(subs)
    for s in subs:
        if len(set(s.children)) != len(s.children) or any(not 0 <= c < n for c in s.children):
            raise UnknownFormat("substring children are duplicated or out of range")
    indegree = [0] * n
    for s in subs:
        for c in s.children:
            indegree[c] += 1
    ready, visited = [i for i in range(n) if indegree[i] == 0], 0
    while ready:  # Kahn's algorithm: every node is visited only when the graph has no cycle
        i = ready.pop()
        visited += 1
        for c in subs[i].children:
            indegree[c] -= 1
            if not indegree[c]:
                ready.append(c)
    if visited != n:
        raise UnknownFormat("substring graph has a cycle")
    parents = [[] for _ in range(n)]
    for i, s in enumerate(subs):
        for c in s.children:
            parents[c].append(i)
    for origin, edges, what in ((0, [s.children for s in subs], "unreachable from start"),
                                (end, parents, "never reaches end")):
        seen, todo = {origin}, [origin]
        while todo:
            for nxt in edges[todo.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        if len(seen) != n:
            raise UnknownFormat(f"a substring is {what}")


def _check_editable(doc: Document) -> int:
    """Step 1: refuse every shape not seen on real documents. Returns the end sentinel's index."""
    subs = doc.substrings
    ends = [i for i, s in enumerate(subs) if s.char_id == END_ID]
    if not subs or subs[0].char_id != START_ID or len(ends) != 1:
        raise UnknownFormat("no start sentinel first or not exactly one end sentinel")
    end = ends[0]
    for sentinel in (subs[0], subs[end]):
        if sentinel.length or sentinel.tombstone:
            raise UnknownFormat("sentinel with length or tombstone")
    if subs[end].children:
        raise UnknownFormat("end sentinel has children")
    _check_graph(subs, end)
    middle = [s for i, s in enumerate(subs) if i not in (0, end)]
    for s in middle:
        if not (1 <= s.char_id[0] <= len(doc.replicas) and 1 <= s.timestamp[0] <= len(doc.replicas)):
            raise UnknownFormat("substring names a replica outside the vector timestamp")
    if sum(s.length for s in middle if not s.tombstone) != utf16_len(doc.text):
        raise UnknownFormat("live substring lengths do not match the text")
    uuids = [r.uuid for r in doc.replicas]
    if any(a >= b for a, b in zip(uuids, uuids[1:])):
        raise UnknownFormat("replicas are not sorted by uuid")  # Apple always sorts them
    return end


def _own_replica(doc: Document, replica_uuid: bytes) -> int:
    """Step 2: 1-based index of our replica. A new one goes to its uuid-sorted position, and
    every char_id/timestamp at or after that index moves up by one (sentinels use 0)."""
    uuids = [r.uuid for r in doc.replicas]
    position = bisect.bisect_left(uuids, replica_uuid)
    index = position + 1
    if replica_uuid not in uuids:
        for s in doc.substrings:
            if s.char_id[0] >= index:
                s.char_id = (s.char_id[0] + 1, s.char_id[1])
            if s.timestamp[0] >= index:
                s.timestamp = (s.timestamp[0] + 1, s.timestamp[1])
        doc.replicas.insert(position, Replica(replica_uuid, [{1: 0}, {1: 1}]))
        return index
    clocks = doc.replicas[position].clocks
    if len(clocks) < 2 or set(clocks[0]) != {1} or set(clocks[1]) != {1}:
        raise UnknownFormat("own replica clocks have an unknown shape")
    for s in doc.substrings:
        if s.char_id[0] == index and s.char_id[1] + s.length > clocks[0][1]:
            raise UnknownFormat("own char clock is behind characters already written")
    return index


def _load_editable(b64: str) -> tuple[Document, int]:
    doc = parse_document(b64)
    return doc, _check_editable(doc)


def check_editable(b64: str, replica_uuid: bytes | None = None) -> None:
    """Raise UnknownFormat when edit_text would refuse this document.

    With replica_uuid, also runs the own-replica checks on a copy."""
    doc, _ = _load_editable(b64)
    if replica_uuid is not None:
        _own_replica(copy.deepcopy(doc), replica_uuid)


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
    doc, end = _load_editable(b64)
    middle = [s for i, s in enumerate(doc.substrings) if i not in (0, end)]
    r = _own_replica(doc, replica_uuid)
    clocks = doc.replicas[r - 1].clocks
    # step 3, Lamport bound: above every substring timestamp and every replica's timestamp clock
    # (iPhone timestamp clocks run ahead of the timestamps visible on substrings)
    replica_clocks = [rep.clocks[1].get(1, 0) for rep in doc.replicas if len(rep.clocks) > 1]
    t = max([1 + s.timestamp[1] for s in middle] + replica_clocks)
    for s in middle:  # step 4
        if not s.tombstone:
            s.tombstone, s.timestamp = True, (r, t)
    length = utf16_len(new_text)
    if length:  # step 5: new run at list index 1, between start and start's old children
        start = doc.substrings[0]
        for s in doc.substrings:
            s.children = [c + 1 for c in s.children]
        doc.substrings.insert(1, Substring((r, clocks[0][1]), length, (r, 0), False, start.children))
        start.children = [1]
        clocks[0][1] += length
    clocks[1][1] = t + 1  # step 6
    doc.text = new_text
    doc.runs = _runs(length)
    return serialize_document(doc)
