; 伴时 Windows installer. Built by installer/build.ps1, which passes:
;   /DSourceDir=<staged app folder: BanShi.exe, _internal, ollama, models>
;   /DOutputDir=<where BanShi-Setup.exe goes>
;   /DAppVersion=<version string>
;   /DWebView2Setup=<path to MicrosoftEdgeWebview2Setup.exe bootstrapper>

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
; never change AppId: it's how upgrades find (and replace) an existing install
AppId={{6F3C2A1E-9B7D-4E5A-8C21-BA5D1E7F0A43}
AppName=伴时
AppVersion={#AppVersion}
AppVerName=伴时 {#AppVersion}
AppPublisher=伴时
; per-user install: no admin prompt, and the app can write next to itself
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\BanShi
DisableDirPage=yes
DisableProgramGroupPage=yes
DefaultGroupName=伴时
UninstallDisplayIcon={app}\BanShi.exe
UninstallDisplayName=伴时
OutputDir={#OutputDir}
OutputBaseFilename=BanShi-Setup
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; the bundle is ~3.5GB, past what the 32-bit loader can map
UseSetupLdr=x64
; model weights don't compress; fast lzma2 keeps build and install time sane
Compression=lzma2/fast
SolidCompression=no
CloseApplications=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "在桌面创建快捷方式"; GroupDescription: "快捷方式："

[InstallDelete]
; upgrades: a different bundled model or Ollama build would otherwise leave
; gigabytes of stale blobs/libraries behind next to the new ones
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\ollama"
Type: filesandordirs; Name: "{app}\_internal"

[UninstallDelete]
; Ollama writes a metadata cache into its models folder at runtime
Type: filesandordirs; Name: "{app}\models"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#WebView2Setup}"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsWebView2

[Icons]
Name: "{group}\伴时"; Filename: "{app}\BanShi.exe"
Name: "{group}\卸载伴时"; Filename: "{uninstallexe}"
Name: "{autodesktop}\伴时"; Filename: "{app}\BanShi.exe"; Tasks: desktopicon

[Run]
; the window is an Edge WebView2; Windows 11 ships it, some Windows 10 installs don't
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "正在安装 Microsoft Edge WebView2 运行库…"; Check: NeedsWebView2
Filename: "{app}\BanShi.exe"; Description: "立即打开伴时"; Flags: nowait postinstall skipifsilent

; User data (角色、聊天记录、信件) lives in %APPDATA%\BanShi and is kept on
; uninstall on purpose, so reinstalling or upgrading never loses it.

[Code]
const
  WebView2Key = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function HasVersion(Root: Integer; Key: String): Boolean;
var
  V: String;
begin
  Result := RegQueryStringValue(Root, Key, 'pv', V) and (V <> '') and (V <> '0.0.0.0');
end;

function NeedsWebView2: Boolean;
begin
  Result := not (HasVersion(HKLM, 'SOFTWARE\WOW6432Node\' + WebView2Key) or
                 HasVersion(HKLM, 'SOFTWARE\' + WebView2Key) or
                 HasVersion(HKCU, 'Software\' + WebView2Key));
end;
