# Probes and Sync Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer the spec's open questions 1–4 with throwaway probes, then build the window-less core — codec, record model, local store, CloudKit client, fake iCloud and sync engine — fully tested without touching real iCloud.

**Architecture:** A `src/reminders_widget` package. `icloud/` holds pure codecs and a CloudKit client that talks through an injected `Transport`, so the core never depends on how sign-in works (decided in plan 2). `store.py` is a SQLite cache with a coalescing change queue; `sync.py` is the only code that calls CloudKit. Tests run against an in-memory `FakeCloudKit` transport.

**Tech Stack:** Python 3.12, SQLite (`sqlite3`), pytest, Playwright 1.64 (probes only), pywebview (probes 2–4 only).

**Spec:** `docs/superpowers/specs/2026-11-09-reminders-widget-design.md`

This is plan 1 of 3. Plan 2 (window, glass, sign-in, shell) is written after Tasks 2–4 report; plan 3 covers installer, README, updates and CI.

## Global Constraints

- Python 3.12; package `reminders_widget` in `src/` layout; tests with pytest.
- CloudKit: database path `database/1/com.apple.reminders/production/private`, zone `Reminders`, headers `Origin: https://www.icloud.com`, `Referer: https://www.icloud.com/`.
- Priority values: 0 none, 1 high, 5 medium, 9 low.
- `ResolutionTokenMap` times count seconds from 2001-01-01 (`APPLE_EPOCH_OFFSET_S = 978307200`); a token's new time is `max(old + 1, now)`.
- Every write starts from a fresh `records/lookup`; on lock conflict retry at most 3 times.
- Delete is always `Deleted=1`; never a hard delete.
- Never write a reminder whose fields could not be decoded (read-only).
- Logs and probe result files never contain reminder titles, notes or list names.
- Probes write only to a test reminder they create themselves, and clean it up with `Deleted=1`. Martin runs every probe that touches iCloud; the executor never signs in.
- Nothing under `.session/`, no cookie or token values, and no Vafle font file is ever committed.
- Commits only with Martin's go-ahead (they decide this when choosing the execution method).

## Review Focus

1. **Titles with emoji or characters outside the BMP.** `encode_title` stores `len(title)` (code points); Apple's text CRDT may count UTF-16 units, so "Buy 🥛" could render cut off on iPhone. Probe 1 checks it on a real iPhone; Task 5 pins whichever length rule the probe confirms.
2. **Reminder deleted on iPhone while a PC edit waits in the queue.** Expected: deletion wins, the edit is dropped, a notice is produced, nothing is re-created. Test in Task 10.
3. **PC clock behind the iPhone's token time.** Expected: the token still moves forward, so the iPhone accepts the PC's change. Test in Task 6.
4. **App closed with unsent changes.** Expected: the queue survives a restart and is sent on the next run. Test in Task 9.
5. **Logs leaking content.** Expected: a full sync of a reminder titled `SECRET-TITLE` with notes `SECRET-NOTE` leaves neither string in the log. Test in Task 10.

---

### Task 1: Project layout and moving the probes

**Files:**
- Create: `pyproject.toml`, `src/reminders_widget/__init__.py`, `src/reminders_widget/icloud/__init__.py`, `tests/__init__.py`, `tests/fakes/__init__.py`
- Move: `probe.py`, `probe_priority.py`, `test_probe.py` → `probes/`
- Modify: `probes/probe.py:22-25` (paths), `.gitignore`

**Interfaces:**
- Produces: importable package `reminders_widget`; `probes/` runnable as `.\.venv\Scripts\python.exe probes\probe.py`

- [ ] **Step 1:** `git mv` the three probe files into `probes/`. In `probes/probe.py` set `HERE = Path(__file__).parent`, `PROFILE_DIR = HERE.parent / ".session"`, `RESULTS_FILE = HERE.parent / "probe-results.txt"` so the existing signed-in session and results location stay where they are.
- [ ] **Step 2:** Write `pyproject.toml`: project `reminders-widget`, `requires-python = ">=3.12"`, no runtime dependencies yet, optional group `dev = ["pytest>=8"]`, `[tool.pytest.ini_options] testpaths = ["tests", "probes"]`, `pythonpath = ["src", "probes"]`.
- [ ] **Step 3:** Add to `.gitignore`: `*.db`, `logs/`, `*.otf`, `*.ttf` except `src/reminders_widget/ui/fonts/OpenSans*` (written as `!src/reminders_widget/ui/fonts/OpenSans*.ttf`).
- [ ] **Step 4:** Run `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"` then `.\.venv\Scripts\python.exe -m pytest -q`. Expected: `9 passed`.
- [ ] **Step 5:** Run `git status --short` and confirm no `.session`, `probe-results.txt` or font file is staged. Commit: `chore: package layout, move probes`.

---

### Task 2: Probe 1 — due date, notes, rename, emoji (Martin runs it)

**Files:**
- Create: `probes/probe_fields.py`
- Create (written by the probe): `tests/fixtures/records.json`

**Interfaces:**
- Consumes: `ICloud`, `TestReminder`, `Results`, `launch_browser`, `wait_for_login`, `split_records`, `choose_list`, `encode_title`, `decode_title` from `probes/probe.py`; `set_priority_token` pattern from `probes/probe_priority.py`.
- Produces: in `probe-results.txt`, the exact CloudKit field names, value types and `ResolutionTokenMap` keys for due date, notes and title; the emoji length verdict. `tests/fixtures/records.json` with anonymised record shapes.

- [ ] **Step 1:** Guided flow, same style as `probe_priority.py`: create test reminder → Martin sets on iPhone a due date with time **and** a note, Enter → log `sorted(fields)`, each new field's `type` and value *type* (not value; for date fields log the value, which is a timestamp, not content), and every key of `ResolutionTokenMap` with its counter.
- [ ] **Step 2:** PC writes a new due date (+1 day, same time) using the field and token key seen in step 1 → confirm on iPhone. PC writes notes `"Poznámka z PC"` encoded with `encode_title` into the notes field → confirm. PC renames to `"TEST premenovaný z PC"` advancing the title's token key → confirm.
- [ ] **Step 3:** PC renames to `"TEST 🥛 mlieko"` with `char_len = len(title)` → Martin reports whether iPhone shows the full title. If not, rename again with `char_len = len(title.encode("utf-16-le")) // 2` → confirm. Log which rule worked.
- [ ] **Step 4:** Write `tests/fixtures/records.json`: up to 10 real reminder records and 3 list records from `changes/zone`, keeping structure, field names, types, `recordChangeTag` shape, `ResolutionTokenMap` keys, numbers and dates, but replacing every `TitleDocument`/notes value with `encode_title(f"Sample {n}")`, every list `Name` with `f"List {n}"`, and every `recordName` with a fresh UUID (keeping prefix form). Log only the count written.
- [ ] **Step 5:** Clean up with `Deleted=1`. Martin runs `.\.venv\Scripts\python.exe probes\probe_fields.py`; the executor reads `probe-results.txt` and records the answers in spec section 3 under a new "Answers" table.

---

### Task 3: Probes 2 and 3 — sign-in inside WebView2, cookies outside a browser (Martin runs it)

**Files:**
- Create: `probes/probe_webview_login.py`
- Modify: `pyproject.toml` (optional group `probe = ["pywebview>=5", "httpx>=0.27"]`)

**Interfaces:**
- Produces: answers to spec questions 2 and 3 in `probe-results.txt` — whether icloud.com sign-in with 2FA completes in a pywebview window, which cookie *names* exist afterwards (never values), whether `validate` and `changes/zone` succeed through `httpx` using only those cookies, and whether a trust-token cookie (name containing `TRUST`) is present.

- [ ] **Step 1:** pywebview window at `https://www.icloud.com/`, private mode off, storage in `%TEMP%\rw-probe-webview` (outside the repo). Poll `window.get_cookies()` every 5 s until a cookie whose name starts with `X-APPLE-WEBAUTH` appears or 5 minutes pass.
- [ ] **Step 2:** Build an `httpx.Client` with those cookies and the iCloud headers; call `validate`, then `zones/list` and one `changes/zone` page. Log HTTP statuses and record counts only.
- [ ] **Step 3:** Delete `%TEMP%\rw-probe-webview` at the end. Martin runs it; the executor records the answers in spec section 3 and names the fallback from the spec's table if either fails.

---

### Task 4: Probe 4 — glass window and desktop pinning (Martin judges by eye)

**Files:**
- Create: `probes/probe_glass.py`, `probes/glass.html`

**Interfaces:**
- Produces: in `probe-results.txt`, which of these work on Martin's PC: transparent WebView2 background, Acrylic backdrop (`DwmSetWindowAttribute(hwnd, 38, 3)`), Mica-alt fallback (`DwmSetWindowAttribute(hwnd, 38, 4)`), staying at the bottom of the window order (`SetWindowPos(hwnd, HWND_BOTTOM, …, SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE)`) after `Win+D` and after clicking the desktop.

- [ ] **Step 1:** `glass.html` is the last approved mockup (transparent `body`, glass card, Open Sans from `C:\Users\oktamko\Documents\4_Zdroje\Fonts\písmo VUT\OpenSans-Regular.ttf` via `file://` for the probe only).
- [ ] **Step 2:** pywebview frameless window with `transparent=True`; get the HWND, apply each backdrop variant for 10 s with a console prompt; Martin answers `a/n` per variant through `Results.confirm`.
- [ ] **Step 3:** Pin to bottom, then ask Martin to press `Win+D`, click the desktop and open another window; confirm the widget stayed visible and under other windows.
- [ ] **Step 4:** The executor records the verdict in spec section 3.

---

### Task 5: Text CRDT codec

**Files:**
- Create: `src/reminders_widget/icloud/crdt.py`
- Test: `tests/test_crdt.py`

**Interfaces:**
- Produces: `encode_text(text: str) -> str` (base64 document), `decode_text(b64: str | None) -> str | None`. Used for titles and notes.

- [ ] **Step 1: Failing tests** — port `TitleDocumentTest` and `CompressionTest` from `probes/test_probe.py` to the new names, plus:

```python
def test_emoji_round_trip():
    assert decode_text(encode_text("Kúpiť 🥛 a 🍞")) == "Kúpiť 🥛 a 🍞"

def test_length_fields_use_confirmed_unit():
    # value of LENGTH_UNIT is fixed by Task 2 step 3: "codepoints" or "utf16"
    doc = gzip.decompress(base64.b64decode(encode_text("a🥛")))
    expected = 2 if LENGTH_UNIT == "codepoints" else 3
    assert _note_length(doc) == expected
```

`_note_length` is a test helper reading note field 5 → field 1.
- [ ] **Step 2:** Run `pytest tests/test_crdt.py -v`. Expected: FAIL, module not found.
- [ ] **Step 3:** Move the codec from `probes/probe.py` (`_varint` … `decode_title`) into `crdt.py`, rename, add `LENGTH_UNIT` from Task 2's answer and use it for every `char_len`. `probes/probe.py` imports from the package afterwards as `from reminders_widget.icloud.crdt import encode_text as encode_title, decode_text as decode_title`, so the probes and `probes/test_probe.py` keep working unchanged.
- [ ] **Step 4:** Run `pytest -q`. Expected: all pass, including `probes/test_probe.py`.
- [ ] **Step 5:** Commit `feat: text CRDT codec`.

---

### Task 6: Resolution token map

**Files:**
- Create: `src/reminders_widget/icloud/tokens.py`, `src/reminders_widget/icloud/errors.py`
- Test: `tests/test_tokens.py`

**Interfaces:**
- Produces: `advance_tokens(raw: str | None, keys: Iterable[str], now: float, replica_id: str) -> str`; `apple_now(unix_time: float) -> float`; `class UnknownFormat(Exception)` in `errors.py` (all error classes live there).

- [ ] **Step 1: Failing tests** — port the four `PriorityProbeTest` cases (renaming `set_priority_token(raw, now, rid)` to `advance_tokens(raw, ["priority"], now, rid)`, and the unparseable case now expects `pytest.raises(UnknownFormat)`), plus:

```python
def test_advances_several_keys_once_each():
    raw = json.dumps({"map": {"priority": {"counter": 2, "modificationTime": 10, "replicaID": "A"}}})
    out = json.loads(advance_tokens(raw, ["priority", "dueDate"], 50.0, "B"))["map"]
    assert out["priority"]["counter"] == 3
    assert out["dueDate"] == {"counter": 1, "modificationTime": 50.0, "replicaID": "B"}

def test_clock_behind_phone_still_moves_forward():   # Review Focus 3
    raw = json.dumps({"map": {"priority": {"counter": 4, "modificationTime": 815935172.2}}})
    out = json.loads(advance_tokens(raw, ["priority"], 815935000.0, "B"))["map"]
    assert out["priority"]["modificationTime"] == 815935173.2
    assert out["priority"]["counter"] == 5

def test_keeps_unknown_top_level_keys():
    raw = json.dumps({"map": {}, "extra": 1})
    assert json.loads(advance_tokens(raw, ["priority"], 1.0, "B"))["extra"] == 1

def test_apple_now():
    assert apple_now(978307200.0) == 0.0
```
- [ ] **Step 2:** Run, expect FAIL. **Step 3:** Implement. **Step 4:** Run, expect PASS. **Step 5:** Commit `feat: resolution token map`.

---

### Task 7: Record model

**Files:**
- Create: `src/reminders_widget/icloud/records.py`
- Test: `tests/test_records.py` (uses `tests/fixtures/records.json`)

**Interfaces:**
- Consumes: `encode_text`, `decode_text`, `advance_tokens`.
- Produces:

```python
@dataclass(frozen=True)
class ReminderList:
    record_name: str
    name: str
    color: str | None          # hex from the record if present, else None

@dataclass(frozen=True)
class Reminder:
    record_name: str
    list_id: str
    title: str | None          # None when undecodable
    notes: str | None
    completed: bool
    priority: int              # 0, 1, 5 or 9
    due: datetime | None       # timezone-aware
    deleted: bool
    change_tag: str
    readonly: bool             # True when title or notes failed to decode
    created: datetime | None

def parse_record(rec: dict) -> Reminder | ReminderList | None   # None for other record types
def to_cloud_fields(changes: dict[str, object]) -> tuple[dict[str, dict], list[str]]
    # changes keys: "title", "notes", "completed", "priority", "due", "deleted", "list_id"
    # returns ({CloudKit field: {"value": v}}, [ResolutionTokenMap keys to advance])
```

`FIELDS` in `records.py` is the one table mapping each change key to its CloudKit field name(s) and token key, filled from Task 2's answers. Known from the probes: title → `TitleDocument` / `titleDocument`, completed → `Completed` + `CompletionDate` (ms) / `completed`, priority → `Priority` / `priority`, list → `List` / `list`, deleted → `Deleted` / none.

- [ ] **Step 1: Failing tests:**
  - every record in `records.json` parses without exception; reminders with `Sample n` titles decode;
  - `to_cloud_fields({"priority": 5})` → `({"Priority": {"value": 5}}, ["priority"])`;
  - `to_cloud_fields({"completed": True})` sets `Completed` 1 and a `CompletionDate` in ms, token key `completed`;
  - `to_cloud_fields({"completed": False})` sets `Completed` 0 and `CompletionDate` `None`;
  - due date and notes cases using the names recorded in Task 2;
  - a reminder whose `TitleDocument` is `"bm90IGEgZG9jdW1lbnQ="` parses with `readonly=True` and `title=None`;
  - `to_cloud_fields({"color": "x"})` raises `KeyError` (unknown change keys are a programming error).
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS). **Step 5:** Commit `feat: record model`.

---

### Task 8: CloudKit client and fake iCloud

**Files:**
- Create: `src/reminders_widget/icloud/cloudkit.py`, `tests/fakes/fake_cloudkit.py`
- Modify: `src/reminders_widget/icloud/errors.py`
- Test: `tests/test_cloudkit.py`

**Interfaces:**
- Produces:

```python
class Transport(Protocol):
    def post(self, url: str, body: dict) -> tuple[int, dict]: ...   # raises OfflineError on network failure

# errors.py
class OfflineError(Exception): ...
class SessionExpired(Exception): ...
class LockConflict(Exception): ...
class RecordGone(Exception): ...            # NOT_FOUND on lookup
class UnknownResponse(Exception): ...

class CloudKit:
    def __init__(self, transport: Transport, base_url: str): ...
    def find_owner(self) -> str
    def changes(self, token: str | None) -> tuple[list[dict], str]       # follows moreComing
    def lookup(self, record_name: str) -> dict
    def modify(self, operation: dict) -> dict

class FakeCloudKit:   # implements Transport
    def __init__(self, records: list[dict] | None = None): ...
    def phone_edit(self, record_name: str, **fields) -> None   # changes fields, new change tag, new sync point
    def phone_delete(self, record_name: str) -> None           # Deleted=1
    offline: bool          # True → post raises OfflineError
    session_valid: bool    # False → post returns (421, {})
    fail_next: list[str]   # serverErrorCodes to return for the next modify calls, e.g. ["OP_LOCK_FAILURE"]
    writes: list[dict]     # every modify operation received, for assertions
```

Error mapping in `CloudKit`: HTTP 401 or 421 → `SessionExpired`; record `serverErrorCode` `OP_LOCK_FAILURE` or `CONFLICT` → `LockConflict`; `NOT_FOUND` → `RecordGone`; any other non-2xx or unexpected body shape → `UnknownResponse`. The fake checks `recordChangeTag` on update like the real server, issues change tags as `f"t{n}"` and sync tokens as `f"s{n}"`, and pages `changes/zone` at 2 records to exercise `moreComing`.

- [ ] **Step 1: Failing tests:** `changes(None)` over 5 records returns all 5 across pages; `changes(token)` after `phone_edit` returns only that record; an update with an old tag raises `LockConflict`; `session_valid=False` raises `SessionExpired`; `offline=True` raises `OfflineError`; lookup of a missing name raises `RecordGone`; a `(500, {})` response raises `UnknownResponse`.
- [ ] **Step 2–4:** Run (FAIL), implement both, run (PASS). Port `probes/probe.py`'s `ICloud` logic; the probe keeps its own copy. **Step 5:** Commit `feat: CloudKit client and fake`.

---

### Task 9: Local store with change queue

**Files:**
- Create: `src/reminders_widget/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Reminder`, `ReminderList`.
- Produces:

```python
@dataclass(frozen=True)
class QueueEntry:
    id: int
    record_name: str
    op: Literal["create", "update", "delete"]
    changes: dict[str, object]   # change keys as in to_cloud_fields

class Store:
    def __init__(self, path: Path | str): ...      # ":memory:" allowed
    def lists(self) -> list[ReminderList]
    def reminders(self) -> list[Reminder]          # not deleted, not completed
    def save_remote(self, items: list[Reminder | ReminderList], sync_token: str) -> None   # one transaction
    def sync_token(self) -> str | None
    def apply_local(self, record_name: str, op: str, changes: dict) -> None    # updates the cached row and enqueues, one transaction
    def pending(self) -> list[QueueEntry]          # oldest first
    def pending_keys(self, record_name: str) -> set[str]
    def ack(self, entry_id: int) -> None
    def drop(self, record_name: str) -> None       # remove all queued entries for a record
    def get_setting(self, key: str, default: object) -> object
    def set_setting(self, key: str, value: object) -> None   # JSON-encoded
```

Coalescing in `apply_local` (one queued entry per record at most):

| Queued | New | Result |
|---|---|---|
| none | any | new entry |
| create | update | create with merged changes |
| create | delete | both removed, nothing sent |
| update | update | update with merged changes, newer values win |
| update | delete | delete |

- [ ] **Step 1: Failing tests:** each row of the coalescing table; three priority clicks leave one entry with the last value; `apply_local` changes what `reminders()` returns at once; completing removes it from `reminders()`; `save_remote` and the sync token round-trip; **queue survives reopening the same file** (Review Focus 4: write to `tmp_path / "s.db"`, close, reopen, `pending()` unchanged); settings round-trip a list of strings.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS). **Step 5:** Commit `feat: local store and change queue`.

---

### Task 10: Sync engine

**Files:**
- Create: `src/reminders_widget/sync.py`
- Test: `tests/test_sync.py`

**Interfaces:**
- Consumes: `CloudKit`, `Store`, `parse_record`, `to_cloud_fields`, `advance_tokens`, `apple_now`, the error classes.
- Produces:

```python
@dataclass(frozen=True)
class SyncResult:
    status: Literal["ok", "offline", "session_expired", "stopped"]
    notices: list[str]          # user-facing English, e.g. "A reminder you changed was deleted on another device."

class SyncEngine:
    def __init__(self, cloud: CloudKit, store: Store, replica_id: str, clock: Callable[[], float] = time.time): ...
    def run_once(self) -> SyncResult        # push then pull
    
def next_delay(failures: int) -> int        # 0→15, 1→30, 2→60, 3→120, ≥4→300 seconds

def merge_incoming(local: Reminder | None, incoming: Reminder, pending: set[str]) -> Reminder
    # incoming wins for every field not in `pending`; local keeps fields in `pending`
```

Push, per `QueueEntry`: create → `modify` create with `to_cloud_fields` fields (no token map). update/delete → `lookup`; if the server record is `Deleted` or `RecordGone`, `store.drop`, add notice, continue; if the parsed record is `readonly`, `store.drop` and add notice "This reminder can't be changed on PC yet."; otherwise fields + `advance_tokens(existing map, keys, apple_now(clock()), replica_id)` → `modify` with the fresh tag; `LockConflict` → repeat lookup+modify up to 3 times, then leave the entry queued. `ack` on success.
Pull: `changes(store.sync_token())`, parse, `merge_incoming` against cached rows and `pending_keys`, `save_remote`.
Errors: `OfflineError` → `offline`; `SessionExpired` → `session_expired`; three consecutive `run_once` calls ending in `UnknownResponse` → `stopped`, and `stopped` stays until a new `SyncEngine` is built.
Logging through `logging.getLogger("reminders_widget.sync")`: counts, statuses, record name hashes (`sha256[:8]`) only.

> **Contribution point for Martin:** `merge_incoming` is 5–10 lines and decides what the person sees when iPhone and PC disagree. The executor writes its tests first, leaves the body as `raise NotImplementedError`, and asks Martin to write it.

- [ ] **Step 1: Failing tests (all against `FakeCloudKit`):**
  - local priority change reaches the fake with `Priority` 5 and `ResolutionTokenMap["map"]["priority"]["counter"]` advanced;
  - `phone_edit(priority=9)` arrives in `store.reminders()` after `run_once`;
  - `merge_incoming`: phone changes due date while PC has pending priority → both kept; phone changes priority while PC has pending priority → local value kept on screen and sent;
  - **phone deletes while PC edit pending** (Review Focus 2) → `fake.writes` has no update for it, notice present, `pending()` empty, not in `reminders()`;
  - `fail_next=["OP_LOCK_FAILURE"]` → succeeds on retry; four failures → entry still pending, status `ok`;
  - readonly reminder edit → never written, notice present;
  - `offline=True` → `offline`, queue untouched; `session_valid=False` → `session_expired`;
  - three `UnknownResponse` runs → `stopped`;
  - `next_delay` values 0..5 → 15, 30, 60, 120, 300, 300;
  - **log has no content** (Review Focus 5): reminder titled `SECRET-TITLE` with notes `SECRET-NOTE`, full sync with `caplog` at DEBUG → neither string in `caplog.text`.
- [ ] **Step 2:** Run, expect FAIL.
- [ ] **Step 3:** Implement `SyncEngine` and `next_delay`; `merge_incoming` per the contribution point.
- [ ] **Step 4:** Run `pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit `feat: sync engine`.

---

### Task 11: List view logic

**Files:**
- Create: `src/reminders_widget/view.py`
- Test: `tests/test_view.py`

**Interfaces:**
- Consumes: `Reminder`.
- Produces: `visible(reminders: list[Reminder], selected: set[str], sort: Literal["priority", "icloud"]) -> list[Reminder]`; `title_for(lists: list[ReminderList], selected: set[str]) -> str` ("All" when every list is selected, else names joined with " + " in list order); `ensure_selection(lists, selected) -> set[str]` (drops ids that no longer exist; empty → all lists).

Priority sort: 1, 5, 9, then 0; ties by `created`, oldest first. `icloud` order: `created` (the iCloud stored order replaces it if Task 2 finds it decodable — then this task reads it from the list record).

- [ ] **Step 1: Failing tests:** two selected lists merge and sort 1,1,5,9,0; unselected lists excluded; `title_for` gives `"Práca + Škola"` and `"All"`; a deleted list id in the saved selection is dropped; empty selection becomes all.
- [ ] **Step 2–4:** Run (FAIL), implement, run (PASS). **Step 5:** Commit `feat: list view logic`.

---

## What plan 2 receives

`Store`, `SyncEngine.run_once`, `next_delay`, `visible`, `title_for`, `ensure_selection`, the `Transport` protocol, and the probe answers in spec section 3 deciding the concrete transport, the sign-in window and the glass technique.
