"""Probe round 4 (plan 2a, Task 1): duplicate create, list colours, stored order,
recurring completion, alarm vs due date.

Run it yourself:  .\\.venv\\Scripts\\python.exe probes\\probe_round4.py
Writes only to one test reminder it creates (cleaned up with Deleted=1).
probe-results.txt gets field names, types, codes and counts — never titles,
notes or list names. Colour values are logged (they are not personal).
"""
import json
import re
import sys
import time
import uuid

from probe import (ICloud, TestReminder, TEST_TITLE, choose_list, launch_browser,
                   split_records, wait_for_login)
from probe_webview_login import AppendResults

APPLE_EPOCH_OFFSET_S = 978307200
DAY_MS = 86_400_000
PHONE_WAIT_S = 150
REPLICA_ID = str(uuid.uuid4()).upper()
_COLOUR_KEY = re.compile(r"hex|colou?r", re.I)
_COLOUR_VALUE = re.compile(r"^#?[0-9A-Fa-f]{3,8}$|^[a-zA-Z]{2,20}$")


def color_summary(field):
    """Shape of a List's Color field. Shows colour-like values only."""
    if not isinstance(field, dict):
        return "chýba"
    ftype, value = field.get("type"), field.get("value")
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except ValueError:
            data = None
        if isinstance(data, dict):
            parts = []
            for key, val in data.items():
                shown = (val if _COLOUR_KEY.search(key) and isinstance(val, (str, int, float))
                         and _COLOUR_VALUE.match(str(val)) else "…")
                parts.append(f"{key}={shown}")
            return f"{ftype} json {{{', '.join(parts)}}}"
        if _COLOUR_VALUE.match(value) and value.startswith("#"):
            return f"{ftype} text {value}"
        return f"{ftype} text dĺžka {len(value)}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{ftype} číslo {value}"
    return f"{ftype} {type(value).__name__}"


def _bare(name):
    return name.split("/", 1)[1] if "/" in name else name


def order_summary(field, reminder_ids):
    """Is a List's ReminderIDs a permutation of that list's reminder ids? Counts only."""
    out = {"type": field.get("type") if isinstance(field, dict) else None,
           "json": False, "count": None, "permutation": False, "missing": 0, "extra": 0}
    value = field.get("value") if isinstance(field, dict) else None
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return out
    if not isinstance(data, list):
        return out
    out["json"] = isinstance(value, str)
    out["count"] = len(data)
    have = {_bare(str(x)) for x in data}
    want = {_bare(x) for x in reminder_ids}
    out["missing"] = len(want - have)
    out["extra"] = len(have - want)
    out["permutation"] = out["missing"] == 0 and out["extra"] == 0
    return out


def advance(raw, keys, now):
    data = json.loads(raw) if raw else {"map": {}}
    entries = data.setdefault("map", {})
    for key in keys:
        tok = entries.get(key)
        if isinstance(tok, dict):
            tok["counter"] = tok.get("counter", 0) + 1
            tok["modificationTime"] = max(tok.get("modificationTime", 0) + 1, now)
            tok.setdefault("replicaID", REPLICA_ID)
        else:
            entries[key] = {"counter": 1, "modificationTime": now, "replicaID": REPLICA_ID}
    return json.dumps(data)


def snapshot(cloud, name):
    rec = cloud.lookup(name)
    fields = rec.get("fields") or {}
    try:
        tokens = json.loads(fields.get("ResolutionTokenMap", {}).get("value") or "{}").get("map", {})
    except ValueError:
        tokens = {}
    return rec, fields, tokens


def wait_phone(cloud, name, tag, what):
    started = time.time()
    rec = cloud.lookup(name)
    while rec.get("recordChangeTag") == tag and time.time() - started < PHONE_WAIT_S:
        print(f"     čakám na iPhone ({what})... {time.time() - started:.0f} s z {PHONE_WAIT_S}", flush=True)
        time.sleep(5)
        rec = cloud.lookup(name)
    return rec.get("recordChangeTag") != tag, time.time() - started


def part_lists(records, results):
    lists, reminders = split_records(records)
    by_list = {}
    for rec in reminders:
        ref = ((rec.get("fields") or {}).get("List") or {}).get("value") or {}
        by_list.setdefault(ref.get("recordName"), []).append(rec["recordName"])
    raw_lists = [r for r in records if r.get("recordType") in ("List", "ReminderList")
                 and not (r.get("fields") or {}).get("Deleted", {}).get("value")]
    for n, rec in enumerate(raw_lists, 1):
        fields = rec.get("fields") or {}
        results.add(f"zoznam {n}: farba", "INFO", color_summary(fields.get("Color")))
        if "ReminderIDs" in fields:
            summary = order_summary(fields["ReminderIDs"], by_list.get(rec["recordName"], []))
            results.add(f"zoznam {n}: ReminderIDs", "INFO", json.dumps(summary))
        else:
            results.add(f"zoznam {n}: ReminderIDs", "INFO", "chýba")
    return lists


def experiment(cloud, results):
    cloud.find_owner()
    records, _ = cloud.changes()
    lists = part_lists(records, results)
    _, reminders = split_records(records)
    sample = reminders[0]["recordName"] if reminders else ""
    prefix = sample.split("/")[0] + "/" if "/" in sample else ""
    test = TestReminder(cloud, choose_list(lists), f"{prefix}{str(uuid.uuid4()).upper()}")
    try:
        if not results.run("vytvorenie testovacej pripomienky", test.create):
            return
        # A. duplicate create
        try:
            test.create()
            results.add("duplicitné vytvorenie", "INFO", "server prijal (bez chyby)")
        except RuntimeError as exc:
            code = str(exc).split(":", 1)[0].strip()
            results.add("duplicitné vytvorenie - kód servera", "OK", code if code.isupper() else "neznámy tvar")

        input(f'\nPočkaj, kým na iPhone uvidíš "{TEST_TITLE}". Potom jej na iPhone nastav '
              'termín (dnes, s časom), opakovanie „Denne“ a upozornenie v čase termínu. Stlač Enter...')
        _, before, tok_before = snapshot(cloud, test.name)
        tag = cloud.lookup(test.name)["recordChangeTag"]
        arrived, secs = wait_phone(cloud, test.name, tag, "opakovanie a upozornenie")
        _, fields, tokens = snapshot(cloud, test.name)
        results.add("iPhone: opakovanie a upozornenie dorazili", "OK" if arrived else "FAIL", f"po {secs:.0f} s")
        results.add("polia po nastavení opakovania", "INFO", ", ".join(sorted(fields)))
        results.add("kľúče značiek po nastavení opakovania", "INFO", ", ".join(sorted(tokens)))
        rec_ids = (fields.get("RecurrenceRuleIDs") or {}).get("value")
        alarm_ids = (fields.get("AlarmIDs") or {}).get("value")
        results.add("RecurrenceRuleIDs / AlarmIDs", "INFO",
                    f"{len(rec_ids) if isinstance(rec_ids, list) else rec_ids} / "
                    f"{len(alarm_ids) if isinstance(alarm_ids, list) else alarm_ids}")
        due_before = (fields.get("DueDate") or {}).get("value")

        # E. alarm vs due: PC moves the due date by one day
        def move_due():
            rec, cur, _ = snapshot(cloud, test.name)
            now = time.time() - APPLE_EPOCH_OFFSET_S
            token_map = advance(cur.get("ResolutionTokenMap", {}).get("value"), ["dueDate"], now)
            cloud.modify({"operationType": "update", "record": {
                "recordType": "Reminder", "recordName": test.name,
                "recordChangeTag": rec["recordChangeTag"],
                "fields": {"DueDate": {"value": due_before + DAY_MS},
                           "ResolutionTokenMap": {"value": token_map}}}})
        if isinstance(due_before, int) and results.run("PC: termín o +1 deň", move_due):
            results.confirm("upozornenie sa posunulo s termínom",
                            "Posunulo sa na iPhone aj UPOZORNENIE na nový termín (zajtra)? Pozri detail pripomienky.")

        # D. recurring: Martin completes it on the iPhone
        _, before_done, tok_before_done = snapshot(cloud, test.name)
        tag = cloud.lookup(test.name)["recordChangeTag"]
        input("\nTeraz testovaciu pripomienku na iPhone ODŠKRTNI (je opakovaná). Stlač Enter...")
        arrived, secs = wait_phone(cloud, test.name, tag, "odškrtnutie")
        _, after_done, tok_after = snapshot(cloud, test.name)
        results.add("iPhone: odškrtnutie opakovanej dorazilo", "OK" if arrived else "FAIL", f"po {secs:.0f} s")
        changed = sorted(k for k in set(before_done) | set(after_done)
                         if before_done.get(k) != after_done.get(k) and k != "ResolutionTokenMap")
        results.add("polia zmenené odškrtnutím", "INFO", ", ".join(changed) or "žiadne")
        results.add("Completed po odškrtnutí", "INFO", str((after_done.get("Completed") or {}).get("value")))
        d0 = (before_done.get("DueDate") or {}).get("value")
        d1 = (after_done.get("DueDate") or {}).get("value")
        if isinstance(d0, int) and isinstance(d1, int):
            results.add("posun termínu po odškrtnutí", "INFO", f"{(d1 - d0) / DAY_MS:+.2f} dňa")
        bumped = sorted(k for k in tok_after if tok_after.get(k) != tok_before_done.get(k))
        results.add("kľúče značiek posunuté odškrtnutím", "INFO", ", ".join(bumped) or "žiadne")
        results.add("nové záznamy (opakovanie)", "INFO",
                    "pozri typy: " + ", ".join(sorted({r.get("recordType") or "?" for r in cloud.changes()[0]
                                                       if r.get("recordType") not in ("Reminder", "List")})))
    finally:
        results.run("upratanie testovacej pripomienky", lambda: test.update(Deleted=1))
        print("\nTestovacia pripomienka sa sama zmaže. Na iPhone nič nemaž.")


def main():
    from playwright.sync_api import sync_playwright

    sys.stdout.reconfigure(encoding="utf-8")
    results = AppendResults()
    results.add("--- kolo 4: duplicitné vytvorenie, farby, poradie, opakovanie, upozornenie ---", "INFO")
    with sync_playwright() as pw:
        context = launch_browser(pw)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.icloud.com/reminders/")
        try:
            results.add("prihlásenie", "OK")
            experiment(ICloud(context, wait_for_login(context, page)), results)
        except Exception as exc:  # a probe records what broke
            results.add("pokus prerušený", "FAIL", type(exc).__name__)
        finally:
            context.close()
    print("\nHotovo. Výsledky bez názvov pripomienok: probe-results.txt")


if __name__ == "__main__":
    main()
