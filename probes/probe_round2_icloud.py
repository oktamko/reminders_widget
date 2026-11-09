"""Throwaway probe, round 2 (iCloud part): edit an existing text document like an iPhone
does (CRDT edit_text) and retry the WebView2 sign-in.

Run it yourself (after probe_fields.py):
    .\\.venv\\Scripts\\python.exe probes\\probe_round2_icloud.py            # part A, then B
    .\\.venv\\Scripts\\python.exe probes\\probe_round2_icloud.py --part A   # only the CRDT edit
    .\\.venv\\Scripts\\python.exe probes\\probe_round2_icloud.py --part B   # only the sign-in

Part A writes only to one test reminder it creates itself and cleans it up with Deleted=1.
Part B reads only (see probe_webview_login.py). Results are appended to probe-results.txt;
reminder titles and notes of your own reminders are never logged.
"""
import json
import sys
import time
import uuid

from probe import ICloud, TEST_TITLE, TestReminder, choose_list, launch_browser, split_records, \
    wait_for_login
from probe_fields import APPLE_EPOCH_OFFSET_S, advance_token
from reminders_widget.icloud.crdt import UnknownFormat, decode_text, edit_text, encode_text

TITLE_FIELD = "TitleDocument"
NOTES_FIELD = "NotesDocument"
TOKEN_MAP_FIELD = "ResolutionTokenMap"
PHONE_WAIT_S = 120
HEADER = "--- kolo 2: úprava názvu a poznámky, prihlásenie ---"

PHONE_TITLE_1 = "TEST z iPhone 1"
PHONE_NOTE = "poznámka z iPhone"
PC_TITLE_1 = "TEST premenovaný z PC"
PC_NOTE = "poznámka z PC"
PHONE_TITLE_2 = "TEST z iPhone 2"
PC_TITLE_2 = "TEST 🥛 druhý raz z PC"


# --- Pure helpers (unit-tested) ---

def text_edit_fields(fields, field, token_key, new_text, replica_uuid, now):
    """Update fields for replacing the text of one document field on a looked-up record.

    Edits the CURRENT document (as the iPhone would) and advances that field's entry in
    ResolutionTokenMap. A missing document is created fresh. `now` is in seconds since 2001.
    Raises UnknownFormat / ValueError when the document cannot be edited safely.
    """
    current = (fields.get(field) or {}).get("value")
    if current is None:
        document = encode_text(new_text, replica_uuid)
    else:
        document = edit_text(current, new_text, replica_uuid)
    raw = (fields.get(TOKEN_MAP_FIELD) or {}).get("value")
    tokens = advance_token(raw, token_key, now, str(uuid.UUID(bytes=replica_uuid)).upper())
    return {field: document, TOKEN_MAP_FIELD: tokens}


def _safe_decode(fields, field):
    try:
        return decode_text((fields.get(field) or {}).get("value"))
    except Exception:
        return None


def matches_expected(fields, title, notes=None):
    """True when the record's title (and notes, if given) decode to exactly these strings."""
    if _safe_decode(fields, TITLE_FIELD) != title:
        return False
    return notes is None or _safe_decode(fields, NOTES_FIELD) == notes


# --- Part A ---

def pc_write(test, results, step, field, token_key, text, replica):
    """Fresh lookup -> edit the current document -> write with that lookup's change tag.

    An UnknownFormat is recorded by exception type only and the write is skipped.
    Returns True when the write went through."""
    def action():
        current = test.cloud.lookup(test.name)
        try:
            update = text_edit_fields(current["fields"], field, token_key, text, replica,
                                      time.time() - APPLE_EPOCH_OFFSET_S)
        except UnknownFormat as exc:
            raise RuntimeError(type(exc).__name__) from None  # type only, no details
        record = test.cloud.modify({"operationType": "update", "record": {
            "recordType": "Reminder", "recordName": test.name,
            "recordChangeTag": current["recordChangeTag"],
            "fields": {key: {"value": value} for key, value in update.items()},
        }})
        test.tag = record["recordChangeTag"]

    return bool(results.run(step, action))


def wait_for_phone(cloud, name, tag, title, notes=None):
    """Poll until the record changed AND decodes to the given texts, or PHONE_WAIT_S passes."""
    started = time.time()
    while True:
        record = cloud.lookup(name)
        arrived = record["recordChangeTag"] != tag and matches_expected(record["fields"], title, notes)
        elapsed = time.time() - started
        if arrived or elapsed >= PHONE_WAIT_S:
            return record, arrived, elapsed
        print(f"     čakám na iPhone... {elapsed:.0f} s z {PHONE_WAIT_S}", flush=True)
        time.sleep(5)


def phone_step(cloud, test, results, step, instruction, title, notes=None):
    tag = cloud.lookup(test.name)["recordChangeTag"]  # snapshot BEFORE the prompt
    print(f"\n{instruction}")
    input("Potom stlač Enter...")
    record, arrived, waited = wait_for_phone(cloud, test.name, tag, title, notes)
    results.add(step, "OK" if arrived else "FAIL",
                f"PC dekóduje presne tie texty po {waited:.0f} s" if arrived
                else "PC nedekóduje očakávané texty (alebo zmena nedorazila)")
    return arrived


def crdt_experiment(cloud, results):
    cloud.find_owner()
    records, _ = cloud.changes()
    lists, reminders = split_records(records)
    if not lists:
        results.add("výber zoznamu", "FAIL", "nenašiel sa žiadny zoznam")
        return
    sample = reminders[0]["recordName"] if reminders else ""
    prefix = sample.split("/")[0] + "/" if "/" in sample else ""
    test = TestReminder(cloud, choose_list(lists), f"{prefix}{str(uuid.uuid4()).upper()}")
    replica = uuid.uuid4().bytes  # one stable PC replica for the whole run

    try:
        if not results.run("vytvorenie testovacej pripomienky", test.create):
            return
        print(f'\nNa iPhone nájdi pripomienku "{TEST_TITLE}" (sync môže chvíľu trvať).')
        input("Keď ju vidíš, stlač Enter...")

        phone_step(cloud, test, results, "iPhone: premenovanie a poznámka dorazili na PC",
                   f"Na iPhone premenuj pripomienku na `{PHONE_TITLE_1}` a pridaj poznámku "
                   f"`{PHONE_NOTE}`.", PHONE_TITLE_1, PHONE_NOTE)

        if pc_write(test, results, "PC: premenovanie cez edit_text", TITLE_FIELD,
                            "titleDocument", PC_TITLE_1, replica):
            results.confirm("názov z PC presne na iPhone",
                            f"Je názov na iPhone PRESNE `{PC_TITLE_1}`, bez zvyškov starého názvu? "
                            "Daj tomu aspoň 30 s.")

        if pc_write(test, results, "PC: poznámka cez edit_text", NOTES_FIELD,
                            "notesDocument", PC_NOTE, replica):
            results.confirm("poznámka z PC presne na iPhone",
                            f"Je poznámka na iPhone PRESNE `{PC_NOTE}`, bez zvyškov starej? "
                            "Daj tomu aspoň 30 s.")

        phone_step(cloud, test, results, "iPhone: druhé premenovanie dorazilo na PC",
                   f"Na iPhone premenuj pripomienku na `{PHONE_TITLE_2}`.", PHONE_TITLE_2)

        if pc_write(test, results, "PC: druhé premenovanie cez edit_text", TITLE_FIELD,
                            "titleDocument", PC_TITLE_2, replica):
            results.confirm("druhý názov z PC presne na iPhone",
                            f"Je názov na iPhone PRESNE `{PC_TITLE_2}`, bez zvyškov starého názvu? "
                            "Daj tomu aspoň 30 s.")
    finally:
        cleaned = results.run("upratanie testovacej pripomienky", lambda: test.update(Deleted=1))
    if cleaned:
        print("\nTestovacia pripomienka by mala sama zmiznúť, na iPhone nič nemaž.")


def part_a(results):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        context = launch_browser(pw)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.icloud.com/reminders/")
        try:
            url = wait_for_login(context, page)
            results.add("prihlásenie", "OK")
            crdt_experiment(ICloud(context, url), results)
        except Exception as exc:  # record what broke
            results.add("pokus prerušený", "FAIL", f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            context.close()


# --- Part B ---

def part_b():
    import probe_webview_login

    print("\nČasť B: prihlásenie v okne WebView2.")
    print("Prihlás sa, dokonči 2FA a zvoľ \"Dôverovať\".")
    print("Okno NEZATVÁRAJ, zatvorí sa samo.\n")
    probe_webview_login.main()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    part = "AB"
    if "--part" in argv:
        try:
            part = argv[argv.index("--part") + 1].upper()
        except IndexError:
            part = ""
        if part not in ("A", "B"):
            sys.exit("Použitie: probe_round2_icloud.py [--part A|B]")
    sys.stdout.reconfigure(encoding="utf-8")
    from probe_webview_login import AppendResults

    results = AppendResults()
    results.add(HEADER, "INFO")
    if "A" in part:
        part_a(results)
    if "B" in part:
        part_b()
    print("\nHotovo. Výsledky bez názvov pripomienok: probe-results.txt")


if __name__ == "__main__":
    main()
