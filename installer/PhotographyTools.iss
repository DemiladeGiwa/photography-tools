; Inno Setup script - builds dist\PhotographyTools-<version>-Setup.exe
; Run via:  python scripts\build_standalone.py   (or build.bat), which passes /DAppVersion.

#ifndef AppVersion
  #define AppVersion "4.3.0"
#endif

[Setup]
AppId={{6C3E2B1A-4F5D-4C8E-9A61-2E7F0B9D4A10}
AppName=Photography Tools
AppVersion={#AppVersion}
AppPublisher=Demi
DefaultDirName={autopf}\Photography Tools
DefaultGroupName=Photography Tools
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=PhotographyTools-{#AppVersion}-Setup
SetupIconFile=..\photo_sorter\gui\assets\icon.ico
UninstallDisplayIcon={app}\PhotographyTools.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "foldermenu"; Description: "Add 'Open in Photography Tools' to folder right-click menu"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\PhotographyTools\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Photography Tools"; Filename: "{app}\PhotographyTools.exe"
Name: "{group}\Uninstall Photography Tools"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Photography Tools"; Filename: "{app}\PhotographyTools.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\Directory\shell\PhotographyTools"; ValueType: string; ValueName: ""; ValueData: "Open in Photography Tools"; Flags: uninsdeletekey; Tasks: foldermenu
Root: HKA; Subkey: "Software\Classes\Directory\shell\PhotographyTools"; ValueType: string; ValueName: "Icon"; ValueData: "{app}\PhotographyTools.exe"; Tasks: foldermenu
Root: HKA; Subkey: "Software\Classes\Directory\shell\PhotographyTools\command"; ValueType: string; ValueName: ""; ValueData: """{app}\PhotographyTools.exe"" ""%1"""; Tasks: foldermenu

[Run]
Filename: "{app}\PhotographyTools.exe"; Description: "Start Photography Tools"; Flags: nowait postinstall skipifsilent
