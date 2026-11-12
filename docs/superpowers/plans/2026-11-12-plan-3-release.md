# Plan 3 — Release: installer, README, update notice, CI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Anyone on Windows 11 can download one installer from GitHub Releases, install Reminders Widget without Python, get told when a newer version exists, and read what the app does and does not do.

**Architecture:** One source of truth for name, version and repository (`reminders_widget/project.py`). An update checker asks GitHub's "latest release" endpoint at most once a day and only feeds the existing top bar. PyInstaller builds a one-folder app; Inno Setup wraps it into a per-user installer that adds WebView2 when missing. GitHub Actions runs the tests on every push and builds the installer for every `v*` tag.

**Tech Stack:** Python 3.12, PyInstaller 6, Inno Setup 6, GitHub Actions (`windows-latest`), httpx, pytest + Playwright (installed Edge).

**Spec:** `docs/superpowers/specs/2026-11-09-reminders-widget-design.md` (§8 Distribution, §9 Testing 6, §7 Logs).

## Global Constraints

- Display name `Reminders Widget`; UI and README in English; no Apple trademark in the app name.
- Repository: public GitHub, MIT license; `REPO` is filled in once from Martin (`owner/name`) in `project.py` — nothing else hard-codes it.
- The installer is not code-signed; per-user install (`PrivilegesRequired=lowest`), no admin prompt.
- Update check: notify only, never download or run anything; at most one request per 24 h; sends no cookies, no user data; User-Agent `RemindersWidget/<version>`.
- Never committed: `.session/`, cookies or tokens, probe results, the Vafle font, real reminder content (existing `.gitignore` stays and gains `build/`, `dist/`, `packaging/Output/`).
- Logs carry no titles, notes, list names, cookie values or paths (spec §7).
- CI never touches real iCloud.

## Review Focus

1. A PC without the WebView2 runtime → the installer installs it (or says clearly it could not) and the app starts.
2. Update check offline, rate-limited (HTTP 403/429), or with an unexpected body → no bar, no crash, retried the next day.
3. Installing a new version while the old one runs → the installer closes or waits for the running app (same mutex), no locked-file failure.
4. Uninstall → no `RemindersWidget` value left under `HKCU\…\Run` pointing at a deleted exe.
5. A user folder with spaces or non-ASCII letters (`C:\Users\Ľuboš Novák\…`) → autostart command quoted, app and data folder work.

---

### Task 1: Project identity and frozen-aware start

**Files:**
- Create: `src/reminders_widget/project.py`
- Modify: `src/reminders_widget/__init__.py`, `pyproject.toml`, `src/reminders_widget/shell/system.py`, `src/reminders_widget/app/main.py`, `src/reminders_widget/store.py:19` (stale "version 2" comment), `.gitignore`
- Test: `tests/test_project.py`, `tests/shell/test_system.py`, `tests/app/test_main.py`

**Interfaces:**
- Produces: `project.NAME = "Reminders Widget"`, `project.VERSION = "0.1.0"`, `project.REPO = "OWNER/apple-reminders-widget"` (placeholder until Martin sends it), `project.releases_url() -> str` (`https://github.com/{REPO}/releases`); `reminders_widget.__version__ == project.VERSION`; `system.autostart_command(frozen: bool | None = None, executable: str | None = None) -> str`.

- [ ] **Step 1: Tests**
  - `test_version_is_single_sourced`: `reminders_widget.__version__ == project.VERSION` and `pyproject.toml` declares `version` dynamic from `reminders_widget.project.VERSION`.
  - `test_releases_url_uses_repo`: `releases_url() == f"https://github.com/{REPO}/releases"`.
  - `test_autostart_command_frozen_is_the_quoted_exe`: with `exe = r"C:\Users\Ľuboš Novák\AppData\Local\Programs\Reminders Widget\RemindersWidget.exe"`, `autostart_command(frozen=True, executable=exe) == f'"{exe}"'` (Review Focus 5).
  - `test_autostart_command_from_source_keeps_pythonw`: unchanged behaviour for `frozen=False`.
  - `test_enabled_autostart_is_repointed_at_the_installed_exe` (main): with autostart enabled and a stored command that differs from `autostart_command()`, `run` rewrites it once; disabled autostart is left alone.
- [ ] **Step 2: Run, expect FAIL** — `.venv/Scripts/python.exe -m pytest -q tests/test_project.py tests/shell/test_system.py tests/app/test_main.py`
- [ ] **Step 3: Implement.** `pyproject`: `dynamic = ["version"]` + `[tool.setuptools.dynamic] version = {attr = "reminders_widget.project.VERSION"}`; extras `dev = ["pytest>=8", "playwright>=1.45"]`, `build = ["pyinstaller>=6.6"]`. `system.autostart_value()` reads the stored command; `main.run` repoints it when enabled and different. `.gitignore` gains `build/`, `dist/`, `packaging/Output/`.
- [ ] **Step 4: Run, expect PASS**, then the whole suite.
- [ ] **Step 5: Commit** `feat: single source for name, version and repository; autostart follows the installed exe`

### Task 2: Update notice

**Files:**
- Create: `src/reminders_widget/app/updates.py`
- Modify: `src/reminders_widget/app/api.py` (`banner_for`, `_state`, new public `open_update`), `src/reminders_widget/app/main.py` (start the checker), `src/reminders_widget/app/ui/app.js` (banner action), `tests/app/test_api.py::test_internal_hooks_are_not_exposed_to_js` (public set gains `open_update`)
- Test: `tests/app/test_updates.py`, `tests/app/test_api.py`, `tests/app/test_ui.py`

**Interfaces:**
- Consumes: `project.VERSION`, `project.REPO`, `project.releases_url()`; `Store.get_setting/set_setting`.
- Produces:
  - `parse_version(tag: str) -> tuple[int, int, int] | None` (`"v0.2.0"`, `"0.2.0"`; anything else → None).
  - `UpdateChecker(store, fetch: Callable[[str], dict], *, current: str = VERSION, now: Callable[[], float] = time.time, on_found: Callable[[dict | None], None])` with `.check_if_due() -> dict | None`; settings `update_checked_at` (float), `update_latest` (`{"version": str, "url": str}` or None).
  - `banner_for(status, notices, update: dict | None = None)`; update bar `{"text": "Reminders Widget 0.2.0 is available.", "action": "open_update", "tone": "info"}`, lowest priority (after offline).
  - `Api.open_update() -> dict` opens `update["url"]` with `webbrowser.open` only when it starts with `releases_url()`; `Api._update` set by the checker's `on_found`, then `_push(state)`.

- [ ] **Step 1: Tests (`test_updates.py`, fake `fetch` and clock)**
  - `test_parse_version`: `"v1.2.3" → (1, 2, 3)`, `"1.2" → None`, `"v1.2.3-beta" → None`.
  - `test_newer_release_is_reported_with_its_page`: fetch returns `{"tag_name": "v0.2.0", "html_url": f"{releases_url()}/tag/v0.2.0", "draft": False, "prerelease": False}` → `{"version": "0.2.0", "url": …}` and `on_found` called once.
  - `test_same_or_older_release_reports_nothing`.
  - `test_checks_at_most_once_a_day`: two calls 1 h apart → one fetch; 25 h apart → two.
  - `test_failures_are_silent_and_retried_next_day` (Review Focus 2): fetch raising `httpx.ConnectError`, returning `{"message": "API rate limit exceeded"}`, or `[]` → result None, no exception, `update_checked_at` set.
  - `test_url_outside_the_repo_is_ignored`: `html_url` on another host or repo → None.
  - `test_request_sends_no_cookies_and_a_plain_user_agent`: default fetch builds `httpx.Client` with no cookies and header `User-Agent: RemindersWidget/<VERSION>`, timeout 10 s (inspect the request through `httpx.MockTransport`).
  - api: `test_banner_for_update_comes_last` (offline beats update; update alone gives the update bar); `test_open_update_opens_only_the_releases_page` (monkeypatched `webbrowser.open`).
  - ui: `test_update_bar_button_opens_the_release` — banner `{"action": "open_update"}` shows a button labelled `Download` that calls `open_update`; the `sign_in` button keeps calling `sign_in`.
- [ ] **Step 2: Run, expect FAIL** — `.venv/Scripts/python.exe -m pytest -q tests/app/test_updates.py tests/app/test_api.py tests/app/test_ui.py`
- [ ] **Step 3: Implement.** `main.run` starts a daemon thread `rw-updates`: first check 30 s after start, then `check_if_due()` every 6 h; errors logged by type only. The UI labels the action button `Sign in` / `Download` from `banner.action` and calls the method of the same name.
- [ ] **Step 4: Run, expect PASS**, then the whole suite.
- [ ] **Step 5: Commit** `feat: once-a-day update notice from GitHub Releases`

### Task 3: PyInstaller build

**Files:**
- Create: `packaging/launch.py` (`from reminders_widget.app.main import run; run()`), `packaging/make_icon.py`, `packaging/icon.ico` (generated, committed), `packaging/reminders_widget.spec`, `packaging/build.ps1`
- Test: `tests/packaging/test_build_inputs.py`

**Interfaces:**
- Consumes: `tray._icon_image()` (the tray drawing, reused for the app icon); `project.NAME`, `project.VERSION`.
- Produces: `dist/RemindersWidget/RemindersWidget.exe` (one-folder, windowed, icon set); `packaging/build.ps1 [-Installer]` runs the spec and, with `-Installer`, `ISCC` with `/DAppVersion=<VERSION>`.

- [ ] **Step 1: Tests**
  - `test_spec_bundles_every_ui_file`: every file under `src/reminders_widget/app/ui/` appears in the spec's `datas` at `reminders_widget/app/ui`.
  - `test_icon_has_the_windows_sizes`: `icon.ico` contains 16, 24, 32, 48, 256 px frames.
  - `test_spec_is_windowed_one_folder_named_remindersWidget`: `console=False`, `name="RemindersWidget"`, no `onefile`.
- [ ] **Step 2: Run, expect FAIL** — `.venv/Scripts/python.exe -m pytest -q tests/packaging`
- [ ] **Step 3: Implement.** `make_icon.py` writes `icon.ico` from `_icon_image()` at the five sizes. The spec relies on PyInstaller's bundled pywebview hook and adds `hiddenimports=["clr", "webview.platforms.winforms", "webview.platforms.edgechromium"]`.
- [ ] **Step 4: Run, expect PASS.** Then `pip install -e .[build]` and `powershell packaging/build.ps1`; Expected: `dist/RemindersWidget/RemindersWidget.exe` exists; started with a temp `APPDATA`, it shows the sign-in prompt and exits from the tray (smoke, no real iCloud).
- [ ] **Step 5: Commit** `build: one-folder PyInstaller build with the app icon`

### Task 4: Inno Setup installer

**Files:**
- Create: `packaging/installer.iss`
- Test: `tests/packaging/test_installer_script.py`

**Interfaces:**
- Consumes: `system.RUN_VALUE`, the `SingleInstance` mutex name `Local\RemindersWidget.Single`, `dist/RemindersWidget/`.
- Produces: `packaging/Output/RemindersWidget-Setup-<version>.exe`.

- [ ] **Step 1: Tests (static checks of the script, so names cannot drift)**
  - `test_installer_closes_the_running_app` (Review Focus 3): `AppMutex=` equals the `SingleInstance` default name and `CloseApplications=yes`.
  - `test_uninstall_removes_the_autostart_value` (Review Focus 4): an `[UninstallRun]`/`[Registry]` entry deletes `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value `RemindersWidget` (`uninsdeletevalue`).
  - `test_installer_adds_webview2_when_missing` (Review Focus 1): `[Code]` checks `pv` under the WebView2 client key `{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}` in HKLM (WOW6432Node) and HKCU, downloads `https://go.microsoft.com/fwlink/p/?LinkId=2124703` with a download page and runs it with `/silent /install`; a failure shows a message with the WebView2 download link instead of aborting silently.
  - `test_per_user_install_with_start_menu_entry`: `PrivilegesRequired=lowest`, `DefaultDirName={localappdata}\Programs\Reminders Widget`, a `{group}` shortcut, `AppVersion={#AppVersion}`, output name as above.
- [ ] **Step 2: Run, expect FAIL** — `.venv/Scripts/python.exe -m pytest -q tests/packaging`
- [ ] **Step 3: Implement** the script: welcome → folder → install → "Launch Reminders Widget" checkbox. Autostart stays the app's job (on at first run, tray toggle, Task 1 repoints it at the exe); the installer only removes the Run value on uninstall. User data in `%APPDATA%\RemindersWidget` is left on uninstall; the final uninstall page says where it is.
- [ ] **Step 4: Run, expect PASS.** With Inno Setup installed (`winget install JRSoftware.InnoSetup` — Martin's choice to install locally; otherwise CI builds it): `powershell packaging/build.ps1 -Installer`; Expected: `packaging/Output/RemindersWidget-Setup-0.1.0.exe`.
- [ ] **Step 5: Commit** `build: per-user Inno Setup installer with WebView2 bootstrap`

### Task 5: README, license, contributing notes

**Files:**
- Create: `README.md`, `LICENSE` (MIT, `Copyright (c) 2026 Martin Cimbala`), `docs/privacy.md`
- Test: `tests/test_docs.py`

- [ ] **Step 1: Tests**
  - `test_readme_has_the_required_sections`: headings `What it does`, `Not affiliated with Apple`, `It can break`, `Install` (incl. "More info → Run anyway" for SmartScreen), `What stays on your PC`, `Build from source`, `License`.
  - `test_readme_links_use_the_project_repo`: every GitHub link contains `project.REPO`.
  - `test_license_is_mit`.
- [ ] **Step 2: Run, expect FAIL** — `.venv/Scripts/python.exe -m pytest -q tests/test_docs.py`
- [ ] **Step 3: Write.** Content from spec §1, §7, §8: unofficial iCloud web API, sign-in happens in Apple's own page, cookies encrypted with Windows DPAPI in `%APPDATA%\RemindersWidget`, no telemetry, the update check is one GitHub request a day, logs without reminder content, how to remove everything (uninstall + delete the data folder), known limits (repeating reminders and alerts on iPhone, monitors with different scales). `docs/privacy.md` holds the long version; the README links it.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `docs: README, MIT license and privacy notes`

### Task 6: GitHub Actions

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/workflows/release.yml`, `packaging/check_tag.py`
- Test: `tests/packaging/test_check_tag.py`

**Interfaces:**
- Produces: `check_tag.py <tag>` exits 0 when `tag == "v" + project.VERSION`, else 1 with a message.

- [ ] **Step 1: Tests** — `test_tag_must_match_the_version` (`v0.1.0` ok, `v0.1.1` and `0.1.0` fail).
- [ ] **Step 2: Run, expect FAIL** — `.venv/Scripts/python.exe -m pytest -q tests/packaging/test_check_tag.py`
- [ ] **Step 3: Implement.**
  - `ci.yml`: on push and pull request; `windows-latest`; Python 3.12; `pip install -e .[dev,probe]`; `pytest -q` (the UI tests use the runner's Edge).
  - `release.yml`: on tags `v*`; `check_tag.py`; tests; `pip install -e .[build]`; `make_icon` not needed (icon committed); `build.ps1 -Installer` (Inno Setup is preinstalled on `windows-latest`; otherwise `choco install innosetup -y`); `gh release create <tag> packaging/Output/*.exe --generate-notes` plus a `SHA256SUMS.txt`; permissions `contents: write` only.
- [ ] **Step 4: Run, expect PASS**, then the whole suite. CI itself runs once Martin has pushed the repository.
- [ ] **Step 5: Commit** `ci: tests on every push, installer on every version tag`

### Task 7: First release run (Martin)

- [ ] Martin creates the public repository and sends `owner/name`; the executor sets `project.REPO`, commits, adds the remote. Martin pushes (`git push -u origin main`) — or tells the executor to.
- [ ] CI is green on GitHub.
- [ ] Tag `v0.1.0` → the release workflow publishes `RemindersWidget-Setup-0.1.0.exe`.
- [ ] Martin installs it (SmartScreen: More info → Run anyway), signs in, uses the widget, restarts the PC (starts once, from the installed exe), uninstalls (the Run value is gone, the data folder is kept).
- [ ] The executor records the result in `docs/superpowers/plans/2026-11-12-plan-3-checklist.md` (no reminder titles).
