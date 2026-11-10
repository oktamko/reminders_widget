# Plan 2a — First Usable App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A normal Windows window where Martin signs in to iCloud once and then sees, adds, completes, re-prioritises, renames, annotates and re-dates his real reminders, synced both ways with his iPhone through the plan-1 core.

**Architecture:** A `reminders_widget.app` package wires the plan-1 core to the outside world: a DPAPI-protected cookie store, an `httpx` transport, a WebView2 sign-in window, a background sync scheduler, and a pywebview window whose HTML/JS talks to a Python `Api` object. The window only calls `Api`; `Api` only touches `Store` and pokes the scheduler; only the scheduler runs `SyncEngine`.

**Tech Stack:** Python 3.12, pywebview 6.2.1 (WebView2/WinForms), httpx 0.28, ctypes (crypt32 DPAPI), SQLite via the plan-1 `Store`, pytest, Playwright (UI tests against a fake bridge).

**Spec:** `docs/superpowers/specs/2026-11-09-reminders-widget-design.md` (sections 4–7, the probe answers in section 3) and `docs/superpowers/plans/2026-11-09-plan-1-followups.md`.

Plan 2b (desktop widget mode, tray, glass from the wallpaper, motion polish, Win+D, autostart, single instance) builds on this window. Plan 3 is distribution.

## Global Constraints

- Interface text is English only; sentences without "please", "successfully" or exclamation marks (spec §5, CDS copy rules already used in plan 1 notices).
- Data folder: `%LOCALAPPDATA%\RemindersWidget\` with `reminders.db`, `session.bin`, `webview\` (sign-in profile) and `logs\`. Nothing of it inside the repo.
- Logs and probe results never contain reminder titles, notes, list names, cookie values, exception messages or file paths with the user name.
- Cookies are stored only DPAPI-encrypted (`CryptProtectData`, current user); never written in clear, never logged (names may be logged).
- The app never writes to iCloud except through `SyncEngine`; tests never touch real iCloud.
- Sync cadence: every 15 s; 1 s after a local change (debounced); immediately when the window is shown and after the PC wakes; failures back off with `retry_delay` (15 s → 5 min).
- Martin signs in himself; the executor never types credentials. Human-run steps are marked **(Martin runs it)**.
- Commits after each task; message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Starting with no network.** Expected: the window opens with the cached reminders, the dot is orange, edits queue; no crash, no sign-in window. Test in Task 7.
2. **Dates from JavaScript.** `"2026-11-12T09:00"` from an `<input type=datetime-local>` is naive. Expected: interpreted in the PC's local time zone and stored aware; `"2026-11-12"` with all-day is a `date`. Test in Task 6.
3. **Session expires mid-use.** Expected: one silent re-validation; if that fails, a "Sign in again to keep syncing" bar; edits keep working locally and are sent after sign-in. Test in Task 7.
4. **Many quick clicks.** Five priority clicks within a second. Expected: one sync run, the last value sent. Test in Task 5.
5. **Closing the window during a sync.** Expected: the scheduler stops within 2 s, nothing is left in flight on the next start, no exception. Test in Task 5.

---

### Task 1: Probe round 4 — open questions from plan 1 (Martin runs it)

**Files:**
- Create: `probes/probe_round4.py`, `probes/test_probe_round4.py`

**Interfaces:**
- Consumes: `ICloud`, `TestReminder`, `Results`, `launch_browser`, `wait_for_login`, `choose_list`, `split_records` from `probes/probe.py`; `AppendResults` from `probes/probe_webview_login.py`; `encode_text` from `reminders_widget.icloud.crdt`.
- Produces: answers in `probe-results.txt` under `--- kolo 4 ---`, recorded by the executor in spec §3 and in the follow-ups file.

- [ ] **Step 1: Write the probe** (tests first for the pure helpers: colour-value summariser, order comparison):
  - **A. Duplicate create:** create a test reminder, then send the same `create` again → log the returned `serverErrorCode` (expected `EXISTS` or `CONFLICT`).
  - **B. List colour:** for every List record log the `Color` field's CloudKit type and value shape: if a JSON string, its keys and the values of keys containing `hex` or `color` (colours are not personal); never `Name`.
  - **C. Stored order:** for every List with `ReminderIDs`, log its type, whether it parses as JSON, and whether it is a permutation of that list's reminder ids (counts only).
  - **D. Recurring:** Martin sets the test reminder to repeat daily on the iPhone → log the new fields/token keys → Martin completes it on the iPhone → log which fields changed (names, `Completed` value, whether `DueDate` moved and by how many days).
  - **E. Alarm vs due:** Martin adds an alert at the due time on the iPhone → PC moves `DueDate` +1 day (token `dueDate`) → confirm "Did the alert move to the new time?" (a/n).
  - Clean up with `Deleted=1` in `finally`.
- [ ] **Step 2:** `.\.venv\Scripts\python.exe -m pytest -q probes` passes. Commit `feat: probe round 4`.
- [ ] **Step 3 (Martin runs it):** `.\.venv\Scripts\python.exe probes\probe_round4.py`. The executor records the answers in spec §3 ("Answers (probe round 4)") and adjusts: duplicate-create code in `cloudkit.py`/fake if it is not `EXISTS`/`CONFLICT`; `ReminderList.color` parsing in `records.py` if a hex is found (with a test); recurring and alarm rules stay as they are unless the probe shows the PC can safely complete/redate them (record a follow-up, do not implement here).

---

### Task 2: App data folder and logging

**Files:**
- Create: `src/reminders_widget/app/__init__.py`, `src/reminders_widget/app/paths.py`, `src/reminders_widget/app/logsetup.py`
- Test: `tests/app/test_paths_logs.py`

**Interfaces:**
- Produces: `data_dir(base: Path | None = None) -> Path` (creates `%LOCALAPPDATA%\RemindersWidget`, or `base` in tests); `db_path(d)`, `session_path(d)`, `webview_dir(d)`, `logs_dir(d)`; `setup_logging(logs: Path) -> None` (rotating `app.log`, 1 MB × 3, level INFO, format `%(asctime)s %(levelname)s %(name)s %(message)s`).
- [ ] Tests: folder created under a `tmp_path` base; `setup_logging` writes a line from `logging.getLogger("reminders_widget.sync")`; rotation keeps ≤ 4 files; calling twice does not add duplicate handlers.
- [ ] Implement, run, commit `feat: app data folder and logging`.

---

### Task 3: DPAPI cookie store

**Files:**
- Create: `src/reminders_widget/app/secrets.py`
- Test: `tests/app/test_secrets.py`

**Interfaces:**
- Produces: `protect(data: bytes) -> bytes`, `unprotect(blob: bytes) -> bytes` (ctypes `crypt32.CryptProtectData`/`CryptUnprotectData`, `CRYPTPROTECT_UI_FORBIDDEN`, free with `LocalFree`; raise `SecretError` on failure); `@dataclass Cookie(name, value, domain, path, secure, expires: float | None)`; `save_cookies(path: Path, cookies: list[Cookie]) -> None` (JSON → protect → atomic replace); `load_cookies(path: Path) -> list[Cookie] | None` (None if missing or undecryptable — never raise to the caller).
- [ ] Tests: round trip on this PC; the file bytes do not contain a cookie value; a corrupted file → `None`; a missing file → `None`; `repr(Cookie)` hides `value`.
- [ ] Implement, run, commit `feat: DPAPI cookie store`.

---

### Task 4: httpx transport and session

**Files:**
- Create: `src/reminders_widget/app/transport.py`
- Test: `tests/app/test_transport.py` (uses `httpx.MockTransport`)

**Interfaces:**
- Consumes: `Cookie`, `save_cookies` (Task 3); `Transport` protocol, `OfflineError`, `SessionExpired` from plan 1.
- Produces:

```python
class HttpxTransport:          # implements reminders_widget.icloud.cloudkit.Transport
    def __init__(self, cookies: list[Cookie], on_cookies: Callable[[list[Cookie]], None],
                 client: httpx.Client | None = None, timeout: float = 30.0): ...
    def post(self, url: str, body: dict) -> tuple[int, dict]
    def validate(self) -> str          # POST setup.icloud.com/setup/ws/1/validate body "null" → ckdatabasews url
    def cookies(self) -> list[Cookie]

class Session:
    def __init__(self, transport: HttpxTransport): ...
    def base_url(self) -> str          # cached validate(); SessionExpired if validate is 401/421 or has no ckdatabasews url
    def renew(self) -> bool            # one silent validate after a SessionExpired; logs "session renewed silently" or "manual sign-in needed"
```

Headers `Content-Type: application/json`, `Origin: https://www.icloud.com`, `Referer: https://www.icloud.com/`. Network errors (`httpx.TransportError`) → `OfflineError`. Every response's `Set-Cookie` updates the jar and calls `on_cookies` when anything changed. Non-JSON bodies → `{}`.
- [ ] Tests: headers sent; cookies sent only to `*.icloud.com`; Set-Cookie updates and persists through `on_cookies`; connect error → `OfflineError`; validate 421 → `SessionExpired`; `renew()` true when a second validate succeeds, false otherwise; no cookie value appears in `caplog`.
- [ ] Implement, run, commit `feat: httpx transport and session`.

---

### Task 5: Sync scheduler

**Files:**
- Create: `src/reminders_widget/app/scheduler.py`
- Test: `tests/app/test_scheduler.py`

**Interfaces:**
- Consumes: `SyncEngine.run_once() -> SyncResult`, `retry_delay`.
- Produces:

```python
class Scheduler:
    def __init__(self, run: Callable[[], SyncResult], on_result: Callable[[SyncResult], None],
                 *, period: float = 15.0, debounce: float = 1.0,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time): ...
    def start(self) -> None            # background daemon thread
    def poke(self) -> None             # a local change: run after `debounce`, coalescing pokes
    def now(self) -> None              # window shown: run as soon as possible
    def stop(self, timeout: float = 2.0) -> None
```

Loop: wait for the earliest of (next period, debounce deadline, `now()`); after a run, `failures` resets on "ok" and increments otherwise; the next wait is `retry_delay(result, failures)` after a failure, `period` after "ok". A wall-clock jump larger than `2 × period` beyond the monotonic elapsed time (PC slept) triggers an immediate run. "stopped" ends the loop until `start()` is called again. `on_result` runs on the scheduler thread; exceptions from `run` or `on_result` are logged by type and never kill the thread.
- [ ] Tests (fake clock with a condition variable, no real sleeps > 0.2 s): five pokes within 1 s → one run (Review Focus 4); failure sequence follows 15/30/60 s; `retry_after` 120 honoured; wall jump → immediate run; `stop()` during a slow run returns within 2 s (Review Focus 5); an exception in `run` keeps the loop alive.
- [ ] Implement, run, commit `feat: sync scheduler`.

---

### Task 6: Window API

**Files:**
- Create: `src/reminders_widget/app/api.py`
- Test: `tests/app/test_api.py`

**Interfaces:**
- Consumes: `Store` (apply_local, lists, reminders, get/set_setting), `view.visible`, `view.title_for`, `view.ensure_selection`, `records.new_reminder_name`, `Scheduler.poke`, `SyncResult`.
- Produces: `class Api` (exposed to JS as `window.pywebview.api`; every public method returns JSON-serialisable values and never raises to JS — errors become `{"ok": False, "message": str}` with a fixed English message):

```python
def state(self) -> dict          # {"lists":[{id,name,color}], "selected":[ids], "sort":"priority"|"icloud",
                                 #  "title": str, "reminders":[{id,list,title,notes,priority,due,due_label,
                                 #  overdue,all_day,readonly,recurring}], "status": str, "banner": dict|None}
def select(self, list_ids: list[str]) -> dict
def set_sort(self, sort: str) -> dict
def add(self, title: str) -> dict                       # into the first selected list
def complete(self, record_name: str, done: bool) -> dict
def set_priority(self, record_name: str, priority: int) -> dict
def rename(self, record_name: str, title: str) -> dict
def set_notes(self, record_name: str, notes: str) -> dict
def set_due(self, record_name: str, value: str | None, all_day: bool) -> dict
def on_sync(self, result: SyncResult) -> None           # called by the scheduler; stores status/notices, pushes state to JS
```

`set_due`: `None`/"" → no due; all-day "YYYY-MM-DD" → `date`; timed "YYYY-MM-DDTHH:MM" → local time zone (`datetime.now().astimezone().tzinfo`) then aware (Review Focus 2). Selection and sort persist via store settings `"selected"` and `"sort"`. After every successful change: `scheduler.poke()`. `ValueError` from `apply_local` (read-only, recurring completion) → `{"ok": False, "message": <the ValueError's fixed text>}` — those texts are code constants, not user data.

> **Contribution point for Martin:** `banner_for(status: str, notices: list[str]) -> dict | None` in `api.py` decides what the bar at the top says (`{"text": ..., "action": "sign_in" | None, "tone": "info" | "warning" | "error"}`) for each status: "session_expired" → "Sign in again to keep syncing." with action `sign_in`; "offline" → …; "stopped" → "iCloud changed something. Check for an update."; notices → shown one at a time. 5–10 lines; the executor writes its tests first and leaves the body to Martin.

- [ ] Tests: every method against an in-memory Store with a fake scheduler; naive datetime-local string converted to aware local; all-day date; read-only and recurring refusals return `ok: False` with the fixed text and queue nothing; selection survives a new `Api` on the same store; `state()` never contains completed or deleted reminders; `on_sync` with notices → banner.
- [ ] Implement (except `banner_for`'s body), run, commit `feat: window API`; Martin writes `banner_for`.

---

### Task 7: The window

**Files:**
- Create: `src/reminders_widget/app/ui/index.html`, `src/reminders_widget/app/ui/app.js`, `src/reminders_widget/app/ui/style.css` (font stack `"Segoe UI Variable Text", "Segoe UI", sans-serif` — bundling Open Sans and the optional Vafle heading font is plan 3's packaging work, so no font files enter the repo now)
- Create: `src/reminders_widget/app/main.py`, `src/reminders_widget/__main__.py`, `src/reminders_widget/app/signin.py`
- Test: `tests/app/test_ui.py` (Playwright on the HTML with a fake `window.pywebview.api` in JS), `tests/app/test_main.py`

**Interfaces:**
- Consumes: Tasks 2–6; `probes/demo_widget.html` is the visual starting point (approved look, picker, sort animation, FLIP, priority pop, ring undo).
- Produces: `python -m reminders_widget` opens the app. `signin.sign_in(webview_dir: Path) -> list[Cookie] | None` opens a pywebview window at `https://www.icloud.com/`, waits until `X-APPLE-WEBAUTH-TOKEN` is present (plus up to 60 s for a TRUST cookie, as in the probe), returns the cookies (or None if the window is closed). `main.run(data: Path | None = None) -> None`.

UI behaviour (on top of the demo): every action calls the `Api` and re-renders from the returned/pushed state (no local-only edits); double-click a title → inline edit (Enter saves, Escape cancels); right-click a row → menu with priority (High/Medium/Low/None), due date, notes, rename; keys `1` `2` `3` `0` on a focused row set high/medium/low/none (spec §5); the arrow on a row opens a detail panel sliding in from the right with title, due date (`datetime-local`), "All day" toggle, notes (`textarea`, saved on blur), list name; the status dot shows ok/syncing/offline/sign-in states; the banner from `state.banner` with an action button; read-only rows show the lock and refuse edits client-side too. `window.pywebview.api.state()` on load and on a `rw:state` event the Python side dispatches with `window.evaluate_js("window.dispatchEvent(new CustomEvent('rw:state',{detail:…}))")`.

`main.run`: data folder → logging → Store → cookies (`load_cookies`; if None → `sign_in`, save) → `HttpxTransport` → `Session.base_url()` (SessionExpired → `renew()` → else sign-in) → `CloudKit` → `SyncEngine` → `Scheduler(run, api.on_sync)` → pywebview window 400×640, title "Reminders", `js_api=api` → `scheduler.now()` on `shown` → `scheduler.stop()` on `closed`. If the network is down at start, open the window with cached data and let the scheduler report "offline" (Review Focus 1). On a "session_expired" result the scheduler first calls `session.renew()`; if false the banner action `sign_in` runs `sign_in` on the GUI thread, saves cookies, rebuilds the transport's jar and calls `scheduler.now()` (Review Focus 3).

- [ ] UI tests (Playwright, fake api recording calls): priority click calls `set_priority` with the cycled value; keys `1`/`2`/`3`/`0` on a focused row call `set_priority` with 1/5/9/0; the right-click menu's items call the matching api methods; ring click then 1.5 s → `complete(id, true)`, a second click within 1.5 s cancels; double-click rename → `rename`; detail panel due/all-day/notes → `set_due`/`set_notes` with the exact string formats; banner action → `sign_in`; read-only row → no api call.
- [ ] Main tests (no GUI: inject fakes for `sign_in`, window factory and transport): first start without cookies calls `sign_in` once and saves; offline at start opens the window and reports offline (Review Focus 1); session_expired → renew true → no sign-in; renew false → banner with `sign_in`; closing stops the scheduler.
- [ ] Implement, run all tests, commit `feat: the window`.

---

### Task 8: First real run (Martin runs it)

- [ ] `.\.venv\Scripts\python.exe -m reminders_widget` → sign in (first start), then on a list named for testing (Martin creates "Widget test" on the iPhone): add, complete and undo, priority up/down, rename, notes, due date with time, all-day due, merge two lists and sort by priority; check each on the iPhone within ~30 s; change something on the iPhone and see it on the PC within ~30 s; quit and restart (no sign-in asked); unplug the network, edit, reconnect (edit arrives). The executor turns Martin's answers into a short checklist result in `docs/superpowers/plans/2026-11-10-plan-2a-checklist.md` (no reminder titles).
