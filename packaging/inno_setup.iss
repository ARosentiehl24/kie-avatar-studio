; Inno Setup script para Kie Avatar Studio.
;
; Genera un instalador `KieAvatarStudio-Setup-vX.Y.Z.exe` que:
; - Copia `KieAvatarStudio.exe` a `Program Files\Kie Avatar Studio\`.
; - Crea shortcut en Start Menu y opcionalmente en Desktop.
; - Registra el uninstaller en Panel de Control.
;
; El workflow `.github/workflows/release.yml` lo invoca con:
;
;     iscc /DAppVersion=1.2.3 packaging/inno_setup.iss
;
; Inno Setup viene preinstalado en los runners windows-latest de GitHub Actions.

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
AppName=Kie Avatar Studio
AppVersion={#AppVersion}
AppPublisher=Alberto Rosentiehl
AppPublisherURL=https://github.com/ARosentiehl24/kie-avatar-studio
AppSupportURL=https://github.com/ARosentiehl24/kie-avatar-studio/issues
AppUpdatesURL=https://github.com/ARosentiehl24/kie-avatar-studio/releases
DefaultDirName={autopf}\Kie Avatar Studio
DefaultGroupName=Kie Avatar Studio
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=KieAvatarStudio-Setup-v{#AppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\KieAvatarStudio.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\KieAvatarStudio.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Kie Avatar Studio"; Filename: "{app}\KieAvatarStudio.exe"
Name: "{group}\{cm:UninstallProgram,Kie Avatar Studio}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Kie Avatar Studio"; Filename: "{app}\KieAvatarStudio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\KieAvatarStudio.exe"; Description: "{cm:LaunchProgram,Kie Avatar Studio}"; Flags: nowait postinstall skipifsilent
