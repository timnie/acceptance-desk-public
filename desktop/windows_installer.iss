#define AppExecutable "AcceptanceDesk.exe"
#define WebView2Setup "MicrosoftEdgeWebview2Setup.exe"
#define WebView2Key "SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

[Setup]
AppId=org.acceptancedesk.workbench
AppName=AI 改稿验收台
AppVersion=0.0.1
VersionInfoVersion=0.0.1.10
DefaultDirName={localappdata}\Programs\AcceptanceDesk
DefaultGroupName=AI 改稿验收台
UninstallDisplayIcon={app}\{#AppExecutable}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.19045
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
OutputDir=..\dist\windows-installer
OutputBaseFilename=Acceptance-Desk-Windows-x64-Setup

[Files]
Source: "..\dist\AcceptanceDesk\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\desktop\{#WebView2Setup}"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\AI 改稿验收台"; Filename: "{app}\{#AppExecutable}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#AppExecutable}"; Description: "打开 AI 改稿验收台"; Flags: nowait postinstall skipifsilent

[Code]
function HasWebView2(): Boolean;
var
  Version: String;
begin
  Result :=
    (RegQueryStringValue(HKLM32, '{#WebView2Key}', 'pv', Version) and
      (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, '{#WebView2Key}', 'pv', Version) and
      (Version <> '') and (Version <> '0.0.0.0'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
begin
  Result := '';
  if HasWebView2() then
    Exit;
  ExtractTemporaryFile('{#WebView2Setup}');
  if not Exec(ExpandConstant('{tmp}\{#WebView2Setup}'), '/silent /install',
              '', SW_SHOWNORMAL, ewWaitUntilTerminated, ExitCode) or
     (ExitCode <> 0) or not HasWebView2() then
    Result := '无法安装 Microsoft Edge WebView2 运行时。请连接网络后重试。';
end;
