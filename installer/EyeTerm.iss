; EyeTerm Setup (Win10/11 x64) - build: ISCC.exe installer\EyeTerm.iss
#define MyAppName 'guanshuhu-terminal'
#define MyAppExeName 'winhelper.exe'

[Setup]
AppId={{8E6C2A70-91D4-4B7E-9A3F-1E4E7B9C0D55}
AppName=EyeTerm - guanshuhu-terminal
AppVersion=4.0.0
AppVerName=EyeTerm 4.0.0
DefaultDirName={autopf}\EyeTerm
UninstallDisplayName=EyeTerm
UninstallDisplayIcon={app}\{#MyAppExeName}
DefaultGroupName=EyeTerm
OutputDir=Output
OutputBaseFilename=EyeTerm_Setup_x64
SetupIconFile=..\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=6.2

[Languages]
Name: english; MessagesFile: compiler:Default.isl

[Tasks]
Name: desktopicon; Description: {cm:CreateDesktopIcon}; GroupDescription: {cm:AdditionalIcons}
Name: autostart; Description: '开机自动启动（静默运行）'; Flags: unchecked

[Files]
Source: ..\dist\winhelper.exe; DestDir: {app}; Flags: ignoreversion
Source: MicrosoftEdgeWebView2RuntimeInstallerX64.exe; DestDir: {tmp}; Flags: deleteafterinstall
; EyeTerm 平台自建根证书（公开文件，私钥在服务器）：安装时自动导入系统"受信任的根证书颁发机构"，
; 使本机浏览器可直接信任 https 管理端（8443）。卸载不移除该信任（保留平台访问能力）。
Source: ..\assets\platform_ca.pem; DestDir: {app}\assets; DestName: eyeterm_root_ca.crt; Flags: ignoreversion

[Icons]
Name: {group}\EyeTerm; Filename: {app}\{#MyAppExeName}
Name: {autodesktop}\EyeTerm; Filename: {app}\{#MyAppExeName}; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: Software\Microsoft\Windows\CurrentVersion\Run; ValueType: string; ValueName: EyeTerm; ValueData: "{app}\{#MyAppExeName}"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: certutil; Parameters: "-addstore -f Root ""{app}\assets\eyeterm_root_ca.crt"""; Flags: runhidden; StatusMsg: "信任 EyeTerm 平台根证书..."
Filename: {app}\{#MyAppExeName}; Description: {cm:LaunchProgram,EyeTerm}; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: {cmd}; Parameters: /C taskkill /IM winhelper.exe /F; Flags: runhidden; RunOnceId: KillApp

[Code]
function IsWin7OrOlder(): Boolean;
var
  V: TWindowsVersion;
begin
  GetWindowsVersionEx(V);
  Result := (V.Major < 6) or ((V.Major = 6) and (V.Minor <= 1));
end;

function DotNetRelease(): Cardinal;
begin
  Result := 0;
  RegQueryDWordValue(HKLM, 'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full', 'Release', Result);
end;

function WebView2Installed(): Boolean;
var
  Pv: String;
begin
  Result := RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Pv) and (Pv <>'');
  if not Result then
    Result := RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Pv) and (Pv <>'');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpReady then
  begin
    if IsWin7OrOlder() then
    begin
      if MsgBox('Windows 7 or earlier detected. The runtime framework (Python 3.12 / WebView2) does not support Windows 7. Continue anyway?', mbConfirmation, MB_YESNO) = IDNO then
      begin
        Result := False;
        Exit;
      end;
    end;
    if DotNetRelease() < 394802 then
    begin
      MsgBox('.NET Framework 4.6.2+ is required. Please install .NET Framework 4.8 Runtime first.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Rc: Integer;
begin
  if CurStep = ssInstall then
  begin
    if not WebView2Installed() then
    begin
      Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebView2RuntimeInstallerX64.exe'), '/silent /install', '', SW_SHOW, ewWaitUntilTerminated, Rc);
    end;
  end;
end;
