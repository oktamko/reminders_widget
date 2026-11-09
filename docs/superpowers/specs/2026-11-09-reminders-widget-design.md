# Reminders Widget for Windows — Design

Date: 2026-11-09
Status: draft, awaiting review

## 1. Purpose

People who use Apple Reminders on iPhone and iPad but work on a Windows 11 PC
have no way to see or change their reminders there. This app puts their
reminders on the Windows desktop and syncs both ways through iCloud, so the
iPhone and iPad keep working exactly as before.

The app is a public open-source project on GitHub. Anyone with an Apple ID and
Windows 11 can install it. Each person signs in with their own Apple ID on
their own PC; there is no server and no shared account.

### Success criteria

- A change made on the PC appears on the iPhone within about 30 seconds, and a
  change made on the iPhone appears on the PC within about 30 seconds.
- The widget opens instantly from local data, works offline, and loses no
  change when the PC sleeps, shuts down or goes offline mid-sync.
- Signing in again is rare: a session renews itself silently, and the user is
  asked for their password and code only when Apple requires it.
- The app never damages reminders on Apple devices, even when Apple changes
  its private format.

### Out of scope for the first version

- Notifications on the PC (the iPhone already notifies).
- Viewing completed reminders. Completing can be undone during a short delay
  before the reminder leaves the list.
- Reordering by drag and drop.
- Subtasks, tags, flags, location or recurring rules (shown as-is if present,
  never edited).
- Platforms other than Windows 11.
- Any language other than English in the interface.

## 2. Known facts from the feasibility probes

The probes (`probe.py`, `probe_priority.py`) ran against a real iCloud account
and established:

| Capability | Result |
|---|---|
| Sign-in through icloud.com, session reused between runs | works |
| Read lists and reminders (`changes/zone`) | works |
| Decode titles (`TitleDocument`: gzip, zlib or plain protobuf) | 34 of 34 |
| Create, complete, soft-delete (`Deleted=1`) from PC | reaches iPhone in about 6–26 s |
| iPhone change reaches PC | about 6 s |
| Set priority from PC, including after an iPhone change | works when the `ResolutionTokenMap` entry is advanced |

Facts the design relies on:

- Endpoint base comes from `POST https://setup.icloud.com/setup/ws/1/validate`
  → `webservices.ckdatabasews.url`. Database `com.apple.reminders`, zone
  `Reminders`, private scope.
- Every write must start from a fresh `records/lookup`, because the iPhone
  rewrites records after syncing and old change tags fail with
  `OP_LOCK_FAILURE`.
- `ResolutionTokenMap` holds a per-field `{counter, modificationTime,
  replicaID}` entry. Apple devices ignore a field change whose entry was not
  advanced. Times count seconds from 2001-01-01.
- Priority values: 0 none, 1 high, 5 medium, 9 low.

## 3. Open questions to verify before product code

Each is answered by a small throwaway probe, like the earlier ones, writing
only to the probe's own test reminder.

1. **Due date, notes, rename.** Which fields and `ResolutionTokenMap` keys the
   iPhone writes for each, and whether the PC can change them the same way it
   changes priority.
2. **Cookies without a browser.** Whether iCloud accepts the session cookies
   from a plain HTTP client after sign-in.
3. **Sign-in inside the app window.** Whether icloud.com sign-in, including
   two-factor authentication, works in an embedded WebView2 window, and
   whether its cookies can be read out.
4. **Transparent window with Acrylic.** Whether a pywebview window can have a
   transparent WebView2 background over a Windows Acrylic backdrop.
5. **Session lifetime.** How often a session renews silently through the
   trust token and how often a person must sign in again. Measured in use,
   logged by the app.

Fallbacks:

| Question | Fallback if it fails |
|---|---|
| 1 | That field is shown read-only and changed on the iPhone |
| 2 | A hidden browser (Playwright's bundled Chromium) stays running for requests |
| 3 | Sign-in opens in a separate Playwright-controlled Chromium window |

### Answers (probe round 1, 2026-11-09)

| Question | Answer |
|---|---|
| 1. Due date | `DueDate` (TIMESTAMP, ms) with token key `dueDate`; the iPhone also writes `AllDay` (`allDay`). PC change reached the iPhone in 18 s. |
| 1. Notes | `NotesDocument` (same CRDT format as titles) with token key `notesDocument`. |
| 1. Rename and notes edit | **Fails when the PC sends a fresh document**: the iPhone merges it with its own copy, so the old text stays and the new text is appended. Titles and notes are text CRDTs (the Apple Notes "topotext" format): the text field holds only visible text, deleted runs stay as tombstoned substrings, and an iPhone rename inserts a new run from its own replica and tombstones the old runs. The PC must edit the existing document the same way (see section 4, `icloud.crdt`). |
| 1. Emoji | Lengths count UTF-16 code units. |
| 2, 3. Sign-in in WebView2 | Not answered: the window was closed before sign-in. Re-run in probe round 2. |
| 4. Glass | DWM calls succeed, but WebView2 in a WinForms window paints black over the Acrylic/Mica backdrop. **Decision (Martin): glass is rendered from the wallpaper** (section 5). |
| 4. Pinning | Bottom of the window order holds after a desktop click and when other windows open. **`Win+D` hides the widget**; pinning into the desktop window is tested in probe round 2. |

### Answers (probe round 2, 2026-11-09)

| Question | Answer |
|---|---|
| PC rename/notes via `edit_text` | Not exact on the iPhone. Cause: the replica table (vector timestamp) is kept **sorted by UUID** in every multi-replica document observed (10 of 10), and char IDs refer to the sorted position; the editor appended the PC replica at the end. The second PC rename was correctly refused (`UnknownFormat`) because the iPhone's merge produced a branching substring graph. Fix: insert the PC replica at its sorted position and renumber, and accept branching graphs for a full replace. |
| 2, 3. Sign-in in WebView2 | **Works.** Sign-in with 2FA completes in a pywebview window; the session cookies (`X-APPLE-WEBAUTH-*`, domain `.icloud.com`) are readable; `validate`, `zones/list` and `changes/zone` succeed through plain `httpx` with them. No cookie containing "TRUST" appeared, so silent renewal (question 5) must be measured in use. |
| Glass from wallpaper | Looks like glass, corners clean, follows the window when dragged; **not aligned** — the blurred layer is smaller than the card and offset. To fix in the shell. |
| Win+D | Parenting into the 24H2 `WorkerW` keeps the widget visible after `Win+D` and under other windows, but it **receives no clicks or drags** (the icon layer is above it). Alternatives tested in round 3. |

### Answers (probe round 3, 2026-11-09)

| Question | Answer |
|---|---|
| PC rename/notes via `edit_text` (sorted replica insert, branching graphs) | **Works.** Rename, note and a second rename after an iPhone rename all appeared exactly on the iPhone within 7–14 s. A read-only dry run over all 217 real titles and notes parsed, edited and edited back exactly. |
| Glass from wallpaper | Looks like glass, corners clean, follows drags; **alignment still off**. To be solved in plan 2. |
| Win+D, D1 (child of Progman above `SHELLDLL_DefView`) | Visible after `Win+D`, under other windows; no clicks, no drags. Rejected. |
| Win+D, D2 (top-level, foreground hook re-shows it) | Visible after `Win+D`, draggable, under other windows; **click not working** in the probe. Most promising; to be finished in plan 2. |

Edge cannot be assumed: it can be removed from Windows, as on the PC used for
the probes. The WebView2 runtime is a separate component and is checked by the
installer (section 8).
| 4 | A slightly transparent window without blur |
| 5 | Nothing to fall back to; the number goes into the README |

## 4. Architecture

Python application with an HTML/CSS/JS interface rendered by pywebview on
WebView2, which is built into Windows 11.

```
┌──────────── window (HTML/CSS/JS) ───────────┐
│  lists · filter · sort · edit               │
└──────────────▲───────────────┬──────────────┘
      new state│               │action
┌──────────────┴───────────────▼──────────────┐
│  app     bridge between window and logic    │
├─────────────────────────────────────────────┤
│  store   local SQLite: reminders, lists,    │
│          sync token, pending change queue   │
├─────────────────────────────────────────────┤
│  sync    pulls changes, pushes queue,       │
│          resolves conflicts                 │
├─────────────────────────────────────────────┤
│  icloud  session, CloudKit calls,           │
│          title and token encoding           │
└─────────────────────────────────────────────┘
  shell: desktop / tray / window, autostart, single instance
```

### Units

| Unit | Responsibility | Depends on |
|---|---|---|
| `icloud.session` | Sign-in hand-off, encrypted cookie and trust-token storage (DPAPI), silent renewal | nothing in the app |
| `icloud.cloudkit` | `changes/zone`, `records/lookup`, `records/modify`; maps HTTP errors to typed errors (`SessionExpired`, `LockConflict`, `Offline`, `UnknownResponse`) | `icloud.session` |
| `icloud.crdt` | Text CRDT for `TitleDocument` and `NotesDocument`: decode visible text, build a new document for a new reminder, and **edit an existing document** by tombstoning the old runs and inserting the new text from the PC's own replica UUID, the way an iPhone does — never by replacing the document | nothing (pure) |
| `icloud.codec` | `ResolutionTokenMap` advance for any field, record ↔ `Reminder` model | `icloud.crdt` |
| `store` | SQLite cache, pending change queue with coalescing, saved settings (selected lists, sort, window mode) | nothing |
| `sync` | The only unit that talks to iCloud: pull, push, retry, conflict rules | `icloud`, `store` |
| `app` | Exposes actions to the window (`complete`, `set_priority`, `add`, `rename`, `set_due`, `set_notes`), pushes state changes to the window | `store`, `sync` |
| `ui` | HTML/CSS/JS; knows only local state and calls `app` | `app` (through the pywebview JS bridge) |
| `shell` | Desktop-pinned, tray and normal window modes; autostart; single instance; wallpaper luminance sampling | Windows APIs |

The window never talks to iCloud. Every user action changes the store
immediately and the sync unit catches up, so the interface responds at once.

## 5. Interface

### Window modes

One window, three ways to show it:

- **Desktop widget** (default): borderless, pinned to the bottom of the window
  order, hidden from the taskbar. Visible over the wallpaper, and still
  visible after `Win+D` (show desktop).
- **Tray**: an icon in the notification area; a click or a configurable hotkey
  (default `Ctrl+Alt+R`) shows the same content.
- **Normal window**: resizable, with a taskbar button. When wide, the list
  picker becomes a sidebar with counts.

### Layout of the widget

- **Header**: list picker icon on the left; reminder count, sort toggle and
  sync status dot on the right.
- **Title**: the selected lists joined with " + ", or "All" when every list is
  selected; sort description beneath.
- **Rows**: ring in the list's color, title, a metadata line (list name with a
  color dot when several lists are shown, due date in a warning color when
  overdue, a notes icon), priority marks on the right (`!!!`, `!!`, `!`).
- **Add field** at the bottom: typing and Enter adds a reminder to the first
  selected list.

### Interactions

| Action | Behaviour |
|---|---|
| Hover the list picker | Expands downward into a glass panel listing every list with a checkmark; content below blurs and dims; closes when the pointer leaves |
| Click a list in the panel | Toggles it; at least one list stays selected; selection is remembered |
| Click the sort toggle | Switches between priority order and the iCloud order |
| Click a ring | Fills and checks; after 1.5 s the row slides out. Clicking again before that undoes it |
| Click priority marks | Cycles none → `!` → `!!` → `!!!` → none |
| Right-click a row | Menu with priority, due date, notes, rename |
| Keys `1` `2` `3` `0` on a focused row | High, medium, low, none |
| Double-click a title | Edits it in place; Enter saves, Escape cancels |
| Arrow on a row | Slides in a detail panel with due date and time, notes, and list |

### Look

- **Glass from the wallpaper**: the widget sits on the desktop under all
  windows, so what is behind it is the wallpaper. The shell reads the current
  wallpaper file and its fit mode, computes the part of the image behind the
  widget on its monitor, and hands it to the page, which blurs it as the
  glass background. Because the image is inside the page, the edges can also
  bend light like iOS glass. CSS adds the light inner edge at the top and a
  faint one at the bottom. The window is opaque with rounded corners
  (`DWMWA_WINDOW_CORNER_PREFERENCE`); desktop icons behind the widget are not
  shown. The image is reloaded when the wallpaper, the widget position or the
  monitor layout changes. In the normal window mode, where other windows can
  be behind it, the background is the tinted style without the wallpaper.
- **Adaptive contrast**: all colors are tokens (`--fg`, `--fg2`, `--glass`,
  `--pane`, `--line`, `--pri`, `--late`) with a light and a dark set. The shell
  samples the wallpaper under the widget and picks the set; the glass tint is
  strong enough for a 4.5:1 text contrast on any wallpaper. List colors appear
  only on dots and rings, never as text.
- **Type**: Open Sans for everything, bundled from the Google Fonts release
  under the SIL Open Font License. If the Vafle VUT
  font is installed on the PC, headings use it. Vafle is never bundled or
  committed: its license does not allow redistribution.
- **Light and dark** follow Windows.

### Motion

Every interaction animates, using only `transform` and `opacity`, with spring
curves (`cubic-bezier` overshoot):

| Interaction | Animation |
|---|---|
| Sort toggle | Both arrows swap places and turn; rows glide to new positions (FLIP); sort label cross-fades |
| Priority change | Marks pop with a spring; the row glides to its new place when sorted by priority |
| Complete | Ring grows and fills; row slides out sideways; rows below move up |
| Add | New row drops in from above |
| List picker | Circle grows into a panel; lists appear one after another; checkmarks pop |
| Hover | Row highlights; ring grows slightly |
| Detail and rename | Panel slides in from the right; the edit field opens in place |
| Sync status | Dot pulses while syncing; color changes cross-fade |

When Windows "Animation effects" is off, every animation becomes a short
cross-fade.

## 6. Synchronization

### Pull

- Every 15 s, `changes/zone` with the saved `syncToken`, following
  `moreComing` until done.
- Also right after a local change (debounced 1 s), when the window is shown,
  and after the PC wakes.
- On network errors the interval backs off: 15 s, 30 s, 1 min, 2 min, 5 min
  at most; it resets after a success.

### Push

- Each user action becomes a queue entry in SQLite: create, change fields, or
  delete.
- Entries for the same reminder coalesce before sending: three priority
  clicks send only the last value.
- Sending one entry: `records/lookup` → apply the changed fields → advance the
  `ResolutionTokenMap` entry for each changed field → `records/modify` with
  the fresh change tag. On `LockConflict`, repeat up to three times.
- Delete is always `Deleted=1`, never a hard delete.

### Conflict rules

| Situation | Result |
|---|---|
| PC and iPhone change different fields | Both kept (per-field tokens) |
| Both change the same field | The newer change wins, as in iCloud |
| An incoming change hits a field with an unsent PC change | The incoming value does not overwrite the screen; the PC change is sent with a newer token |
| iPhone deleted a reminder the PC changed | Deletion wins; the PC change is dropped; a short message says so |
| Title edited on both | The newer full title wins; no text merge |

### Order

"iCloud order" uses the order stored in iCloud if it can be decoded, otherwise
creation date.

## 7. Sign-in, failures and safety

### First run

1. A welcome screen says the app is not made by Apple, uses a private iCloud
   interface, and may stop working when Apple changes it.
2. icloud.com sign-in opens inside the app window. The person types their
   password and two-factor code on Apple's page; the app never sees or stores
   the password. They tick "Keep me signed in".
3. The app takes over the session cookies and trust token and stores them
   encrypted with Windows DPAPI.

One Windows user has one Apple ID.

### Session renewal

When a request fails as `SessionExpired`, the app first renews silently with
the trust token, as icloud.com does in a browser. Only if that fails does the
status dot turn red and a bar say "Sign in again to keep syncing" with a
button. Reminders stay visible and editable; changes wait in the queue.

The app logs every silent renewal and every manual sign-in (no personal data)
to measure open question 5.

### Offline

Cached reminders show, the dot turns orange, changes queue up, and they are
sent without a prompt once the network returns.

### Unknown formats

- A reminder whose fields cannot be decoded shows with a lock icon and is
  read-only.
- A write only sends the fields the user changed, on top of a freshly looked-up
  record, so unknown fields are never overwritten.
- If iCloud returns unknown errors repeatedly, sync stops and a bar says
  "iCloud changed something. Check for an update." with a link to GitHub
  Releases.

### Logs

A rotating log file in the app data folder records sync events, errors and
timings. It never contains reminder titles, notes or list names, so people
can attach it to a public GitHub issue.

### Other

- Single instance: a second launch brings the running window forward.
- Autostart with Windows: a setting, on by default.
- Update check: on start and daily, the app reads the latest GitHub Release and
  offers the update; it downloads nothing without consent.

## 8. Distribution

- Repository: public on GitHub, MIT license.
- README: what it does, the not-affiliated-with-Apple disclaimer, the risk that
  Apple's changes can break it, how to get past the SmartScreen warning for an
  unsigned installer, what data stays on the PC.
- Installer: one `.exe` built with PyInstaller and an installer tool; no
  Python needed on the target PC. Not code-signed in the first version.
  The installer checks for the WebView2 runtime and installs Microsoft's
  Evergreen runtime if it is missing.
- Never committed: `.session/`, any cookie or token file, probe results, the
  Vafle font, real reminder content.

## 9. Testing

1. **Unit tests (pure logic, test-first)**: title codec, token advance for any
   field, queue coalescing, conflict rules, list filter and sort across several
   lists, contrast token choice from luminance.
2. **Fake iCloud**: an in-memory CloudKit stand-in with change tags,
   `OP_LOCK_FAILURE`, "iPhone just changed this record", network loss, expired
   session and unknown format. The sync unit is tested against it, covering
   every row of the conflict table.
3. **Recorded shapes**: response structures captured by the probes with
   titles, notes and names removed, served by the fake so tests match what
   Apple really sends. Safe to commit.
4. **Interface tests**: the HTML runs in Playwright with a fake bridge;
   checks that clicks call the right actions and that filter and sort are
   correct. Animations and glass are checked by eye.
5. **Release checklist on real iCloud**: on a dedicated "Widget test" list,
   add, complete, undo, priority, due date, notes, rename, confirm on iPhone,
   clean up.
6. **GitHub Actions**: tests 1–4 on a Windows runner on every push. Real
   iCloud is never used in CI.
