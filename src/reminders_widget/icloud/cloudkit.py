"""CloudKit web-API client for the Reminders zone, talking through an injected Transport."""
from __future__ import annotations

from typing import Protocol

from .errors import LockConflict, RecordGone, SessionExpired, UnknownResponse

CONTAINER_PATH = "database/1/com.apple.reminders/production/private"
ZONE = "Reminders"
MAX_CHANGE_PAGES = 1000  # a server that never stops paging is a bug, not a big zone


class Transport(Protocol):
    def post(self, url: str, body: dict) -> tuple[int, dict]:
        """Raises OfflineError on network failure."""
        ...


class CloudKit:
    def __init__(self, transport: Transport, base_url: str):
        self._transport = transport
        self._base = base_url.rstrip("/") + "/" + CONTAINER_PATH
        self._owner: str | None = None

    def _post(self, path: str, body: dict) -> dict:
        status, data = self._transport.post(f"{self._base}/{path}", body)
        if status in (401, 421):
            raise SessionExpired(f"HTTP {status} at {path}")
        if not 200 <= status < 300:
            raise UnknownResponse(f"HTTP {status} at {path}")
        if not isinstance(data, dict):
            raise UnknownResponse(f"unexpected body at {path}")
        return data

    @staticmethod
    def _check_error(item: dict) -> None:
        code = item.get("serverErrorCode")
        if not code:
            return
        if code in ("OP_LOCK_FAILURE", "CONFLICT"):
            raise LockConflict(code)
        if code == "NOT_FOUND":
            raise RecordGone(code)
        raise UnknownResponse(code)

    def _zone_id(self) -> dict:
        if self._owner is None:
            self.find_owner()
        return {"zoneName": ZONE, "ownerRecordName": self._owner}

    def find_owner(self) -> str:
        data = self._post("zones/list", {})
        zones = data.get("zones")
        if not isinstance(zones, list):
            raise UnknownResponse("zones/list has no zones")
        for zone in zones:
            try:
                zone_id = zone["zoneID"]
                if zone_id["zoneName"] == ZONE:
                    self._owner = zone_id["ownerRecordName"]
                    return self._owner
            except (KeyError, TypeError):
                raise UnknownResponse("malformed zone entry") from None
        raise UnknownResponse("Reminders zone not found")

    def changes(self, token: str | None) -> tuple[list[dict], str | None]:
        records: list[dict] = []
        zone_id = self._zone_id()
        for _ in range(MAX_CHANGE_PAGES):
            spec = {"zoneID": zone_id}
            if token:
                spec["syncToken"] = token
            data = self._post("changes/zone", {"zones": [spec]})
            try:
                zone = data["zones"][0]
            except (KeyError, IndexError, TypeError):
                raise UnknownResponse("changes/zone has no zones") from None
            if not isinstance(zone, dict):
                raise UnknownResponse("malformed changes/zone entry")
            self._check_error(zone)
            page = zone.get("records", [])
            if not isinstance(page, list):
                raise UnknownResponse("changes/zone records is not a list")
            records += page
            previous, token = token, zone.get("syncToken", token)
            if not zone.get("moreComing"):
                return records, token
            if token == previous:
                raise UnknownResponse("changes/zone says moreComing without a new token")
        raise UnknownResponse(f"changes/zone exceeded {MAX_CHANGE_PAGES} pages")

    def _single_record(self, path: str, body: dict) -> dict:
        data = self._post(path, body)
        try:
            record = data["records"][0]
        except (KeyError, IndexError, TypeError):
            raise UnknownResponse(f"{path} has no records") from None
        if not isinstance(record, dict):
            raise UnknownResponse(f"malformed record at {path}")
        self._check_error(record)
        return record

    def lookup(self, record_name: str) -> dict:
        return self._single_record("records/lookup", {
            "zoneID": self._zone_id(),
            "records": [{"recordName": record_name}],
        })

    def modify(self, operation: dict) -> dict:
        return self._single_record("records/modify", {
            "zoneID": self._zone_id(),
            "operations": [operation],
            "atomic": True,
        })
