; Inno Setup 6 — installation par utilisateur (sans droits administrateur), désinstallation propre.
#define AppName "LDC P2P"
#define AppVersion "2.0.0"
#define AppExe "LDC-P2P.exe"

[Setup]
AppId={{5B1E6C52-7C0B-4E0D-9B0E-1D5F5C0D2A11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=LDC
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=LDC-P2P-Setup-{#AppVersion}
SetupIconFile=ldc-p2p.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; Flags: unchecked

[Files]
Source: "..\dist\LDC-P2P\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Lancer {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
// À la désinstallation, les données utilisateur (identité, messages) sont CONSERVÉES sauf demande explicite.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    if MsgBox('Supprimer aussi vos données LDC P2P (identité, contacts, messages) sur cet ordinateur ?' + #13#10 +
              'Sans votre code de récupération, c''est irréversible.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      DelTree(ExpandConstant('{userappdata}\LDCP2P'), True, True, True);
end;
