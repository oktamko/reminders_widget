# Plan 2b — first desktop run (Task 8)

Date: 2026-11-10. Run by Martin on his own PC (two monitors, 125 %). No reminder titles recorded.

## Result

Martin: everything matched the checklist (widget on the wallpaper, aligned glass, readable text, clicks,
detail panel, right-click, tray mode switch, Ctrl+Alt+R, one instance) after the fixes below.

## Problems found during the run, all fixed before the result above

| Run | Symptom | Cause | Fix (commit) |
|---|---|---|---|
| 1 | The widget could not be dragged | Only the list title and the sort label were drag regions | Drag from any spot that is not a control (ca140c7) |
| 1 | `Win+D` hid the widget | Not a minimize: the shell lifts the desktop (`Progman`) over the widget just after the foreground event, then a taskbar XAML window (`TopLevelWindowForOverflowXamlIsland`) took the foreground and counted as another app, sending the widget to the bottom | Taskbar XAML windows count as taskbar; no minimize box; restore on minimize (ca140c7, c15e119) |
| 2 | `Win+D` hid the widget only sometimes | Two late raises at fixed times lost the race with the shell now and then | While the desktop or taskbar is in front, check every 0.2 s whether the desktop covers the widget and raise it (3012b73); measured 0.24 ms per check, worst case |

Verification for the last fix: 5 real `Win+D` rounds with another app in front, 4 samples each (0.3–4 s): the widget was on top in all 20.

## Fixed before the run (final review)

Windows restart no longer vetoed by the widget; glass tint drawn over the wallpaper with text tone from the area under the widget; no black frame; widget position clamped in pywebview's logical px; no taskbar button; a click keeps the widget under other windows; a widget closed to the tray stays hidden; a half-written wallpaper is retried; no pulse under reduced motion (0d9ed57..1db198b).

## Not covered by this run

- Unplugging the second monitor.
- Probe round 4 (duplicate-create code, list colours, stored order, recurring completion, alarm vs due): `.\.venv\Scripts\python.exe probes\probe_round4.py`.
- `banner_for` (Martin's contribution) — the top bar stays empty until written.
