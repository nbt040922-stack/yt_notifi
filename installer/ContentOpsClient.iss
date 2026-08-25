#ifndef PackageRoot
#define PackageRoot "D:\ContentOpsClient"
#endif
#define MyAppName "ContentOps Client"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "ContentOps"

[Setup]
AppId={{D5D8D0D8-2C0B-4F4C-9F19-CONTENTOPSCLIENT}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\ContentOps\Client
DefaultGroupName=ContentOps Client
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=D:\
OutputBaseFilename=ContentOps_Client_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=ContentOps Client
DisableProgramGroupPage=yes

[Tasks]
Name: "startup"; Description: "Tự khởi chạy cùng Windows"; GroupDescription: "Tùy chọn:"; Flags: checkedonce
Name: "launchsetup"; Description: "Mở trang cấu hình sau khi cài đặt"; GroupDescription: "Tùy chọn:"; Flags: checkedonce

[Files]
; PackageRoot contains yt_notifi_bootstrap.exe, YTDOWNLOAD.exe, bundled tools, scripts and manifests.
Source: "{#PackageRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{commonappdata}\ContentOps\Client\config"
Name: "{commonappdata}\ContentOps\Client\state"
Name: "{commonappdata}\ContentOps\Client\logs"
Name: "{commonappdata}\ContentOps\Client\runtime"

[Icons]
Name: "{group}\ContentOps Status"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Status-ContentOpsClient.ps1"""
Name: "{commonstartup}\ContentOps Client"; Filename: "{app}\yt_notifi\yt_notifi_bootstrap.exe"; WorkingDir: "{app}\yt_notifi"; Tasks: startup

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Setup-ContentOpsClient.ps1"" -SourceRoot ""{app}"" -InstallRoot ""{app}"" -DataRoot ""{commonappdata}\ContentOps\Client"""; Flags: waituntilterminated; Description: "Khởi động ContentOps Client và kiểm tra health"; StatusMsg: "Đang khởi động và kiểm tra 8787/8790..."
Filename: "http://127.0.0.1:8787/setup"; Description: "Mở trang cấu hình"; Flags: shellexec postinstall skipifsilent; Tasks: launchsetup

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\Uninstall-ContentOpsClient.ps1"" -InstallRoot ""{app}"" -DataRoot ""{commonappdata}\ContentOps\Client"""; Flags: runhidden waituntilterminated

