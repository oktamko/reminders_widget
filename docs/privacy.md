# Privacy

Reminders Widget runs entirely on your PC. There is no server, no account with the project and no telemetry.

## Who the app talks to

- **Apple (icloud.com, setup.icloud.com and iCloud's CloudKit web service)** — to sign you in through Apple's own page and to read and write your reminders. This is the same traffic your browser sends when you use icloud.com.
- **GitHub (api.github.com)** — at most once a day, one request for the newest release of this project, so the app can tell you about an update. It sends no cookies and no information about you or your reminders; GitHub sees your IP address, as with any web request. Nothing is downloaded or installed automatically.

Nothing else.

## What is stored, and where

All in `%LOCALAPPDATA%\RemindersWidget`:

- `reminders.db` — your lists and open reminders (titles, notes, due dates, priorities) and changes waiting to be sent, so the widget opens instantly and works offline.
- `session.bin` — iCloud session cookies, encrypted with the Windows Data Protection API for your Windows user. Another user of the PC, or a copy of the file on another PC, cannot read them. Your Apple ID password is never handled by the app: you type it into Apple's page, and the app keeps only the session Apple gives back.
- `webview/` — data of the embedded browser used for signing in.
- `logs/app.log` (rotating, a few files) — sync events, error types and timings. Logs never contain reminder titles, notes, list names, cookie values or file paths, so they are safe to attach to a public issue.

Settings (selected lists, sort, window position, mode) are stored in `reminders.db`. If autostart is on, Windows keeps one entry named `RemindersWidget` under `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`.

## Removing everything

1. Uninstall Reminders Widget in Windows Settings → Apps (this also removes the autostart entry).
2. Delete the folder `%LOCALAPPDATA%\RemindersWidget`.
3. Optionally, sign out of the session in your Apple ID settings (Devices / sign-in sessions).
