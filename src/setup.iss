; GenshinMultiAccountTool - Inno Setup 安装脚本
; 使用前请先执行 build_onedir.py 生成 dist_onedir 目录
; 安装包结构: exe + tesseract-ocr + 配置文件（其他全部嵌入 exe）

#define MyAppName "GenshinMultiAccountTool"
#define MyAppVersion "1.1.1"
#define MyAppPublisher "GenshinAutoTool"
#define MyAppURL ""
#define MyAppExeName "GenshinMultiAccountTool.exe"

[Setup]
; 签名信息
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}

; 安装路径（支持自定义）
DefaultDirName={autopf}\{#MyAppName}
DisableDirPage=no
DefaultGroupName={#MyAppName}

; 输出
OutputDir=.\dist_installer
OutputBaseFilename={#MyAppName}_Setup_v{#MyAppVersion}
SetupIconFile=icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

; 架构（32/64 位通用）
ArchitecturesInstallIn64BitMode=x64compatible

; 要求管理员权限（写入 Program Files 需要）
PrivilegesRequired=admin

; 版本信息
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; ==== 主程序（单文件 exe，所有 Python 代码和依赖已嵌入）====
Source: "dist_onedir\{#MyAppName}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; ==== tesseract-ocr 引擎（体积过大，保留为独立目录）====
Source: "dist_onedir\{#MyAppName}\tesseract-ocr\*"; DestDir: "{app}\tesseract-ocr"; Flags: ignoreversion recursesubdirs createallsubdirs

; ==== 配置文件：仅首次安装时复制（升级不覆盖已有用户数据）====
Source: "dist_onedir\{#MyAppName}\config.json"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "dist_onedir\{#MyAppName}\scheduler_config.json"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

[Icons]
; 开始菜单快捷方式
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"

; 桌面快捷方式（用户可选）
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 安装完成后可选启动程序
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Registry]
; 写入注册表以便 Windows 设置 → 应用 中显示并正常卸载
Root: HKLM; Subkey: "SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}"; \
    ValueType: string; ValueName: "DisplayName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}"; \
    ValueType: string; ValueName: "DisplayVersion"; ValueData: "{#MyAppVersion}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}"; \
    ValueType: string; ValueName: "Publisher"; ValueData: "{#MyAppPublisher}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}"; \
    ValueType: string; ValueName: "UninstallString"; ValueData: "{uninstallexe}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}"; \
    ValueType: string; ValueName: "DisplayIcon"; ValueData: "{app}\{#MyAppExeName}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}"; \
    ValueType: dword; ValueName: "NoModify"; ValueData: "$00000001"; Flags: uninsdeletekey
Root: HKLM; Subkey: "SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}"; \
    ValueType: dword; ValueName: "NoRepair"; ValueData: "$00000001"; Flags: uninsdeletekey
Root: HKLM; Subkey: "SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppName}"; \
    ValueType: dword; ValueName: "EstimatedSize"; ValueData: "$00007A12"; Flags: uninsdeletekey

[UninstallDelete]
; 清理程序生成的数据文件（logs 等运行时生成的文件）
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{app}\*.log"

[Code]
function InitializeSetup: Boolean;
begin
  Result := True;
end;
