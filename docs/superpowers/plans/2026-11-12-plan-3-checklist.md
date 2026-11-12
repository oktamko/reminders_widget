# Plan 3 — first release run (Task 7)

Date: 2026-11-12. Run by Martin on his own PC. No reminder titles recorded.

## Result

| Step | Result |
|---|---|
| Repository | `oktamko/reminders_widget` (public); `project.REPO` and README set; `main` pushed |
| CI on GitHub | Green on the first run: 675 passed, nothing skipped (UI tests ran in the runner's Edge) |
| Release `v0.1.0` | Published by the release workflow in 2 min 14 s: `RemindersWidget-Setup-0.1.0.exe` (19.9 MB) and `SHA256SUMS.txt`; `installer.iss` compiled on the first try |
| Install | The installer ran per-user without an admin prompt and copied the app |
| Launch | **Blocked by Windows Smart App Control**: "CreateProcess failed; code 4551. An Application Control policy has blocked this file." |

## Cause

Smart App Control is on (`VerifiedAndReputablePolicyState = 1`). The Code Integrity log records `RemindersWidget.exe` as not meeting the signing level (events 3033/3077, policy `{0283ac0f-fff1-49ae-ada1-8a933130cad6}`). The exe is unsigned and new, so it has no reputation. The source run keeps working: `python.exe` is signed and the third-party DLLs it loads are widely known files.

The spec (§8) planned for SmartScreen ("More info → Run anyway"), which can be passed; Smart App Control cannot, and it is on for many clean Windows 11 installs. Decision (Martin): free, legitimate code signing through SignPath Foundation — plan 3b (`2026-11-12-plan-3b-code-signing.md`).

## Left as is for now

- The installed copy stays installed but never ran; autostart still points at the source run.
- Uninstalling was not tried: the Inno uninstaller is unsigned too, and it is not known whether Smart App Control lets it run.
- Martin keeps using the source run until a signed release exists.
