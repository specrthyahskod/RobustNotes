#define AppName "RobustNotes"
#define AppVersion "0.1.5"
#define AppPublisher "RobustNotes"
#define AppExeName "RobustNotes.exe"

[Setup]
AppId={{9B5A829B-9F38-4D39-BF1A-70E65C742F91}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\RobustNotes
DefaultGroupName=RobustNotes
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir=output
OutputBaseFilename=RobustNotes-Setup-{#AppVersion}
SetupIconFile=..\assets\robustnotes-icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\RobustNotes.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\RobustNotes"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\RobustNotes"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch RobustNotes"; Flags: postinstall nowait skipifsilent
