# Reminders Widget

Your Apple Reminders on the Windows 11 desktop — read, add and change them on the PC, and see the change on your iPhone within about half a minute.

## What it does

- Shows your reminders as a frosted-glass widget on the desktop (it stays under your windows and survives `Win+D`), or as a normal window. A tray icon and `Ctrl+Alt+R` bring it up.
- Syncs both ways with iCloud: add a reminder, complete it, rename it, change its priority, notes or due date on the PC, and the same change appears on your iPhone, iPad and Mac — and theirs appear here.
- Shows one list or several merged, sorted by priority or in iCloud's order. Completed reminders are not shown.
- Opens instantly from a local copy, works offline and sends your changes when you are back online.
- No notifications on the PC: your iPhone already reminds you.

Repeating reminders are completed on the iPhone (the PC would end the series), and moving a due date on the PC leaves an alert at its old time; the app tells you so when it happens.

## Not affiliated with Apple

Reminders Widget is an independent open-source project. It is not made, endorsed or supported by Apple. Apple, iCloud, iPhone and Apple Reminders are trademarks of Apple Inc.

## It can break

Apple offers no public interface for Reminders. This app talks to the same private web interface that icloud.com uses. When Apple changes it, the app may stop syncing until it is updated — it then stops writing and says "iCloud changed something. Check for an update." rather than risk damaging your reminders. The app can check once a day whether a newer version exists (optional) and shows it in the top bar.

## Install

1. Download `RemindersWidget-Setup-<version>.exe` from [Releases](https://github.com/oktamko/reminders_widget/releases).
2. Run it. Release 0.1.0 is not code-signed, so Windows SmartScreen may say "Windows protected your PC": click **More info**, then **Run anyway**. On PCs with **Smart App Control** turned on, Windows blocks unsigned programs completely and there is no way past it; signed releases (see [Code signing policy](#code-signing-policy)) fix this. Until then, run it from source (below).
3. It installs for your user only (no administrator rights) into `%LOCALAPPDATA%\Programs\Reminders Widget`. If the Microsoft Edge WebView2 Runtime is missing, the installer downloads and installs it.
4. Start Reminders Widget and sign in with your Apple ID in Apple's own sign-in page. Two-factor codes work as on icloud.com.

The installer asks whether to start Reminders Widget with Windows and whether to check for updates once a day; both can be changed later in the tray menu (**Start with Windows**, **Check for updates**). To remove it, uninstall it from Windows Settings → Apps.

Requirements: Windows 11 (64-bit) and an Apple ID with iCloud Reminders turned on.

## What stays on your PC

Everything the app keeps is in `%LOCALAPPDATA%\RemindersWidget`:

| File | What it is |
|---|---|
| `reminders.db` | A local copy of your lists and reminders, and changes waiting to be sent |
| `session.bin` | Your iCloud session cookies, encrypted with Windows (DPAPI) for your Windows account only |
| `webview/` | The sign-in page's browser data |
| `logs/` | A rotating log of sync events and errors — never reminder titles, notes or list names, so you can attach it to an issue |

Your password is never seen or stored by the app: you type it into Apple's page. There is no server, no account with us and no telemetry. The only request that does not go to Apple is one call a day to GitHub to look for a newer release; it carries no data about you. Uninstalling leaves this folder in place; delete it to remove everything. More detail: [docs/privacy.md](docs/privacy.md).

## Build from source

Needs Python 3.12 and Windows 11.

```powershell
git clone https://github.com/oktamko/reminders_widget.git
cd reminders_widget
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m reminders_widget
```

To build the app folder and the installer: `pip install -e ".[build]"`, install [Inno Setup 6](https://jrsoftware.org/isinfo.php) and run `powershell -ExecutionPolicy Bypass -File packaging/build.ps1 -Installer`. Pushing a tag `v<version>` builds and publishes the installer through GitHub Actions.

## Code signing policy

Free code signing provided by SignPath.io, certificate by SignPath Foundation.

Signed releases are being set up: the app and its installer are built from this repository by GitHub Actions and signed in that same run; nothing built elsewhere is signed.

| Role | Members |
|---|---|
| Author (commits without extra review) | oktamko |
| Reviewer (reviews every outside contribution) | oktamko |
| Approver (approves each signing request by hand) | oktamko |

Privacy: this program will not transfer any information to other networked systems unless specifically requested by the user. It talks to Apple's iCloud — the sign-in page you open, then your reminders — and makes one optional request a day to GitHub to look for a newer version (off in the installer or the tray menu). The installer downloads the Microsoft Edge WebView2 Runtime from Microsoft only if it is missing. Details: [docs/privacy.md](docs/privacy.md).

## License

[MIT](LICENSE).
