; Inno Setup script for Gesit.  Build:  iscc /DAppVersion=1.1.2 packaging\windows\gesit.iss
; Expects (created by build_windows.ps1):
;   build\windows\dist\Gesit\      PyInstaller output (gesit.exe, gesit-native-host.exe, _internal)
;   extension\dist\{chrome,firefox}  browser extensions
;   build\windows\tools\           ffmpeg.exe and yt-dlp.exe (optional)
#ifndef AppVersion
  #define AppVersion "1.1.2"
#endif
#define AppName "Gesit"
#define AppURL "https://github.com/Rifal21/GesitDownloadManager"

[Setup]
AppId={{6F1D2C3A-8B54-4E0A-9C7D-1A2B3C4D5E6F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Rifal21
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
; per-user install by default (no administrator prompt); the user may choose "all users" in the dialog
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist
OutputBaseFilename=Gesit-{#AppVersion}-windows-x64-setup
SetupIconFile=gesit.ico
UninstallDisplayIcon={app}\gesit.exe
LicenseFile=..\..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=no
; a user-level install keeps running instances out of the way via PrepareToInstall below

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "integration"; Description: "Set up browser integration (Chrome, Edge, Brave, Vivaldi, Firefox)"; GroupDescription: "Browsers:"
Name: "autostart"; Description: "Start Gesit hidden when I sign in (needed for browser integration to be always ready)"; GroupDescription: "Startup:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "..\..\build\windows\dist\Gesit\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\..\extension\dist\chrome\*"; DestDir: "{app}\extension\chrome"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\..\extension\dist\firefox\*"; DestDir: "{app}\extension\firefox"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\..\build\windows\tools\*"; DestDir: "{app}\tools"; Flags: recursesubdirs createallsubdirs ignoreversion skipifsourcedoesntexist
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\gesit.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\gesit.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\gesit.exe"; Parameters: "--install-host"; StatusMsg: "Setting up browser integration..."; Flags: runhidden; Tasks: integration
Filename: "{app}\gesit.exe"; Parameters: "--enable-autostart"; Flags: runhidden; Tasks: autostart
Filename: "{app}\gesit.exe"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\gesit.exe"; Parameters: "--quit"; Flags: runhidden; RunOnceId: "GesitQuit"
Filename: "{app}\gesit.exe"; Parameters: "--uninstall-host"; Flags: runhidden; RunOnceId: "GesitHost"
Filename: "{app}\gesit.exe"; Parameters: "--disable-autostart"; Flags: runhidden; RunOnceId: "GesitAutostart"

[Code]
// Ask a running Gesit to quit so its files can be replaced during an upgrade.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  Exe: String;
begin
  Exe := ExpandConstant('{app}\gesit.exe');
  if FileExists(Exe) then
  begin
    Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Sleep(1500);
  end;
  Result := '';
end;
