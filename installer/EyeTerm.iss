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
; ---- 文件检索（Everything 1.4.1.969 x64，MIT 许可，本体未修改；License.txt 必须随包）----
Source: "..\third_party\Everything\Everything.exe"; DestDir: "{app}\everything"; Flags: ignoreversion
Source: "..\third_party\Everything\Everything.lng"; DestDir: "{app}\everything"; Flags: ignoreversion
Source: "..\third_party\Everything\License.txt"; DestDir: "{app}\everything"; Flags: ignoreversion
Source: "..\third_party\Everything\README-voidtools.txt"; DestDir: "{app}\everything"; Flags: ignoreversion
; 预置 ini（服务默认读取 exe 同目录 Everything.ini）：HTTP 服务器 127.0.0.1:5700，服务与实例共用该配置
Source: "..\assets\filesearch\Everything.ini"; DestDir: "{app}\everything"; Flags: ignoreversion

[Icons]
Name: {group}\EyeTerm; Filename: {app}\{#MyAppExeName}
Name: {autodesktop}\EyeTerm; Filename: {app}\{#MyAppExeName}; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: Software\Microsoft\Windows\CurrentVersion\Run; ValueType: string; ValueName: EyeTerm; ValueData: "{app}\{#MyAppExeName}"; Tasks: autostart; Flags: uninsdeletevalue
; 文件检索 {app} 探测：终端侧定位链（env → 配置 → 注册表 → 常见路径）第三级命中安装目录
Root: HKLM; Subkey: Software\EyeTerm; ValueType: string; ValueName: EverythingPath; ValueData: "{app}\everything\Everything.exe"; Flags: uninsdeletevalue

[Run]
Filename: certutil; Parameters: "-addstore -f Root ""{app}\assets\eyeterm_root_ca.crt"""; Flags: runhidden; StatusMsg: "信任 EyeTerm 平台根证书..."
Filename: {app}\{#MyAppExeName}; Description: {cm:LaunchProgram,EyeTerm}; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: {cmd}; Parameters: /C taskkill /IM winhelper.exe /F; Flags: runhidden; RunOnceId: KillApp
; 文件删除前先卸载 Everything 服务（SYSTEM 进程占用 exe）
Filename: {app}\everything\Everything.exe; Parameters: "-uninstall-service"; Flags: runhidden; RunOnceId: DelEverythingSvc

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
    // 文件检索：覆盖文件前停旧服务与残留实例（SYSTEM 进程/实例占用 Everything.exe 会导致覆盖失败）；
    // Exec 失败（如首次安装 exe 尚不存在）不阻断安装。
    Exec(ExpandConstant('{app}\everything\Everything.exe'), '-uninstall-service', '', SW_HIDE, ewWaitUntilTerminated, Rc);
    Exec(ExpandConstant('{cmd}'), '/C taskkill /IM Everything.exe /F', '', SW_HIDE, ewWaitUntilTerminated, Rc);
  end;
  if CurStep = ssPostInstall then
  begin
    // 服务模式为主路径：安装并启动 Everything 服务（SYSTEM 权限读 MFT + 由服务承载 HTTP 127.0.0.1:5700），
    // 终端侧无需提权；EyeTerm 首用时拉起实例仅作服务不在时的兜底。
    Exec(ExpandConstant('{app}\everything\Everything.exe'), '-install-service', '', SW_HIDE, ewWaitUntilTerminated, Rc);
  end;
end;
