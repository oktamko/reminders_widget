"""Tiny protobuf wire-format reader and writer, enough for Apple's CRDT documents."""
import gzip
import zlib
from collections.abc import Iterator

VARINT, FIXED64, BYTES, FIXED32 = 0, 1, 2, 5


def varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative varint")
    out = bytearray()
    while value > 127:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def int_field(num: int, value: int) -> bytes:
    return varint(num << 3 | VARINT) + varint(value)


def bytes_field(num: int, data: bytes) -> bytes:
    return varint(num << 3 | BYTES) + varint(len(data)) + data


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Return (value, position after it); ValueError when truncated or longer than 64 bits."""
    value = shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        if shift > 63:
            raise ValueError("varint longer than 64 bits")
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def iter_fields(buf: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """Yield (field number, wire type, value): an int for varints, bytes otherwise."""
    pos = 0
    while pos < len(buf):
        tag, pos = read_varint(buf, pos)
        num, wire = tag >> 3, tag & 7
        if wire == VARINT:
            value, pos = read_varint(buf, pos)
        elif wire in (BYTES, FIXED64, FIXED32):
            if wire == BYTES:
                size, pos = read_varint(buf, pos)
            else:
                size = 8 if wire == FIXED64 else 4
            if pos + size > len(buf):
                raise ValueError("truncated field")
            value, pos = bytes(buf[pos:pos + size]), pos + size
        else:
            raise ValueError(f"unsupported wire type {wire}")
        yield num, wire, value


def unpack_varints(data: bytes) -> list[int]:
    """Values of a packed repeated varint field."""
    values, pos = [], 0
    while pos < len(data):
        value, pos = read_varint(data, pos)
        values.append(value)
    return values


def first_bytes_field(buf: bytes, wanted: int) -> bytes | None:
    """Return the first length-delimited field number `wanted`, or None."""
    for num, wire, value in iter_fields(buf):
        if num == wanted and wire == BYTES:
            return value
    return None


def decompress(raw: bytes) -> bytes:
    """Documents arrive gzip-compressed, zlib-compressed or uncompressed."""
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    if len(raw) >= 2 and raw[0] == 0x78 and int.from_bytes(raw[:2], "big") % 31 == 0:
        return zlib.decompress(raw)
    return raw
