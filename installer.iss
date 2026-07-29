; Inno Setup script — builds ClippingSoftware-Setup.exe
;
; Produces a normal Windows app: Start Menu entry (searchable), optional desktop
; shortcut, optional "start with Windows", and an uninstall entry in
; Settings > Apps.
;
; Build with:
;   "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
; (run PyInstaller first so dist\ClippingSoftware.exe exists)

#define MyAppName "Clipping Software"
#define MyAppVersion "1.2.3"
#define MyAppPublisher "Caro122"
#define MyAppExeName "ClippingSoftware.exe"

[Setup]
; Keep this GUID stable — it's how Windows recognises upgrades of the same app
AppId={{7995685C-954A-49E4-A0A9-021C9D74DACE}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppSupportURL=https://github.com/Caro1226966/ClippingSoftware
DefaultDirName={autopf}\ClippingSoftware
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Ask each person whether to install just for themselves or for everyone.
; "lowest" means no admin prompt unless they pick all-users.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=dist
OutputBaseFilename=ClippingSoftware-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Matches the mutex the app creates, so Setup detects a running copy
AppMutex=Caro122ClippingSoftware
; Force-close a running copy during update. Without this the old version keeps
; running in memory after an install (the onefile exe runs from a temp copy, so
; the on-disk file isn't locked), and the update silently never takes effect.
CloseApplications=force
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startupicon"; Description: "Start automatically when Windows starts (it records in the background)"; GroupDescription: "Startup:"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu entry — this is what makes it searchable. "--show" opens the
; settings window; without it the app would just sit silently in the tray.
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--show"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--show"; Tasks: desktopicon
; The Startup entry deliberately has no "--show" so it boots quietly to the tray
Name: "{autostartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--show"; Description: "Start {#MyAppName} now"; Flags: nowait postinstall skipifsilent

; Settings and saved clips are intentionally left behind on uninstall so a
; reinstall keeps them; clips live in Videos\clipping.

[Code]
// Guarantee the old copy is gone before we install over it, so the update
// actually takes effect (CloseApplications can miss the temp-extracted onefile).
procedure KillRunningApp;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/C taskkill /F /IM ClippingSoftware.exe',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function InitializeSetup(): Boolean;
begin
  KillRunningApp;
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  KillRunningApp;  // again just before copying files, in case it relaunched
  Result := '';
end;
