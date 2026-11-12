# Plan 3b — Free code signing through SignPath Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The installed Reminders Widget runs on PCs with Windows Smart App Control on, because the app and its installer carry a free, legitimate Authenticode signature from SignPath Foundation, and the project meets every condition SignPath Foundation sets.

**Architecture:** First make the app and installer meet SignPath Foundation's conditions (announced system changes, a privacy statement shown during install with an off switch for the update check, product metadata in the exe, a "Code signing policy" section). Then split the release workflow so the inner `RemindersWidget.exe` is signed before Inno Setup packs it, and the installer is signed after; each signing request waits for Martin's manual approval in SignPath. The signing steps stay inactive until the SignPath organisation and project exist.

**Tech Stack:** as plan 3, plus SignPath.io (`signpath/github-action-submit-signing-request`), `actions/upload-artifact`, PyInstaller `VSVersionInfo`, Inno Setup `[Tasks]` and a wizard page.

**Spec:** `docs/superpowers/specs/2026-11-09-reminders-widget-design.md` (§8 Distribution — this plan adds code signing, which §8 deferred). Background: `docs/superpowers/plans/2026-11-12-plan-3-checklist.md` (Smart App Control blocked v0.1.0).

## Why

v0.1.0 installs but does not start on Martin's PC: Smart App Control blocks the unsigned `RemindersWidget.exe` (`CreateProcess failed; code 4551`). Smart App Control cannot be passed like SmartScreen, and it is on for many clean Windows 11 installs. Martin's decision: free, safe and legitimate — SignPath Foundation (checked 2026-11-12 at signpath.org/terms).

## SignPath Foundation conditions (as read on 2026-11-12) and where each is met

| Condition | Where |
|---|---|
| OSI license, no proprietary code, no dual licensing | MIT (plan 3) — already met |
| Actively maintained, already released in the form to be signed, functionality documented on the download page | v0.1.0 + README (plan 3) — already met |
| Built from source in CI, signing automated, manual approval per release | Task 5 |
| Product name and version set in the binaries' metadata, the same in every build | Task 3 |
| No privacy compromise; data transfers to systems the user did not ask for need a privacy policy, **shown during install**, and **an option to disable** them | Task 2 (update check) |
| Announce system configuration changes | Task 1 (autostart) |
| Uninstallation for installed software | installer (plan 3) — already met |
| "Code signing policy" section on the home page and the download/release pages, with the exact sentence, team roles and a privacy policy | Task 4 |
| Multi-factor authentication for SignPath and repository access for all members | Martin (Task 6) |

Certificate subject: **SignPath Foundation** is the publisher, not Martin. Acceptance is at their discretion.

## Global Constraints

- Everything from plan 3 stays (per-user install, notify-only update check, English UI, nothing secret committed, logs without content).
- Required sentence, verbatim: `Free code signing provided by SignPath.io, certificate by SignPath Foundation.`
- Team roles: Martin (`oktamko`) is author, reviewer and approver.
- No SignPath token or organisation secret in the repository; they live in GitHub repository secrets/variables.
- The release still works without SignPath configured (unsigned, as v0.1.0), so nothing blocks releases while the application is pending.

## Review Focus

1. Upgrading from v0.1.0 (autostart on, written by the source run or by nobody) → autostart follows the installer's choice, no silent change.
2. Update check turned off in the installer → no request ever goes to GitHub, and the tray shows it off; turned back on in the tray → it works.
3. Release workflow with SignPath not configured → still publishes an unsigned installer; configured → nothing unsigned is published.
4. A signing request rejected or timed out in SignPath → the release fails, nothing is published.
5. A signed install on a PC with Smart App Control on → setup, app and uninstaller all start.

---

### Task 1: Autostart announced by the installer

**Files:** Modify `src/reminders_widget/app/main.py` (`sync_autostart`), `packaging/installer.iss`; Test `tests/app/test_main.py`, `tests/packaging/test_installer_script.py`

- [ ] Tests:
  - `test_installed_copy_never_turns_autostart_on_by_itself`: `frozen=True`, first start → no write; `autostart_initialised` set.
  - `test_source_first_start_still_turns_autostart_on` (developer runs keep today's behaviour).
  - `test_installer_offers_autostart_as_a_task`: `[Tasks]` entry `autostart` with description `Start Reminders Widget when Windows starts`, checked by default; a `[Registry]` line with `Tasks: autostart` writes `RemindersWidget` = `"{app}\RemindersWidget.exe"` (quoted) and keeps `uninsdeletevalue`.
- [ ] Implement; installed copy keeps repointing an existing entry (plan 3 behaviour).
- [ ] Commit `feat: the installer asks about autostart; the installed app never turns it on silently`

### Task 2: Update check shown at install and switchable

**Files:** Create nothing new beyond tests; Modify `src/reminders_widget/app/updates.py`, `src/reminders_widget/shell/tray.py` (`menu_items`), `src/reminders_widget/app/main.py`, `packaging/installer.iss`, `docs/privacy.md`; Test `tests/app/test_updates.py`, `tests/shell/test_tray.py`, `tests/packaging/test_installer_script.py`

**Interfaces:**
- `updates.checks_enabled(store, read_installer_choice=…) -> bool`: the store setting `update_checks` if set; else the installer's choice `HKCU\Software\Reminders Widget\CheckForUpdates` (DWORD 0/1); else True.
- `UpdateChecker(…, enabled: Callable[[], bool])` — `check_if_due()` returns None without a request when disabled.
- Tray item `Check for updates` (checkbox) between `Start with Windows` and `Quit`, writing the store setting.

- [ ] Tests:
  - `test_disabled_checks_never_ask_github` (Review Focus 2).
  - `test_installer_choice_is_the_default_until_the_tray_changes_it`.
  - `test_tray_has_a_check_for_updates_toggle`.
  - `test_installer_shows_the_privacy_statement_with_an_update_switch`: a wizard page (before installing) with the privacy text and a checkbox `Check for updates once a day (one request to GitHub, no personal data)`, checked by default, written to the registry value above; uninstall removes the key.
- [ ] Implement; privacy.md says how to turn the check off.
- [ ] Commit `feat: the update check is shown during install and can be turned off`

### Task 3: Product name and version in the exe

**Files:** Create `packaging/version_info.py` (`write_version_file(path) -> None` from `project.NAME/VERSION`); Modify `packaging/reminders_widget.spec`, `packaging/build.ps1`; Test `tests/packaging/test_build_inputs.py`

- [ ] Tests: `test_version_resource_matches_the_project` — the generated file has `ProductName` = `Reminders Widget`, `FileDescription` = `Reminders Widget`, `ProductVersion`/`FileVersion` = `VERSION` (+ `.0`), `OriginalFilename` = `RemindersWidget.exe`; the spec passes `version=`. Inno: `VersionInfoProductName=Reminders Widget`, `VersionInfoVersion={#AppVersion}`.
- [ ] Implement; verify locally: the built exe's Properties → Details show the name and version.
- [ ] Commit `build: product name and version in the exe and installer metadata`

### Task 4: Code signing policy and Smart App Control note

**Files:** Modify `README.md`, `docs/privacy.md`, `.github/workflows/release.yml` (release notes); Create `.github/release-notes.md`; Test `tests/test_docs.py`

- [ ] Tests:
  - `test_readme_has_a_code_signing_policy`: a `## Code signing policy` section containing the exact sentence, the roles (author, reviewer, approver: `oktamko`), and a link to `docs/privacy.md`.
  - `test_release_notes_carry_the_policy`: `.github/release-notes.md` has the same sentence and links; `gh release create` uses `--notes-file .github/release-notes.md` (plus generated notes).
  - `test_readme_explains_smart_app_control`: the Install section says unsigned builds are blocked when Smart App Control is on and that signed releases fix it.
- [ ] Implement.
- [ ] Commit `docs: code signing policy and Smart App Control note`

### Task 5: Signing in the release workflow

**Files:** Modify `.github/workflows/release.yml`, `packaging/build.ps1` (separate `-AppOnly` / `-InstallerOnly` steps); Test `tests/packaging/test_check_tag.py` (workflow checks)

Flow: test → build app folder → if SignPath is configured: upload `dist/RemindersWidget/RemindersWidget.exe` as an artifact → `signpath/github-action-submit-signing-request` (policy `release-signing`, `wait-for-completion: true`, output to the same path) → Inno compile → upload the installer → sign it the same way → checksums → publish.

- [ ] Tests:
  - `test_release_signs_app_before_packing_and_installer_after` — order of steps.
  - `test_signing_runs_only_when_configured` — signing steps have `if: vars.SIGNPATH_ORGANIZATION_ID != ''`; the token comes from `secrets.SIGNPATH_API_TOKEN` only (Review Focus 3).
  - `test_signing_failure_stops_the_release` — no `continue-on-error` on signing steps (Review Focus 4).
- [ ] Implement. Values to fill in after approval: repository variable `SIGNPATH_ORGANIZATION_ID`, project slug `reminders_widget`, signing policy `release-signing`, artifact configuration (sign the PE file; for the installer also the Inno uninstaller — see Open questions), secret `SIGNPATH_API_TOKEN`.
- [ ] Commit `ci: sign the app and the installer through SignPath when configured`

### Task 6: Apply and first signed release (Martin)

- [ ] Martin turns on two-factor authentication on GitHub (and later SignPath).
- [ ] Martin applies at https://signpath.org/apply with the repository link (after Tasks 1–5 are pushed, so the project already meets the conditions).
- [ ] After approval: Martin creates the SignPath project/policy as they instruct and adds the GitHub secret and variable; the executor fills in the remaining IDs.
- [ ] Release `v0.1.1`; Martin approves both signing requests in SignPath.
- [ ] Martin installs v0.1.1 with Smart App Control on: setup, app, autostart after restart and uninstall all work (Review Focus 5). Result recorded in `docs/superpowers/plans/2026-11-12-plan-3b-checklist.md`.

## Open questions (decide during Task 5, from SignPath's documentation)

- **Inno uninstaller:** Inno writes `unins000.exe` at install time; signing it needs `SignTool`/`SignedUninstaller` at compile time. Options: SignPath signs a pre-built uninstaller (`SignedUninstaller=yes` + `SignedUninstallerDir`), or SignPath's artifact configuration signs it inside the installer. Pick what SignPath supports; if neither, verify whether Smart App Control lets the unsigned uninstaller run.
- **Third-party DLLs** (Pillow, pythonnet) stay unsigned, as SignPath allows for upstream libraries. Expected to pass Smart App Control by reputation (the same files run today from the source checkout); confirmed in Task 6.
