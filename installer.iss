; Inno Setup script for HDR to SDR Converter
; Build: installer_output\HDR_to_SDR_Setup.exe

#define AppName      "HDR to SDR Converter"
#define AppVersion   "3.2.0"
#define AppPublisher "Torin Nelson"
#define AppURL       "https://hdrtosdr.com"
#define AppExeName   "HDR_to_SDR_Converter.exe"

[Setup]
AppId={{A7C3F9E2-B841-4D6A-8F2C-1E5D3B0C9A74}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=.\installer_output
#ifdef FREE_BUILD
OutputBaseFilename=HDR_to_SDR_Setup_FREE
#else
OutputBaseFilename=HDR_to_SDR_Setup
#endif
SetupIconFile=.\logo\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Recursively bundle the entire onedir distribution
Source: ".\dist\HDR_to_SDR_Converter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; GPL compliance: the bundled ffmpeg is GPLv2+, so its license text and our
; third-party notices must ship with the application.
Source: ".\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: ".\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
; THIRD_PARTY_NOTICES.md points readers at LICENSE and TRADEMARK.md -- ship
; both so those pointers resolve in an installed app, not just in the repo.
Source: ".\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: ".\TRADEMARK.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{group}\Third-Party Licenses"; Filename: "{app}\THIRD_PARTY_NOTICES.md"

[Run]
Filename: "{app}\{#AppExeName}"; \
  Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
