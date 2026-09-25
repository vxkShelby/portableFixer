; Inno Setup script for PortableFix.
; Compile with ISCC.exe (Inno Setup 6+) from the repo root:
;   ISCC installer\PortableFix.iss
; Bump MyAppVersion together with portablefix/version.py on every release;
; scripts\build.ps1 refuses to build when they differ, and passes the checked
; value as /DMyAppVersion (hence #ifndef: a command-line define wins).
#define MyAppName "PortableFix"
#ifndef MyAppVersion
#define MyAppVersion "1.11.4"
#endif
#define MyAppPublisher "vxkShelby"
#define MyAppURL "https://github.com/vxkShelby/portableFixer"
#define RepoRoot ".."

[Setup]
AppId={{B4E6C6A0-3F5D-4B7C-9E1A-2C8D6F0A1B3E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
; #MyAppName is appended automatically -> suggested default is "...\PortableFix"
DefaultDirName={autopf}\{#MyAppName}
; Without this, Inno's "auto" default for the Select Destination Location
; page skips it whenever the default path looks fine to it (which is always,
; with PrivilegesRequired=lowest) - users never get a chance to pick.
DisableDirPage=no
DefaultGroupName={#MyAppName}
; Lets the user choose install-for-me (no admin, works for a USB drive path
; too - it is just a folder) vs install-for-all-users (Program Files, needs
; admin). PortableFix launches non-elevated and only elevates on demand via
; its own in-app "Restart as Administrator" button, so the installer does
; not need to force admin up front.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
DisableProgramGroupPage=yes
OutputDir={#RepoRoot}\Output
OutputBaseFilename=PortableFix-Setup
SetupIconFile={#RepoRoot}\portablefix.ico
UninstallDisplayIcon={app}\portablefix.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#RepoRoot}\App\*"; DestDir: "{app}\App"; Flags: ignoreversion recursesubdirs createallsubdirs
; Data\ by allowlist, like the portable zip: on the build machine it also
; holds that machine's settings.json and update status, which would ship to
; every user and overwrite their settings on each reinstall.
Source: "{#RepoRoot}\Data\SHA256SUMS"; DestDir: "{app}\Data"; Flags: ignoreversion
Source: "{#RepoRoot}\Data\PortableFix-SelfSigned.cer"; DestDir: "{app}\Data"; Flags: ignoreversion
Source: "{#RepoRoot}\Data\.gitkeep"; DestDir: "{app}\Data"; Flags: ignoreversion
Source: "{#RepoRoot}\Modules\*"; DestDir: "{app}\Modules"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#RepoRoot}\Vendor\*"; DestDir: "{app}\Vendor"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#RepoRoot}\PortableFix.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\portablefix.ico"; DestDir: "{app}"; Flags: ignoreversion

; What an in-app update leaves behind when it was interrupted: its staged
; package and the backups of the replaced folders. They belong to the version
; being replaced or removed, and Inno Setup only deletes files it installed.
[InstallDelete]
Type: filesandordirs; Name: "{app}\_update_stage"
Type: filesandordirs; Name: "{app}\App.old"
Type: filesandordirs; Name: "{app}\Modules.old"
Type: filesandordirs; Name: "{app}\Vendor.old"
Type: filesandordirs; Name: "{app}\App.failed"
Type: filesandordirs; Name: "{app}\Modules.failed"
Type: filesandordirs; Name: "{app}\Vendor.failed"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\_update_stage"
Type: filesandordirs; Name: "{app}\App.old"
Type: filesandordirs; Name: "{app}\Modules.old"
Type: filesandordirs; Name: "{app}\Vendor.old"
Type: filesandordirs; Name: "{app}\App.failed"
Type: filesandordirs; Name: "{app}\Modules.failed"
Type: filesandordirs; Name: "{app}\Vendor.failed"

[Icons]
; WorkingDir is the install root, never App\: a process whose current
; directory is App\ (the app, or anything it starts) blocks the update from
; renaming that folder.
Name: "{group}\{#MyAppName}"; Filename: "{app}\App\PortableFix.exe"; WorkingDir: "{app}"; IconFilename: "{app}\portablefix.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\App\PortableFix.exe"; WorkingDir: "{app}"; IconFilename: "{app}\portablefix.ico"; Tasks: desktopicon

[Run]
; No runasoriginaluser: Inno's de-elevation trick for that flag (used to
; avoid launching the app elevated right after an admin-mode/per-machine
; install) is a known-fragile mechanism that can fail outright with
; "error 740: the requested operation requires elevation" on some systems
; instead of falling back gracefully - a hard crash on first launch is
; worse than the one-time cosmetic issue of the very first post-install
; launch coming up elevated after an admin install. Every later launch
; (Start Menu/Desktop shortcut, or a per-user install) is unaffected -
; the app's own manifest is asInvoker, so those always start non-elevated.
; WorkingDir as on the shortcuts; left out, it would default to App\.
Filename: "{app}\App\PortableFix.exe"; WorkingDir: "{app}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent
