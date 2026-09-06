; Inno Setup script for PortableFix.
; Compile with ISCC.exe (Inno Setup 6+) from the repo root:
;   ISCC installer\PortableFix.iss
; Bump MyAppVersion together with portablefix/version.py on every release.
#define MyAppName "PortableFix"
#define MyAppVersion "1.0.4"
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
DefaultGroupName={#MyAppName}
; Lets the user choose install-for-me (no admin, works for a USB drive path
; too - it is just a folder) vs install-for-all-users (Program Files, needs
; admin). PortableFix itself elevates on launch via PortableFix.cmd, so the
; installer does not need to force admin up front.
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
Source: "{#RepoRoot}\Data\*"; DestDir: "{app}\Data"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#RepoRoot}\Modules\*"; DestDir: "{app}\Modules"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#RepoRoot}\Vendor\*"; DestDir: "{app}\Vendor"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#RepoRoot}\PortableFix.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#RepoRoot}\portablefix.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\PortableFix.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\portablefix.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\PortableFix.cmd"; WorkingDir: "{app}"; IconFilename: "{app}\portablefix.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\PortableFix.cmd"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent runasoriginaluser
