; EyeTerm Setup (Win10/11 x64) - build: ISCC.exe installer\EyeTerm.iss
; 2026-09-17 三大改造①③：文件名可携带预置注册配置（EyeTerm_Setup_x64_{ver}_{cfg64}.exe，
; cfg64=base64url(zlib(json{"s":server,"t":token}))，协议 main 定稿）——安装器从自身
; 文件名提取 cfg64 原样写入 {app}\config_bootstrap.json（raw 透传，解码在客户端
; bootstrap.py，解析失败/字段不全 → 客户端完全回退手动流程，零行为变化）。
#define MyAppName 'guanshuhu-terminal'
#define MyAppExeName 'winhelper.exe'
#define MyAppVersion '4.1.10'

[Setup]
AppId={{8E6C2A70-91D4-4B7E-9A3F-1E4E7B9C0D55}
AppName=EyeTerm - guanshuhu-terminal
AppVersion={#MyAppVersion}
AppVerName=EyeTerm {#MyAppVersion}
DefaultDirName={autopf}\EyeTerm
UninstallDisplayName=EyeTerm
UninstallDisplayIcon={app}\{#MyAppExeName}
DefaultGroupName=EyeTerm
OutputDir=Output
; 2026-09-16：安装包命名带版本号与时间戳（用户要求），不再固定名覆盖
OutputBaseFilename=EyeTerm_Setup_x64_{#MyAppVersion}_{#GetDateTimeString("yyyymmdd_hhnn","","")}
SetupIconFile=..\app.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=6.2
; 2026-09-17（4.1.1 加急）：关闭 Restart Manager「Setup was unable to
; automatically close all applications」弹窗路径——应用生命周期由 [Code]
; PrepareToInstall 的 taskkill 自杀逻辑接管（用户实机复现 RM 弹窗在
; CurStepChanged 之前触发，ssInstall 内 taskkill 打不中）。
CloseApplications=no
RestartApplications=no

[Languages]
Name: english; MessagesFile: compiler:Default.isl

[Tasks]
Name: desktopicon; Description: {cm:CreateDesktopIcon}; GroupDescription: {cm:AdditionalIcons}
; 2026-09-17 三大改造②：开机自启改默认勾选（HKCU Run 同一键，客户端设置弹窗可改）
Name: autostart; Description: '开机自动启动（静默运行）'

[Files]
Source: ..\dist\winhelper.exe; DestDir: {app}; Flags: ignoreversion
Source: MicrosoftEdgeWebView2RuntimeInstallerX64.exe; DestDir: {tmp}; Flags: deleteafterinstall
; 托盘图标（三大改造②：Shell_NotifyIconW 读取 exe 同目录 app.ico）
Source: ..\app.ico; DestDir: {app}; Flags: ignoreversion
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
; 4.1.7：--autostart 旗标（开机自启静默收纳托盘，不弹主窗体）；存量终端由
; desktop.py ensure_run_key_autostart 幂等重写兜底
Root: HKCU; Subkey: Software\Microsoft\Windows\CurrentVersion\Run; ValueType: string; ValueName: EyeTerm; ValueData: """{app}\{#MyAppExeName}"" --autostart"; Tasks: autostart; Flags: uninsdeletevalue
; 文件检索 {app} 探测：终端侧定位链（env → 配置 → 注册表 → 常见路径）第三级命中安装目录
Root: HKLM; Subkey: Software\EyeTerm; ValueType: string; ValueName: EverythingPath; ValueData: "{app}\everything\Everything.exe"; Flags: uninsdeletevalue

[Run]
Filename: certutil; Parameters: "-addstore -f Root ""{app}\assets\eyeterm_root_ca.crt"""; Flags: runhidden; StatusMsg: "信任 EyeTerm 平台根证书..."
; 2026-09-17（4.1.1 加急）：移除 skipifsilent——静默更新链装完同样自动拉起客户端
; 2026-09-18（4.1.6）：安装后自启统一走 --replace——新实例接管旧实例（若
; PrepareToInstall taskkill 后仍有存活/迟启动实例），杜绝「更新重启+手动
; 点击」叠出多开；无旧实例时幂等正常启动。
Filename: {app}\{#MyAppExeName}; Parameters: --replace; Description: {cm:LaunchProgram,EyeTerm}; Flags: nowait postinstall

[UninstallRun]
Filename: {cmd}; Parameters: /C taskkill /IM winhelper.exe /F; Flags: runhidden; RunOnceId: KillApp
; 4.1.7：卸载清理文件检索索引器计划任务（防卸载后 worker 仍被拉起）
Filename: {cmd}; Parameters: /C schtasks /Delete /F /TN EyeTermFileIndexer; Flags: runhidden; RunOnceId: DelFsIndexerTask
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

// ---- 4.1.1 加急（2026-09-17）：运行态安装自愈 ----
// Restart Manager 已关（CloseApplications=no），应用关闭由本函数在「文件复制
// 与 RM 检查之前」接管：taskkill /F /T → 轮询等进程消失（10s 上限/200ms 步进）
// → 仍存活才中止并给中文指引。Everything-eyeterm 实例由 ssInstall 既有
// taskkill /IM Everything.exe 覆盖（在 {app} 覆盖范围内），不在此重复。
function AppRunning(): Boolean;
var
  Rc: Integer;
begin
  // tasklist|find 命中返回 0、未命中返回 1（避免 Inno FileSize 形态差异）
  Exec(ExpandConstant('{cmd}'),
       '/C tasklist /FI "IMAGENAME eq winhelper.exe" | find /I "winhelper.exe" > NUL',
       '', SW_HIDE, ewWaitUntilTerminated, Rc);
  Result := (Rc = 0);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  i: Integer; Clean: Boolean; Rc: Integer;
begin
  Result := '';
  Clean := False;
  for i := 1 to 3 do
    Exec(ExpandConstant('{cmd}'), '/C taskkill /F /T /IM winhelper.exe',
         '', SW_HIDE, ewWaitUntilTerminated, Rc);
  for i := 1 to 50 do   // 10s 上限 / 200ms 步进
  begin
    if not AppRunning() then begin Clean := True; Break; end;
    Sleep(200);
  end;
  if not Clean then
    Result := '无法自动关闭正在运行的 EyeTerm（winhelper.exe）。' +
      '请打开任务管理器，手动结束 winhelper.exe 进程后重新运行本安装程序。';
end;

// ---- 三大改造①：安装包文件名解析（协议 main 定稿）----
// EyeTerm_Setup_x64_{ver}_{cfg64}.exe → cfg64 = 去掉固定前缀与 .exe 后的剩余段
// （base64url 字符集含下划线，故不能按末个下划线切分；固定前缀切分与客户端
// bootstrap.py 正则语义一致）。cfg64 原样写入 config_bootstrap.json（raw 透传），
// 解码与校验统一在客户端（Inno 不做 zlib/base64 解压）。
function WriteBootstrapFromName(): Boolean;
var
  ExeName, Prefix, Cfg, Json: String;
begin
  Result := False;
  try
    Prefix := 'EyeTerm_Setup_x64_';
    ExeName := ExtractFileName(ExpandConstant('{srcexe}'));
    if Pos(Prefix, ExeName) <> 1 then
      Exit;
    Cfg := Copy(ExeName, Length(Prefix) + 1, Length(ExeName));
    // 去掉 .exe 尾（大小写不敏感）
    if Length(Cfg) > 4 then
    begin
      if (Copy(Cfg, Length(Cfg) - 3, 4) = '.exe') or (Copy(Cfg, Length(Cfg) - 3, 4) = '.EXE') then
        Cfg := Copy(Cfg, 1, Length(Cfg) - 4);
    end;
    if Length(Cfg) < 8 then
      Exit;
    Json := '{"cfg64":"' + Cfg + '"}';
    SaveStringToFile(ExpandConstant('{app}\config_bootstrap.json'), Json, False);
    Result := True;
  except
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Rc: Integer;
begin
  if CurStep = ssInstall then
  begin
    // 2026-09-17：升级安装前停止运行中的客户端（否则覆盖 winhelper.exe 报 DeleteFile code 5）
    Exec(ExpandConstant('{cmd}'), '/C taskkill /IM winhelper.exe /F', '', SW_HIDE, ewWaitUntilTerminated, Rc);
    Sleep(1500);
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
    // 4.1.7 文件检索自研索引器（方案 A 主通道）：注册 SYSTEM 计划任务（开机触发）
    // 并立即 Run 一次（装完即首建，不等重启）；/F 幂等覆盖（重装/升级安全）。
    // 失败不阻断安装（客户端方案 B 管理员引导可兜底补部署）。
    Exec(ExpandConstant('{cmd}'),
         '/C schtasks /Create /F /TN EyeTermFileIndexer /TR "\"{app}\winhelper.exe\" --fs-indexer-worker" /SC ONSTART /RL HIGHEST',
         '', SW_HIDE, ewWaitUntilTerminated, Rc);
    Exec(ExpandConstant('{cmd}'), '/C schtasks /Run /TN EyeTermFileIndexer',
         '', SW_HIDE, ewWaitUntilTerminated, Rc);
    // 三大改造①：文件名携带预置注册配置 → {app}\config_bootstrap.json（raw 透传）；
    // 任何失败静默跳过（不写文件 → 客户端完全回退手动流程，零行为变化）。
    WriteBootstrapFromName();
  end;
end;
