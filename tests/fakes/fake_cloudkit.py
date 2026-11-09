"""In-memory stand-in for the iCloud Reminders CloudKit zone (implements Transport)."""
from __future__ import annotations

import copy

from reminders_widget.icloud.errors import OfflineError

PAGE_SIZE = 2


class FakeCloudKit:
    owner = "_fakeowner"

    def __init__(self, records: list[dict] | None = None):
        self.offline = False
        self.session_valid = True
        self.fail_next: list[str] = []
        # what a create of an existing record answers; Apple's documented code is EXISTS,
        # CONFLICT is kept so both paths of the client are tested
        self.duplicate_create_code = "EXISTS"
        self.writes: list[dict] = []
        self._counter = 0
        self._records: dict[str, dict] = {}
        self._seq: dict[str, int] = {}
        for record in records or []:
            self._store(copy.deepcopy(record))

    # --- test-side helpers -------------------------------------------------
    def phone_edit(self, record_name: str, **fields) -> None:
        record = self._records[record_name]
        for key, value in fields.items():
            record.setdefault("fields", {})[key] = {"value": value}
        self._touch(record)

    def phone_delete(self, record_name: str) -> None:
        self.phone_edit(record_name, Deleted=1)

    # --- internals ---------------------------------------------------------
    def _touch(self, record: dict) -> None:
        self._counter += 1
        record["recordChangeTag"] = f"t{self._counter}"
        self._seq[record["recordName"]] = self._counter

    def _store(self, record: dict) -> dict:
        record.setdefault("fields", {})
        self._records[record["recordName"]] = record
        self._touch(record)
        return record

    # --- Transport ---------------------------------------------------------
    def post(self, url: str, body: dict) -> tuple[int, dict]:
        if self.offline:
            raise OfflineError("fake offline")
        if not self.session_valid:
            return 421, {}
        endpoint = "/".join(url.split("/")[-2:])
        handler = {
            "zones/list": self._zones_list,
            "changes/zone": self._changes_zone,
            "records/lookup": self._lookup,
            "records/modify": self._modify,
        }.get(endpoint)
        if handler is None:
            return 404, {}
        return 200, handler(body)

    def _zones_list(self, body: dict) -> dict:
        return {"zones": [{"zoneID": {"zoneName": "Reminders",
                                      "ownerRecordName": self.owner}}]}

    def _changes_zone(self, body: dict) -> dict:
        spec = body["zones"][0]
        token = spec.get("syncToken")
        after = int(token[1:]) if token else 0
        pending = sorted((n for n, s in self._seq.items() if s > after),
                         key=lambda n: self._seq[n])
        page = pending[:PAGE_SIZE]
        more = len(pending) > PAGE_SIZE
        new_token = f"s{self._seq[page[-1]]}" if more else f"s{self._counter}"
        return {"zones": [{
            "zoneID": spec["zoneID"],
            "records": [copy.deepcopy(self._records[n]) for n in page],
            "syncToken": new_token,
            "moreComing": more,
        }]}

    def _lookup(self, body: dict) -> dict:
        out = []
        for ref in body["records"]:
            record = self._records.get(ref["recordName"])
            out.append(copy.deepcopy(record) if record else
                       {"recordName": ref["recordName"], "serverErrorCode": "NOT_FOUND",
                        "reason": "Record not found"})
        return {"records": out}

    def _modify(self, body: dict) -> dict:
        operation = body["operations"][0]
        self.writes.append(copy.deepcopy(operation))
        sent = operation["record"]
        if self.fail_next:
            return {"records": [self._error(sent, self.fail_next.pop(0))]}
        name = sent["recordName"]
        kind = operation["operationType"]
        stored = self._records.get(name)
        if kind == "create":
            if stored is not None:
                return {"records": [self._error(sent, self.duplicate_create_code)]}
            record = self._store(copy.deepcopy(sent))
        elif kind == "update":
            if stored is None:
                return {"records": [self._error(sent, "NOT_FOUND")]}
            if sent.get("recordChangeTag") != stored["recordChangeTag"]:
                return {"records": [self._error(sent, "OP_LOCK_FAILURE")]}
            stored["fields"].update(copy.deepcopy(sent.get("fields", {})))
            self._touch(stored)
            record = stored
        else:
            return {"records": [self._error(sent, "BAD_REQUEST")]}
        return {"records": [copy.deepcopy(record)]}

    @staticmethod
    def _error(record: dict, code: str) -> dict:
        return {"recordName": record.get("recordName"), "serverErrorCode": code,
                "reason": code}
