import base64
import gzip
import json
import unittest
import zlib

from probe import decode_title, encode_title, title_shape
from probe_priority import set_priority_token


class TitleDocumentTest(unittest.TestCase):
    def test_round_trip(self):
        cases = {
            "diacritics": "Zavolať ľudí, kúpiť šťavu",
            # 'A' is byte 65: a length prefix that looks like a printable char
            "printable length prefix": "x" * 65,
            "multi-byte varint length": "dlhý názov " * 30,
        }
        for label, title in cases.items():
            with self.subTest(label):
                self.assertEqual(decode_title(encode_title(title)), title)

    def test_unrecognised_input_returns_none(self):
        for value in ("", None, "bm90IGEgZG9jdW1lbnQ=", "%%%"):
            with self.subTest(value):
                self.assertIsNone(decode_title(value))


class CompressionTest(unittest.TestCase):
    def test_decodes_zlib_and_uncompressed_documents(self):
        document = gzip.decompress(base64.b64decode(encode_title("Zavolať")))
        for label, raw in (("zlib", zlib.compress(document)), ("plain", document)):
            with self.subTest(label):
                self.assertEqual(decode_title(base64.b64encode(raw).decode()), "Zavolať")


class PriorityProbeTest(unittest.TestCase):
    def test_advances_existing_entry_only(self):
        raw = json.dumps({"map": {
            "priority": {"counter": 1, "modificationTime": 100.5, "replicaID": "A"},
            "completed": {"counter": 7, "modificationTime": 5, "replicaID": "A"},
        }})
        result = json.loads(set_priority_token(raw, 500.0, "B"))["map"]
        self.assertEqual(result["priority"], {"counter": 2, "modificationTime": 500.0, "replicaID": "A"})
        self.assertEqual(result["completed"]["counter"], 7)

    def test_time_always_moves_forward(self):
        raw = json.dumps({"map": {"priority": {"counter": 1, "modificationTime": 900.0}}})
        result = json.loads(set_priority_token(raw, 500.0, "B"))["map"]
        self.assertEqual(result["priority"]["modificationTime"], 901.0)

    def test_creates_missing_entry(self):
        raw = json.dumps({"map": {"completed": {"counter": 0}}})
        result = json.loads(set_priority_token(raw, 500.0, "B"))["map"]
        self.assertEqual(result["priority"], {"counter": 1, "modificationTime": 500.0, "replicaID": "B"})

    def test_returns_none_when_unparseable(self):
        for raw in (None, "not json", json.dumps({"other": {}})):
            with self.subTest(raw):
                self.assertIsNone(set_priority_token(raw, 500.0, "B"))


class TitleShapeTest(unittest.TestCase):
    def test_never_reveals_title_text(self):
        record = {"fields": {"TitleDocument": {"value": encode_title("tajny nazov")}}}
        self.assertNotIn("tajny", title_shape(record))

    def test_reports_missing_field_names(self):
        record = {"fields": {"Priority": {"value": 1}}}
        self.assertIn("bez TitleDocument", title_shape(record))
        self.assertIn("Priority", title_shape(record))


if __name__ == "__main__":
    unittest.main()
