[Setup]
AppName=DSC Tracker
AppVersion=1.0
AppPublisher=RMH Advisors
DefaultDirName={autopf}\DSC Tracker
DefaultGroupName=DSC Tracker
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=DSC_Tracker_Setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\DSC_Tracker.exe

[Files]
; build with: pyinstaller ... --distpath dist_gui  (see README); change the path if you build into dist\
Source: "..\backend\dist_gui\DSC_Tracker.exe"; DestDir: "{app}"; Flags: ignoreversion
; onlyifdoesntexist: a reinstall/upgrade keeps CLOUD_API_URL edits made by staff
Source: "..\backend\.env"; DestDir: "{app}"; Flags: onlyifdoesntexist

[Icons]
Name: "{autodesktop}\DSC Tracker"; Filename: "{app}\DSC_Tracker.exe"
Name: "{group}\DSC Tracker"; Filename: "{app}\DSC_Tracker.exe"
Name: "{group}\Uninstall DSC Tracker"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\DSC_Tracker.exe"; Description: "Launch DSC Tracker"; Flags: nowait postinstall skipifsilent
