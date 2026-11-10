# Plan 2a — first real run (Task 8)

Date: 2026-11-10. Run by Martin on his own PC and iPhone. No reminder titles recorded.

## Result

Martin: "Všetko ide jak má" (everything works as it should) after the fixes below.
The app log shows sync runs settling on `status=ok`, local changes pushed (`push: queued=1 settled=1`) and iPhone changes pulled.

## Problems found during the run, all fixed before the result above

| Run | Symptom | Cause | Fix (commit) |
|---|---|---|---|
| 1 | Empty window, red dot, no sign-in visible | Sign-in window opened behind the main window; the empty state said "Nothing here" | Sign-in window on top; "Sign in to iCloud…" prompt with a button (114fd8b) |
| 2 | Signed in, but iCloud refused the session; `session.bin` kept 1 of 18 cookies | WebView2 reports session cookies (incl. `X-APPLE-WEBAUTH-TOKEN`) with a 1970/0001 expiry; they were dropped as expired, iCloud answered 421 and cleared cookies, and the token-less jar was saved | Such expiries mean "session cookie"; a jar without the auth token is never saved (4c1d1c7); keep the last complete snapshot (07e9d57) |
| 3 | Sign-in window signed in but never finished | After a restart the token is gone and icloud.com rebuilds its session with cookies pywebview's `get_cookies()` (page URL only) cannot see | Read cookies for `setup.icloud.com` too; finish when `validate` accepts them (4fe7574) |

## Not covered by this run

- Probe round 4 (duplicate-create code, list colours, stored order, recurring completion, alarm vs due) — still to run: `.\.venv\Scripts\python.exe probes\probe_round4.py`.
- `banner_for` (Martin's contribution) — the top bar stays empty until written.
