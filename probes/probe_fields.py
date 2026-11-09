"""Throwaway probe 1: due date, notes, rename and emoji titles through CloudKit.

Run it yourself:  .\\.venv\\Scripts\\python.exe probes\\probe_fields.py

As in probe.py, it writes only to one test reminder it creates itself.
probe-results.txt gets field names, types, token keys and counters, never reminder
titles or notes. It also writes tests/fixtures/records.json with anonymised record shapes.

What it finds out:
- the CloudKit field names and ResolutionTokenMap keys the iPhone uses for due date and notes
  (detected by diffing the record before and after you edit it on the iPhone)
- whether the PC can write due date, notes and a new title so the iPhone shows them
- whether an emoji title needs the UTF-16 length (len(title) vs utf-16 code units)
"""
import base64
import copy
import gzip
import json
import sys
import time
import uuid
from pathlib import Path

from probe import (HERE, ICloud, Results, TEST_TITLE, TestReminder, _bytes_field, _int_field,
                   _position, choose_list, encode_title, launch_browser,
                   split_records, wait_for_login)

PHONE_WAIT_S = 120
TOKEN_MAP_FIELD = "ResolutionTokenMap"
APPLE_EPOCH_OFFSET_S = 978307200  # token times count seconds from 2001-01-01
REPLICA_ID = str(uuid.uuid4()).upper()
FIXTURE_FILE = HERE.parent / "tests" / "fixtures" / "records.json"
DAY_MS = 86_400_000
NOTE_TEXT = "Poznámka z PC"
RENAMED = "TEST premenovaný z PC"
EMOJI_TITLE = "TEST 🥛 mlieko"
LIST_TYPES = ("List", "ReminderList")


# --- token map ---

def advance_token(raw, key, now, replica_id):
    """Advance one ResolutionTokenMap entry (create it if missing); returns the new JSON text.

    raw may be None (the PC-created record has no map until the iPhone touches it).
    Raises ValueError if raw is not JSON with a "map" object.
    """
    if raw is None:
        data = {"map": {}}
    else:
        try:
            data = json.loads(raw)
            entries = data["map"]
        except (TypeError, ValueError, KeyError) as exc:
            raise ValueError("neznámy tvar ResolutionTokenMap") from exc
        if not isinstance(entries, dict):
            raise ValueError("neznámy tvar ResolutionTokenMap")
    entries = data["map"]
    token = entries.get(key)
    if token:
        token["counter"] = token.get("counter", 0) + 1
        token["modificationTime"] = max(token.get("modificationTime", 0) + 1, now)
    else:
        entries[key] = {"counter": 1, "modificationTime": now, "replicaID": replica_id}
    return json.dumps(data)


def token_entries(fields):
    """The map's entries, or {} if the record has no readable map."""
    try:
        return json.loads(fields[TOKEN_MAP_FIELD]["value"])["map"]
    except (KeyError, TypeError, ValueError):
        return {}


# --- title encoder with a choosable length rule ---

def utf16_len(text):
    return len(text.encode("utf-16-le")) // 2


def encode_title_len(title, char_len):
    """Same document as probe.encode_title, but the length fields take `char_len`."""
    op1 = (_bytes_field(1, _position(0, 0)) + _int_field(2, 0)
           + _bytes_field(3, _position(0, 0)) + _int_field(5, 1))
    op2 = (_bytes_field(1, _position(1, 0)) + _int_field(2, char_len)
           + _bytes_field(3, _position(1, 0)) + _int_field(5, 2))
    op3 = (_bytes_field(1, _position(0, -1)) + _int_field(2, 0)
           + _bytes_field(3, _position(0, -1)))
    uuid_entry = (_bytes_field(1, uuid.uuid4().bytes)
                  + _bytes_field(2, _int_field(1, char_len))
                  + _bytes_field(2, _int_field(1, 1)))
    note = (_bytes_field(2, title.encode("utf-8"))
            + _bytes_field(3, op1) + _bytes_field(3, op2) + _bytes_field(3, op3)
            + _bytes_field(4, _bytes_field(1, uuid_entry))
            + _bytes_field(5, _int_field(1, char_len)))
    document = _int_field(1, 0) + _int_field(2, 0) + _bytes_field(3, note)
    outer = _int_field(1, 0) + _bytes_field(2, document)
    return base64.b64encode(gzip.compress(outer)).decode("ascii")


# --- field detection ---

def new_fields(before, after):
    return {name: field for name, field in after.items() if name not in before}


def describe_field(name, field):
    """Type info for the log; the value only for numbers (times), never for strings/bytes."""
    value = field.get("value")
    text = f"{name}: type={field.get('type')}, python={type(value).__name__}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        text += f", hodnota={value}"
    return text


def detect_due_fields(new, changed_token_keys=()):
    """Candidate due-date fields; one item means unambiguous.

    Among several new timestamps prefer names containing "due", then names equal to a
    changed token key (case-insensitive)."""
    stamps = [name for name, field in new.items() if field.get("type") == "TIMESTAMP"]
    if not stamps:
        return [name for name in new if "due" in name.lower()]
    pool = [name for name in stamps if "due" in name.lower()] or stamps
    if len(pool) > 1:
        keys = {key.lower() for key in changed_token_keys}
        matched = [name for name in pool if name.lower() in keys]
        if len(matched) == 1:
            return matched
    return pool


def detect_note_fields(new):
    texty = [name for name, field in new.items()
             if field.get("type") != "TIMESTAMP" and isinstance(field.get("value"), str)]
    named = [name for name in texty if "note" in name.lower()]
    return named or texty


def pick_token_key(before, after, field_names, hints, changed_only=True):
    """Token keys that plausibly belong to the field; one item means unambiguous."""
    matches = [key for key, token in after.items()
               if (not changed_only or before.get(key) != token)
               and any(hint in key.lower() for hint in hints)]
    if len(matches) > 1:
        wanted = {name.lower() for name in field_names}
        exact = [key for key in matches if key.lower() in wanted]
        if len(exact) == 1:
            return exact
    return matches


# --- fixture anonymiser ---

def _is_document(name, note_fields):
    return name.endswith("Document") or name in note_fields


def anonymise_records(reminders, lists, note_fields=()):
    """Copy records keeping shape, field names, types, tags, numbers, dates and token maps;
    drop every title, note, name, id and other string. Reminders first, then lists."""
    names = {}

    def new_name(old):
        if old not in names:
            prefix = old.split("/")[0] + "/" if "/" in old else ""
            names[old] = prefix + str(uuid.uuid4()).upper()
        return names[old]

    def walk_ids(obj):
        """Remap every recordName and blank owner/user/device ids anywhere; keep other strings
        (zoneName, action...) so the shape survives."""
        if isinstance(obj, dict):
            out = {}
            for key, val in obj.items():
                if key == "recordName" and isinstance(val, str):
                    out[key] = new_name(val)
                elif key in ("ownerRecordName", "userRecordName", "deviceID") and isinstance(val, str):
                    out[key] = "x"
                else:
                    out[key] = walk_ids(val)
            return out
        if isinstance(obj, list):
            return [walk_ids(item) for item in obj]
        return obj

    def scrub_value(value):
        if isinstance(value, (dict, list)):
            if isinstance(value, list):
                return [scrub_value(item) for item in value]
            return walk_ids(value)
        return "x" if isinstance(value, str) else value

    def scrub(record, label, n):
        rec = copy.deepcopy(record)
        fields = rec.pop("fields", None) or {}
        rec = walk_ids(rec)  # recordName, parent, share, created/modified, zoneID, anything else
        for name, field in fields.items():
            value = field.get("value")
            if name == TOKEN_MAP_FIELD:
                continue
            if label == "List" and name == "Name":
                field["value"] = f"List {n}"
            elif isinstance(value, str) and _is_document(name, note_fields):
                field["value"] = encode_title(f"{label} {n}" if label == "List" else f"Sample {n}")
            else:
                field["value"] = scrub_value(value)
        rec["fields"] = fields
        return rec

    out = [scrub(rec, "Sample", n) for n, rec in enumerate(reminders, 1)]
    out += [scrub(rec, "List", n) for n, rec in enumerate(lists, 1)]
    return out


def pick_fixture_records(records, max_reminders=10, max_lists=3):
    """Real, non-deleted records: up to 10 reminders, up to 3 lists (referenced ones first)."""
    live = [r for r in records if not r.get("deleted")
            and not (r.get("fields") or {}).get("Deleted", {}).get("value")]
    reminders = [r for r in live if r.get("recordType") == "Reminder"][:max_reminders]
    all_lists = [r for r in live if r.get("recordType") in LIST_TYPES]
    used = set()
    for rec in reminders:
        ref = ((rec.get("fields") or {}).get("List") or {}).get("value")
        if isinstance(ref, dict):
            used.add(ref.get("recordName"))
    all_lists.sort(key=lambda r: r["recordName"] not in used)
    return reminders, all_lists[:max_lists]


def write_fixture(records, note_fields):
    reminders, lists = pick_fixture_records(records)
    out = anonymise_records(reminders, lists, note_fields)
    FIXTURE_FILE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(out)


# --- guided run ---

def write_with_token(test, values, token_key):
    """Fresh lookup, advance the token (if a key is given), update the record."""
    fields = test.cloud.lookup(test.name)["fields"]
    update = dict(values)
    if token_key:
        raw = fields.get(TOKEN_MAP_FIELD, {}).get("value")
        update[TOKEN_MAP_FIELD] = advance_token(raw, token_key, time.time() - APPLE_EPOCH_OFFSET_S,
                                                REPLICA_ID)
    test.update(**update)


def ask(question):
    return input(f"\n{question} [a/n] ").strip().lower().startswith("a")


def edit_arrived(baseline, fields, tag_changed):
    """True once the due date AND the note have landed (not just any new field like TimeZone)."""
    new = new_fields(baseline, fields)
    note_field = any("note" in name.lower() for name in new)
    note_token = any("note" in key.lower() for key in token_entries(fields)
                     if key not in token_entries(baseline))
    changed = [key for key, tok in token_entries(fields).items()
               if token_entries(baseline).get(key) != tok]
    return bool(tag_changed and detect_due_fields(new, changed) and (note_field or note_token))


def wait_for_phone_edit(cloud, name, baseline, tag):
    """Poll until the iPhone's due date and note both show up, or PHONE_WAIT_S passes."""
    started = time.time()
    record = cloud.lookup(name)
    while True:
        done = edit_arrived(baseline, record["fields"], record["recordChangeTag"] != tag)
        if done or time.time() - started >= PHONE_WAIT_S:
            return record, time.time() - started
        print(f"     čakám na iPhone... {time.time() - started:.0f} s z {PHONE_WAIT_S}", flush=True)
        time.sleep(5)
        record = cloud.lookup(name)


def choose_unique(results, step, what, candidates):
    """Return the single candidate; otherwise record FAIL with the candidates."""
    if len(candidates) == 1:
        return candidates[0]
    results.add(step, "FAIL", f"{what}: kandidáti {candidates or 'žiadni'}")
    return None


def detect(baseline, results, record):
    fields, after_tokens = record["fields"], token_entries(record["fields"])
    before_tokens = token_entries(baseline)
    results.add("polia po úprave na iPhone", "INFO", ", ".join(sorted(fields)))
    results.add("polia pred úpravou", "INFO", ", ".join(sorted(baseline)))
    new = new_fields(baseline, fields)
    for name, field in sorted(new.items()):
        results.add("nové pole", "INFO", describe_field(name, field))
    for key, token in sorted(after_tokens.items()):
        was = before_tokens.get(key)
        results.add("kľúč ResolutionTokenMap", "INFO",
                    f"{key}: counter={token.get('counter')}"
                    + ("" if was else " (nový)") + (" (zmenený)" if was and was != token else ""))
    if not after_tokens:
        results.add("ResolutionTokenMap", "INFO", "záznam nemá čitateľnú mapu značiek")

    changed = [key for key, token in after_tokens.items() if before_tokens.get(key) != token]
    found = {}
    due_name = choose_unique(results, "detekcia poľa termínu", "termín", detect_due_fields(new, changed))
    note_name = choose_unique(results, "detekcia poľa poznámok", "poznámky", detect_note_fields(new))
    if due_name:
        key = choose_unique(results, "detekcia kľúča značky termínu", "termín",
                            pick_token_key(before_tokens, after_tokens, [due_name], ["due"]))
        if key:
            found["due"] = (due_name, key)
            results.add("detekcia termínu", "OK", f"pole {due_name}, kľúč {key}")
    if note_name:
        key = choose_unique(results, "detekcia kľúča značky poznámok", "poznámky",
                            pick_token_key(before_tokens, after_tokens, [note_name], ["note"]))
        if key:
            found["note"] = (note_name, key)
            results.add("detekcia poznámok", "OK", f"pole {note_name}, kľúč {key}")
    title_keys = pick_token_key(before_tokens, after_tokens, ["TitleDocument"], ["title"],
                                changed_only=False)
    if len(title_keys) == 1:
        found["title_key"] = title_keys[0]
        results.add("detekcia kľúča značky názvu", "OK", title_keys[0])
    elif title_keys:
        results.add("detekcia kľúča značky názvu", "FAIL", f"kandidáti {title_keys}")
    else:
        found["title_key"] = None
        results.add("detekcia kľúča názvu", "INFO",
                    "žiadny kľúč názvu, premenovanie zapíšem bez posunutia značky")
    return found, fields


def rename(test, title, token_key, length_rule):
    write_with_token(test, {"TitleDocument": encode_title_len(title, length_rule(title))}, token_key)


def emoji_step(test, results, title_key):
    rules = (("len(title)", len), ("UTF-16 jednotky", utf16_len))
    for label, rule in rules:
        if not results.run(f"PC: emoji názov, dĺžka {label}",
                           lambda: rename(test, EMOJI_TITLE, title_key, rule)):
            return
        shown = ask(f'Vidíš na iPhone celý názov "{EMOJI_TITLE}" (aj s mliekom a koncom)? Daj tomu aspoň 30 s.')
        if shown:
            results.add("emoji pravidlo dĺžky", "OK", f"funguje: {label}")
            return
        results.add(f"emoji, dĺžka {label}", "FAIL", "iPhone neukazuje celý názov")
    results.add("emoji pravidlo dĺžky", "FAIL", "nefunguje ani len(title) ani UTF-16")


def experiment(cloud, results):
    cloud.find_owner()
    records, _ = cloud.changes()
    lists, reminders = split_records(records)
    if not lists:
        results.add("výber zoznamu", "FAIL", "nenašiel sa žiadny zoznam")
        return
    sample = reminders[0]["recordName"] if reminders else ""
    prefix = sample.split("/")[0] + "/" if "/" in sample else ""
    test = TestReminder(cloud, choose_list(lists), f"{prefix}{str(uuid.uuid4()).upper()}")

    cleaned = False
    try:
        # inside the try: a create that failed after the server stored the record still gets cleaned up
        if not results.run("vytvorenie testovacej pripomienky", test.create):
            return
        baseline = cloud.lookup(test.name)
        tag = baseline["recordChangeTag"]  # snapshot BEFORE the prompt
        print(f'\nNa iPhone otvor "{TEST_TITLE}" (sync môže chvíľu trvať) a nastav jej')
        print("TERMÍN S ČASOM a POZNÁMKU (text je jedno aký). Názov a prioritu NEMEŇ.")
        input("Potom počkaj asi 30 s a stlač Enter...")
        record, waited = wait_for_phone_edit(cloud, test.name, baseline["fields"], tag)
        results.add("úprava z iPhone dorazila na PC",
                    "OK" if record["recordChangeTag"] != tag else "FAIL", f"po {waited:.0f} s")
        found, fields = detect(baseline["fields"], results, record)

        if "due" in found:
            name, key = found["due"]
            old = fields[name]["value"]
            if results.run("PC: termín o +1 deň", lambda: write_with_token(test, {name: old + DAY_MS}, key)):
                results.confirm("nový termín viditeľný na iPhone",
                                "Je termín na iPhone o deň neskôr, v rovnakom čase? Daj tomu aspoň 30 s.")
        if "note" in found:
            name, key = found["note"]
            if results.run("PC: poznámka",
                           lambda: write_with_token(test, {name: encode_title(NOTE_TEXT)}, key)):
                results.confirm("poznámka viditeľná na iPhone",
                                f'Vidíš na iPhone poznámku "{NOTE_TEXT}"? Daj tomu aspoň 30 s.')
        if "title_key" in found:
            key = found["title_key"]
            if results.run("PC: premenovanie",
                           lambda: rename(test, RENAMED, key, len)):
                results.confirm("nový názov viditeľný na iPhone",
                                f'Volá sa pripomienka na iPhone "{RENAMED}"? Daj tomu aspoň 30 s.')
            emoji_step(test, results, key)

        note_fields = [found["note"][0]] if "note" in found else []
        results.run("zápis tests/fixtures/records.json",
                    lambda: print(f"     zapísaných záznamov: {write_fixture(records, note_fields)}"))
    finally:
        # lookup fails if the record never existed; that is recorded as FAIL, not raised
        cleaned = results.run("upratanie testovacej pripomienky", lambda: test.update(Deleted=1))
    if cleaned:
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
