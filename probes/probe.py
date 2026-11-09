"""Throwaway feasibility probe: can Windows read and change Apple Reminders via iCloud?

Run it yourself:  .venv/Scripts/python probe.py

- You sign in on Apple's own page in a separate Brave window. This script never
  reads or stores your password; the session lives in .session/ (Brave profile).
- It reads all reminders but writes ONLY to one test reminder it creates itself.
- probe-results.txt gets OK/FAIL per step and contains no reminder titles.

CloudKit record format taken from reading tarekbecker/icloud-reminders-cli.
"""
import base64
import json
import sys
import time
import uuid
import zlib
from collections import Counter
from pathlib import Path

from reminders_widget.icloud.crdt import _position  # noqa: F401  (used by probe_fields)
from reminders_widget.icloud.crdt import decode_text as decode_title, encode_text as encode_title
from reminders_widget.icloud.protobuf import bytes_field as _bytes_field  # noqa: F401
from reminders_widget.icloud.protobuf import decompress as _decompress
from reminders_widget.icloud.protobuf import first_bytes_field as _first_bytes_field
from reminders_widget.icloud.protobuf import int_field as _int_field  # noqa: F401
from reminders_widget.icloud.protobuf import read_varint as _read_varint

HERE = Path(__file__).parent
PROFILE_DIR = HERE.parent / ".session"
BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
RESULTS_FILE = HERE.parent / "probe-results.txt"
CONTAINER_PATH = "database/1/com.apple.reminders/production/private"
ZONE = "Reminders"
TEST_TITLE = "TEST widget – skúška, môžeš zmazať"
LOGIN_TIMEOUT_S = 300
ICLOUD_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://www.icloud.com",
    "Referer": "https://www.icloud.com/",
}


# --- TitleDocument: base64(gzip(protobuf CRDT document)), codec lives in the package ---

def _field_numbers(buf):
    numbers = []
    pos = 0
    while pos < len(buf):
        tag, pos = _read_varint(buf, pos)
        num, wire = tag >> 3, tag & 7
        numbers.append(f"{num}:{wire}")
        if wire == 0:
            _, pos = _read_varint(buf, pos)
        elif wire == 2:
            size, pos = _read_varint(buf, pos)
            pos += size
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            break
    return ",".join(dict.fromkeys(numbers))


def title_shape(rec):
    """Describe why a title did not decode, without revealing any of its text."""
    fields = rec.get("fields") or {}
    field = fields.get("TitleDocument")
    if not field:
        return "bez TitleDocument, polia: " + ",".join(sorted(fields))
    value = field.get("value")
    if not isinstance(value, str):
        return f"typ {field.get('type')}, hodnota {type(value).__name__}"
    try:
        raw = base64.b64decode(value)
        node = _decompress(raw)
        shape = f"hlavicka={raw[:2].hex()} rozbalene={node is not raw}"
        for label, wanted in (("outer", 2), ("doc", 3), ("note", 2)):
            shape += f" {label}=[{_field_numbers(node)}]"
            node = _first_bytes_field(node, wanted)
            if node is None:
                return shape + f" chyba pole {wanted}"
        return shape + " text nie je platne UTF-8"
    except (ValueError, IndexError, OSError, zlib.error) as exc:
        return f"chyba {type(exc).__name__}, hlavicka={base64.b64decode(value)[:2].hex()}"


# --- iCloud access through the signed-in browser session ---

class ICloud:
    def __init__(self, context, base_url):
        self.request = context.request
        self.base = base_url.rstrip("/") + "/" + CONTAINER_PATH
        self.owner = None

    def post(self, path, body):
        resp = self.request.post(f"{self.base}/{path}", headers=ICLOUD_HEADERS,
                                 data=json.dumps(body))
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status} pri {path}: {resp.text()[:300]}")
        return resp.json()

    def find_owner(self):
        for zone in self.post("zones/list", {}).get("zones", []):
            if zone["zoneID"]["zoneName"] == ZONE:
                self.owner = zone["zoneID"]["ownerRecordName"]
                return
        raise RuntimeError("Zóna Reminders sa nenašla")

    def changes(self, token=None):
        """Return (records, new_token), following pagination."""
        records = []
        while True:
            spec = {"zoneID": {"zoneName": ZONE, "ownerRecordName": self.owner}}
            if token:
                spec["syncToken"] = token
            zone = self.post("changes/zone", {"zones": [spec]})["zones"][0]
            records += zone.get("records", [])
            token = zone.get("syncToken", token)
            if not zone.get("moreComing"):
                return records, token

    def lookup(self, record_name):
        """Fetch the current server version of one record."""
        result = self.post("records/lookup", {
            "zoneID": {"zoneName": ZONE, "ownerRecordName": self.owner},
            "records": [{"recordName": record_name}],
        })
        record = result["records"][0]
        if record.get("serverErrorCode"):
            raise RuntimeError(f"{record['serverErrorCode']}: {record.get('reason')}")
        return record

    def modify(self, operation):
        result = self.post("records/modify", {
            "zoneID": {"zoneName": ZONE, "ownerRecordName": self.owner},
            "operations": [operation],
            "atomic": True,
        })
        record = result["records"][0]
        if record.get("serverErrorCode"):
            raise RuntimeError(f"{record['serverErrorCode']}: {record.get('reason')}")
        return record


class TestReminder:
    """The only record this probe ever writes to."""

    def __init__(self, cloud, list_ref, record_name):
        self.cloud = cloud
        self.name = record_name
        self.tag = None
        self.list_ref = list_ref

    def create(self):
        record = self.cloud.modify({"operationType": "create", "record": {
            "recordType": "Reminder",
            "recordName": self.name,
            "fields": {
                "TitleDocument": {"value": encode_title(TEST_TITLE)},
                "Completed": {"value": 0},
                "List": {"value": {"recordName": self.list_ref, "action": "NONE"}},
            },
        }})
        self.tag = record["recordChangeTag"]

    def field_names(self):
        return sorted(self.cloud.lookup(self.name).get("fields", {}))

    def update(self, **fields):
        # The iPhone rewrites a record after syncing it, so a saved tag goes stale.
        current = self.cloud.lookup(self.name)
        record = self.cloud.modify({"operationType": "update", "record": {
            "recordType": "Reminder",
            "recordName": self.name,
            "recordChangeTag": current["recordChangeTag"],
            "fields": {key: {"value": value} for key, value in fields.items()},
        }})
        self.tag = record["recordChangeTag"]
        return record["fields"]


def wait_for_login(context, page):
    """Wait until icloud.com reports a session; return the CloudKit base URL."""
    print(f"\nV novom okne Brave sa prihlás na stránke Apple (máš na to {LOGIN_TIMEOUT_S // 60} minút).")
    print("Ak sa objaví otázka, či dôverovať prehliadaču, zvoľ Dôverovať.\n")
    deadline = time.time() + LOGIN_TIMEOUT_S
    while time.time() < deadline:
        resp = context.request.post("https://setup.icloud.com/setup/ws/1/validate",
                                    headers=ICLOUD_HEADERS, data="null")
        if resp.ok:
            url = resp.json().get("webservices", {}).get("ckdatabasews", {}).get("url")
            if url:
                return url
        page.wait_for_timeout(5000)
    raise TimeoutError("Prihlásenie nebolo dokončené včas")


# --- Guided run ---

class Results:
    def __init__(self):
        self.lines = []

    def add(self, step, status, detail=""):
        line = f"{status:4} {step}" + (f" - {detail}" if detail else "")
        self.lines.append(line)
        print(f"  -> {line}")
        RESULTS_FILE.write_text("\n".join(self.lines) + "\n", encoding="utf-8")

    def run(self, step, action):
        """Run one step; a failure is recorded and does not abort the probe."""
        try:
            value = action()
        except Exception as exc:  # the point of a probe is to record what breaks
            self.add(step, "FAIL", str(exc)[:300])
            return None
        self.add(step, "OK")
        return value if value is not None else True

    def confirm(self, step, question):
        started = time.time()
        answer = input(f"\n{question} [a/n] ").strip().lower()
        status = "OK" if answer.startswith("a") else "FAIL"
        self.add(step, status, f"potvrdené po {time.time() - started:.0f} s")


def split_records(records):
    lists, reminders = {}, []
    for rec in records:
        fields = rec.get("fields") or {}
        if rec.get("deleted") or fields.get("Deleted", {}).get("value"):
            continue
        if rec.get("recordType") in ("List", "ReminderList"):
            name = fields.get("Name", {}).get("value") \
                or decode_title(fields.get("TitleDocument", {}).get("value"))
            lists[rec["recordName"]] = name or "(bez názvu)"
        elif rec.get("recordType") == "Reminder":
            reminders.append(rec)
    return lists, reminders


def title_of(rec):
    return decode_title(rec.get("fields", {}).get("TitleDocument", {}).get("value"))


def choose_list(lists):
    print("\nTvoje zoznamy:")
    refs = list(lists)
    for index, ref in enumerate(refs, 1):
        print(f"  {index}. {lists[ref]}")
    choice = input("Do ktorého zoznamu pridať testovaciu pripomienku? Číslo: ")
    return refs[int(choice) - 1]


def probe(cloud, results):
    results.run("zistenie vlastníka zóny", cloud.find_owner)
    if not cloud.owner:
        return

    fetched = results.run("načítanie všetkých záznamov", cloud.changes)
    if not fetched:
        return
    records, token = fetched
    lists, reminders = split_records(records)
    active = [r for r in reminders if not r["fields"].get("Completed", {}).get("value")]
    decoded = [t for t in map(title_of, reminders) if t]
    priorities = {}
    for rec in active:
        value = rec["fields"].get("Priority", {}).get("value", 0)
        priorities[value] = priorities.get(value, 0) + 1
    results.add("dekódovanie názvov", "OK" if len(decoded) == len(reminders) else "FAIL",
                f"{len(decoded)} z {len(reminders)}")
    shapes = Counter(title_shape(rec) for rec in reminders if not title_of(rec))
    for shape, count in shapes.most_common():
        results.add("nedekódované názvy", "INFO", f"{count}x {shape}")
    results.add("prehľad", "INFO", f"{len(lists)} zoznamov, {len(active)} aktívnych, "
                                   f"priority aktívnych: {priorities}")

    print("\nUkážka aktívnych pripomienok (skontroluj názvy a diakritiku):")
    for rec in active[:10]:
        print(f"  - {title_of(rec)}  [priorita {rec['fields'].get('Priority', {}).get('value', 0)}]")
    results.confirm("názvy sa zhodujú s iPhone", "Sedia názvy s tým, čo vidíš na iPhone?")

    if not lists:
        results.add("výber zoznamu", "FAIL", "nenašiel sa žiadny zoznam")
        return

    sample = reminders[0]["recordName"] if reminders else ""
    prefix = sample.split("/")[0] + "/" if "/" in sample else ""
    test = TestReminder(cloud, choose_list(lists), f"{prefix}{str(uuid.uuid4()).upper()}")

    if not results.run("vytvorenie testovacej pripomienky", test.create):
        return
    results.confirm("vytvorenie viditeľné na iPhone",
                    f'Vidíš na iPhone "{TEST_TITLE}"? Sync môže chvíľu trvať, pokojne počkaj.')
    results.run("polia po synchronizácii s iPhone",
                lambda: print("     polia:", ", ".join(test.field_names())))

    if results.run("priorita na vysokú", lambda: test.update(Priority=1)):
        results.confirm("vysoká priorita viditeľná na iPhone",
                        "Má testovacia pripomienka na iPhone vysokú prioritu (!!!)?")
    if results.run("priorita na nízku", lambda: test.update(Priority=9)):
        results.confirm("nízka priorita viditeľná na iPhone",
                        "Zmenila sa na iPhone na nízku prioritu (!)?")

    print("\nTeraz na iPhone vytvor novú pripomienku s názvom: TEST z iPhone")
    input("Potom stlač Enter...")
    started = time.time()
    found = False
    while time.time() - started < 120 and not found:
        changed, token = cloud.changes(token)
        found = any(title_of(r) == "TEST z iPhone" for r in changed)
        if not found:
            print(f"     čakám na iPhone... {time.time() - started:.0f} s z 120", flush=True)
            time.sleep(5)
    results.add("zmena z iPhone dorazila na PC", "OK" if found else "FAIL",
                f"po {time.time() - started:.0f} s")

    now_ms = int(time.time() * 1000)
    if results.run("odškrtnutie", lambda: test.update(Completed=1, CompletionDate=now_ms)):
        results.confirm("odškrtnutie viditeľné na iPhone",
                        "Je testovacia pripomienka na iPhone odškrtnutá?")

    if results.run("upratanie testovacej pripomienky", lambda: test.update(Deleted=1)):
        results.confirm("zmazanie viditeľné na iPhone",
                        "Zmizla testovacia pripomienka z iPhone (aj medzi hotovými)?")
    print('\nPripomienku "TEST z iPhone" si zmaž na iPhone sám, skript ju nemení.')


def launch_browser(pw):
    """Open Brave with the probe's own profile, separate from your normal one."""
    return pw.chromium.launch_persistent_context(
        str(PROFILE_DIR), executable_path=BRAVE_PATH, headless=False, no_viewport=True,
        chromium_sandbox=True)


def main():
    from playwright.sync_api import sync_playwright

    sys.stdout.reconfigure(encoding="utf-8")
    results = Results()
    with sync_playwright() as pw:
        context = launch_browser(pw)
        page =context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.icloud.com/reminders/")
        try:
            url = wait_for_login(context, page)
            results.add("prihlásenie", "OK")
            probe(ICloud(context, url), results)
        except TimeoutError as exc:
            results.add("prihlásenie", "FAIL", str(exc))
        finally:
            context.close()
    print(f"\nHotovo. Výsledky bez názvov pripomienok: {RESULTS_FILE.name}")


if __name__ == "__main__":
    main()
