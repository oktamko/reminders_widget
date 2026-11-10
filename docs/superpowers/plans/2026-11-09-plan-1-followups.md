# Plan 1 follow-ups

Deferred items from the plan 1 reviews (probes and sync core), carried into plan 2. Each line: task, then the item.

## Must handle in plan 2

- (plan 2a) Run probe round 4 and apply its answers (plan 2a Task 1 step 3).
- (plan 2a) Martin writes `banner_for`; then notices should accumulate until dismissed instead of being replaced every run.
- (plan 2a deferred) out-of-order state responses (add a state version); poke/now should respect a server `retry_after`; a 'syncing' dot state; hidden detail panel reachable by Tab.

- Confirm Apple's real error code for a duplicate create (EXISTS vs CONFLICT) in the first iCloud probe.
- Expired / invalid sync token recovery (probe what CloudKit returns, then clear the token and resync).
- Read one real list Color value (read-only) to colour rings and dots; Task 7 currently returns color=None.
- Probe what the iPhone does when a recurring reminder is completed; until then the PC refuses it.
- Probe whether a PC due-date change updates the iPhone alarm (AlarmIDs).
- Check whether List.ReminderIDs is the decodable iCloud order (view.py uses creation order).
- Glass: the once-blurred composite moved by `window.screenX/Y` is smooth (probe round 3b: look OK, corners OK, follows drags, smooth), but even the UNBLURRED composite is not aligned, although JS and Win32 window positions agree exactly (80,80 CSS = 100,100 px at 125 %; 2 monitors, both 125 %, fill, 8400x5600 image, virtual 3584x1152 CSS). So the composite itself differs from what Windows draws. Next step: measure automatically instead of by eye — capture the real desktop under the widget (screenshot with the widget hidden) and find the offset/scale of our composite by image matching; then fix the composite maths.
- Win+D: D2 (top-level + foreground hook) is the candidate; make clicks work.
- Store.pending() raises on a corrupted queue row (only a damaged DB); consider tolerant decoding.
- At the create-refusal cap, a SessionExpired/UnknownResponse from the refresh lookup escapes after the drop.

## Deferred minors

- Task 1: minor (deferred): pyproject [build-system]/packages.find and *.egg-info ignore beyond brief — needed for pip install -e
- Task 2: minor (deferred): 4 r["fields"] indexing, 5 unused params/import, 6 cleanup message printed on failure, 7 +24h across DST
- Task 2: minor (deferred): note detection depends on "note" in field/token name; extra FAIL line if create fails before server stored record; unsorted test import
- Task 3: minor (deferred): non-close get_cookies exception now skips the results line (traceback instead); closing window during trust wait loses the WEBAUTH OK line
- Task 6: minor (deferred): advance_tokens docstring omits non-numeric case; NaN/Infinity pass _number; missing counter/modificationTime counts as 0
- Task 4: minor (deferred): BackColor FAIL path continues (later answers untrustworthy if it fires)
- Task 8: minor (deferred): no test for update without recordChangeTag; find_owner caching not proven by test; changes() can loop on moreComing with unchanged token (add no-progress guard); zone-level error untested; fake doesn't validate recordType/zoneID; changes returns str|None token; commit trailer says Sonnet 5.5 (subagent's own attribution)
- Task 5: minor (deferred): TypeError on non-str b64 (API misuse); surrogate check runs before no-change short-circuit
- Task 4b-desktop: minor (deferred): D1 setup before try not covered by unparent; D2 pump ready-wait has no timeout; D2 click makes widget foreground (may skew "under other windows"); sub-pixel resample
- Task 7: minor (deferred): list Color format unknown (fixture scrubbed), color=None; TimeZone field ignored for timed due dates
- Task 9: minor (deferred): update after an in-flight delete creates a stray update entry (engine must drop on RecordGone); begin_send re-marks an already in-flight id silently; no schema migration for in_flight (no DB shipped yet)
- Task 10: minor (deferred): expired sync token recovery (plan 2 probe); CONFLICT fallback may overwrite a phone edit in the lost-response window
- Task 10: minor (deferred): a create refused every run blocks the push queue silently (no notice/attempt cap); transient sqlite errors count toward "stopped"; offline/session_expired reset the error counter; null List field → list_id "" not readonly
- Task 11: minor (deferred): title_for returns "" for empty/unknown selection (callers must ensure_selection first); missing edge tests (unexpected priority, empty selection, no-mutation); else-branch treats any sort as icloud
- Task 4b-desktop: minor (deferred): DPI read once (mixed-DPI drift); rAF writes transform every frame; unused send_glass param; JS translate formula duplicated in Python mirror; need screenX fallback if stale during drag
- Task 7: minor (deferred): redundant deepcopy in check_editable; tests import tests.test_crdt as package
- Task 4b-desktop: minor (deferred): Pillow unpinned; find_workerw takes first WorkerW child on 24H2
- Task 4b-icloud: minor (deferred): extra cleanup FAIL when create fails; flow continues after a 120 s phone timeout; text_edit_fields could refuse fresh TitleDocument; non-UnknownFormat exception text via str(exc); unused import json, duplicate Hotovo line
