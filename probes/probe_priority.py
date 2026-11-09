"""Throwaway probe: can the PC set priority on a reminder the iPhone never prioritised?

Run it yourself:  .\\.venv\\Scripts\\python.exe probe_priority.py

As in probe.py, it writes only to one test reminder it creates itself.
probe-results.txt gets field names and metadata, never reminder titles or notes.
"""
import json
import sys
import time
import uuid
from collections import Counter

from probe import (ICloud, Results, TEST_TITLE, TestReminder, choose_list, launch_browser,
                   split_records, title_of, title_shape, wait_for_login)

PHONE_WAIT_S = 120
TOKEN_MAP_FIELD = "ResolutionTokenMap"
APPLE_EPOCH_OFFSET_S = 978307200  # token times count seconds from 2001-01-01
REPLICA_ID = str(uuid.uuid4()).upper()
HIGH, MEDIUM, LOW = 1, 5, 9


def set_priority_token(raw, now, replica_id):
    """Advance the map's priority entry, creating it if missing; None if unparseable."""
    try:
        data = json.loads(raw)
        entries = data["map"]
    except (TypeError, ValueError, KeyError):
        return None
    token = entries.get("priority")
    if token:
        token["counter"] = token.get("counter", 0) + 1
        token["modificationTime"] = max(token.get("modificationTime", 0) + 1, now)
    else:
        entries["priority"] = {"counter": 1, "modificationTime": now, "replicaID": replica_id}
    return json.dumps(data)


def priority_state(fields):
    entries = json.loads(fields[TOKEN_MAP_FIELD]["value"])["map"]
    return {"Priority": fields.get("Priority", {}).get("value"), "token": entries.get("priority")}


def write_priority(test, value):
    fields = test.cloud.lookup(test.name)["fields"]
    token_map = set_priority_token(fields.get(TOKEN_MAP_FIELD, {}).get("value"),
                                   time.time() - APPLE_EPOCH_OFFSET_S, REPLICA_ID)
    if token_map is None:
        raise RuntimeError("mapa značiek chýba alebo má neznámy tvar")
    test.update(Priority=value, **{TOKEN_MAP_FIELD: token_map})


def report_decoding(records, results):
    _, reminders = split_records(records)
    decoded = sum(1 for rec in reminders if title_of(rec))
    results.add("dekódovanie názvov", "OK" if decoded == len(reminders) else "FAIL",
                f"{decoded} z {len(reminders)}")
    shapes = Counter(title_shape(rec) for rec in reminders if not title_of(rec))
    for shape, count in shapes.most_common():
        results.add("nedekódované názvy", "INFO", f"{count}x {shape}")


def experiment(cloud, results):
    cloud.find_owner()
    records, _ = cloud.changes()
    report_decoding(records, results)
    lists, reminders = split_records(records)
    prefix = reminders[0]["recordName"].split("/")[0] + "/" if "/" in reminders[0]["recordName"] else ""

    test = TestReminder(cloud, choose_list(lists), f"{prefix}{str(uuid.uuid4()).upper()}")
    test.create()
    results.add("vytvorenie testovacej pripomienky", "OK")
    input(f'\nPočkaj, kým na iPhone uvidíš "{TEST_TITLE}", a stlač Enter (prioritu na iPhone NEMEŇ)...')
    before = cloud.lookup(test.name)["fields"]
    results.add("stav pred prvým zápisom", "INFO", json.dumps(priority_state(before)))

    if results.run("PC: prvá priorita, vysoká", lambda: write_priority(test, HIGH)):
        results.confirm("vysoká z PC viditeľná na iPhone",
                        "Má pripomienka na iPhone vysokú prioritu (!!!)? Daj tomu aspoň 30 s.")
    if results.run("PC: zmena na strednú", lambda: write_priority(test, MEDIUM)):
        results.confirm("stredná z PC viditeľná na iPhone",
                        "Zmenila sa na iPhone na strednú prioritu (!!)? Daj tomu aspoň 30 s.")

    tag = cloud.lookup(test.name)["recordChangeTag"]
    input("\nNa iPhone teraz nastav tejto pripomienke NÍZKU prioritu (!) a stlač Enter...")
    started = time.time()
    record = cloud.lookup(test.name)
    while record["recordChangeTag"] == tag and time.time() - started < PHONE_WAIT_S:
        print(f"     čakám na iPhone... {time.time() - started:.0f} s z {PHONE_WAIT_S}", flush=True)
        time.sleep(5)
        record = cloud.lookup(test.name)
    state = priority_state(record["fields"])
    results.add("iPhone: nízka priorita dorazila na PC", "OK" if state["Priority"] == LOW else "FAIL",
                f"po {time.time() - started:.0f} s, {json.dumps(state)}")

    if results.run("PC: po zmene z iPhone späť na vysokú", lambda: write_priority(test, HIGH)):
        results.confirm("vysoká po zmene z iPhone viditeľná",
                        "Zmenila sa na iPhone späť na vysokú prioritu (!!!)? Daj tomu aspoň 30 s.")

    results.run("upratanie testovacej pripomienky", lambda: test.update(Deleted=1))
    print("\nTestovacia pripomienka by mala sama zmiznúť, na iPhone nič nemaž.")


def main():
    from playwright.sync_api import sync_playwright

    sys.stdout.reconfigure(encoding="utf-8")
    results = Results()
    with sync_playwright() as pw:
        context = launch_browser(pw)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.icloud.com/reminders/")
        try:
            url = wait_for_login(context, page)
            results.add("prihlásenie", "OK")
            experiment(ICloud(context, url), results)
        except Exception as exc:  # record what broke; the probe has no later steps to save
            results.add("pokus prerušený", "FAIL", f"{type(exc).__name__}: {str(exc)[:300]}")
        finally:
            context.close()
    print("\nHotovo. Výsledky bez názvov pripomienok: probe-results.txt")


if __name__ == "__main__":
    main()
