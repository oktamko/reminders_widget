"""Static checks of packaging/installer.iss, so names shared with the app cannot drift."""
import inspect
import re
from pathlib import Path

from reminders_widget.app.paths import APP_FOLDER
from reminders_widget.shell import system

ISS = (Path(__file__).resolve().parents[2] / "packaging" / "installer.iss").read_text(encoding="utf-8")
WEBVIEW2 = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def setting(name):
    m = re.search(rf"^{name}=(.*)$", ISS, re.M)
    return m.group(1).strip() if m else None


def test_installer_closes_the_running_app():   # plan 3 Review Focus 3
    mutex = inspect.signature(system.SingleInstance.__init__).parameters["name"].default
    assert setting("AppMutex") == mutex
    assert setting("CloseApplications") == "yes"


def test_uninstall_removes_the_autostart_value():   # plan 3 Review Focus 4
    line = next(l for l in ISS.splitlines()
                if l.startswith("Root: HKCU") and "CurrentVersion\\Run" in l and "Tasks:" not in l)   # always, task or not
    assert f'ValueName: "{system.RUN_VALUE}"' in line and "uninsdeletevalue" in line and "ValueType: none" in line


def test_installer_adds_webview2_when_missing():   # plan 3 Review Focus 1
    code = ISS[ISS.index("[Code]"):]
    assert WEBVIEW2 in code and "'pv'" in code
    assert "HKLM" in code and "WOW6432Node" in code and "HKCU" in code
    assert "https://go.microsoft.com/fwlink/p/?LinkId=2124703" in code
    assert "CreateDownloadPage" in code and "/silent /install" in code
    assert "https://developer.microsoft.com/microsoft-edge/webview2/" in code   # what to do if it fails


def test_per_user_install_with_start_menu_entry():
    assert setting("PrivilegesRequired") == "lowest"
    assert setting("DefaultDirName") == r"{localappdata}\Programs\Reminders Widget"
    assert setting("AppName") == "Reminders Widget" and setting("AppVersion") == "{#AppVersion}"
    assert setting("OutputBaseFilename") == "RemindersWidget-Setup-{#AppVersion}"
    assert 'Name: "{group}\\Reminders Widget"; Filename: "{app}\\RemindersWidget.exe"' in ISS


def test_uninstall_says_where_the_data_stays():
    assert APP_FOLDER in ISS[ISS.index("[Code]"):]


def test_running_app_message_says_how_to_quit_it():   # final review 4: closing the window only hides it
    for key in ("SetupAppRunningError", "UninstallAppRunningError"):
        line = next((l for l in ISS.splitlines() if l.startswith(key + "=")), "")
        assert "tray" in line and "Quit" in line


def test_upgrade_removes_the_old_program_files_first():   # final review 8
    section = ISS[ISS.index("[InstallDelete]"):]
    assert r'Type: filesandordirs; Name: "{app}\_internal"' in section.splitlines()[1]



def test_installer_offers_autostart_as_a_task():   # plan 3b Task 1
    tasks = ISS[ISS.index("[Tasks]"):].splitlines()[1]
    assert tasks.startswith('Name: "autostart"') and "Start Reminders Widget when Windows starts" in tasks
    assert "unchecked" not in tasks                                    # on by default, but asked
    line = next(l for l in ISS.splitlines() if "Tasks: autostart" in l)
    assert 'Root: HKCU; Subkey: "Software\\Microsoft\\Windows\\CurrentVersion\\Run"' in line
    assert 'ValueName: "RemindersWidget"' in line and 'ValueData: """{app}\\RemindersWidget.exe"""' in line
    assert "uninsdeletevalue" in line



def test_installer_shows_the_privacy_statement_with_an_update_switch():   # plan 3b Task 2
    code = ISS[ISS.index("[Code]"):]
    assert "CreateInputOptionPage" in code
    assert "Check for updates once a day (one request to GitHub, no personal data)" in code
    assert "will not transfer any information to other networked systems" in code    # privacy statement
    assert "RegWriteDWordValue(HKCU, 'Software\\Reminders Widget', 'CheckForUpdates'" in code
    line = next(l for l in ISS.splitlines() if 'Subkey: "Software\\Reminders Widget"' in l)
    assert "uninsdeletekey" in line



def test_unticked_autostart_removes_an_existing_entry():   # plan 3b final review 1
    line = next(l for l in ISS.splitlines() if "Tasks: not autostart" in l)
    assert 'ValueName: "RemindersWidget"' in line and "deletevalue" in line and "CurrentVersion\\Run" in line


def test_upgrade_offers_autostart_as_it_is_now():   # final review 2
    assert setting("UsePreviousTasks") == "no"
    code = ISS[ISS.index("[Code]"):]
    assert "WizardSelectTasks('!autostart')" in code
    assert "RegValueExists(HKCU, 'Software\\Microsoft\\Windows\\CurrentVersion\\Run', 'RemindersWidget')" in code
    assert "_is1" in code                                    # only on an upgrade (previous install present)


def test_privacy_page_starts_from_the_current_choice():   # final review 3
    code = ISS[ISS.index("[Code]"):]
    assert "RegQueryDWordValue(HKCU, 'Software\\Reminders Widget', 'CheckForUpdates'" in code


def test_privacy_text_names_every_transfer():   # final review (re-graded): SignPath checks this text
    code = ISS[ISS.index("[Code]"):]
    for part in ("sign-in page", "Microsoft", "GitHub"):
        assert part in code
