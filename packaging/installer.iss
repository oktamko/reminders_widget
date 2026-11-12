; Reminders Widget installer (Inno Setup 6). Per-user, no admin prompt.
; Build: powershell packaging/build.ps1 -Installer   (passes /DAppVersion=<project.VERSION>)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6B4E2C1A-93D7-4F0B-A8E5-2D7C9F31B4A6}
AppName=Reminders Widget
AppVersion={#AppVersion}
AppVerName=Reminders Widget {#AppVersion}
AppPublisher=Martin Cimbala
DefaultDirName={localappdata}\Programs\Reminders Widget
DefaultGroupName=Reminders Widget
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=RemindersWidget-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\RemindersWidget.exe
UninstallDisplayName=Reminders Widget
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; the app's single-instance mutex: setup and uninstall wait until it is closed
AppMutex=Local\RemindersWidget.Single
CloseApplications=yes

[Messages]
SetupAppRunningError=Reminders Widget is running. Right-click its tray icon and choose Quit, then click OK. (Closing the widget only hides it.)
UninstallAppRunningError=Reminders Widget is running. Right-click its tray icon and choose Quit, then click OK. (Closing the widget only hides it.)

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\RemindersWidget\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Reminders Widget"; Filename: "{app}\RemindersWidget.exe"

[Registry]
; autostart is the app's own setting; uninstall removes the entry so nothing points at a deleted exe
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "RemindersWidget"; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\RemindersWidget.exe"; Description: "Launch Reminders Widget"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebView2Client = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Bootstrapper = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';
  WebView2Failed = 'The Microsoft Edge WebView2 Runtime could not be installed. ' +
    'Reminders Widget needs it to show its window.' + #13#10#13#10 +
    'Install it from https://developer.microsoft.com/microsoft-edge/webview2/ and then start Reminders Widget again.';

var
  DownloadPage: TDownloadWizardPage;

function HasVersion(Root: Integer; Key: String): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0');
end;

function WebView2Installed: Boolean;
begin
  Result := HasVersion(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WebView2Client)
    or HasVersion(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WebView2Client)
    or HasVersion(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WebView2Client);
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage(SetupMessage(msgWizardPreparing), SetupMessage(msgPreparingDesc), nil);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if (CurPageID = wpReady) and not WebView2Installed then
  begin
    DownloadPage.Clear;
    DownloadPage.Add(WebView2Bootstrapper, 'MicrosoftEdgeWebview2Setup.exe', '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
        if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '',
                    SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
          MsgBox(WebView2Failed, mbError, MB_OK);
      except
        MsgBox(WebView2Failed, mbError, MB_OK);
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('Reminders Widget was removed. Your local data (the reminder cache and the encrypted ' +
           'iCloud sign-in) stays in ' + ExpandConstant('{localappdata}') + '\RemindersWidget' +
           ' — delete that folder to remove it too.', mbInformation, MB_OK);
end;
