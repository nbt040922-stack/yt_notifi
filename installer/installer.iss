#define MyAppName "YT_NOTIFI"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "YT_NOTIFI"
#define MyAppExeName "yt_notifi_bootstrap.exe"
#ifndef YTDOWNLOAD_SOURCE
#define YTDOWNLOAD_SOURCE "D:\YTDOWNLOAD\dist\YTDOWNLOAD 1.0.0.exe"
#endif
#ifndef BOOTSTRAP_SOURCE
#define BOOTSTRAP_SOURCE "..\dist\yt_notifi_bootstrap"
#endif
[Setup]
AppId={{7BCE1DB9-7D5A-4D98-AE55-7A1FC9E19B8E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\YT_NOTIFI
DefaultGroupName=YT_NOTIFI
OutputDir=..\dist
OutputBaseFilename=YT_NOTIFI_Setup
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
Uninstallable=yes
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
[Tasks]
Name: "startup"; Description: "Tự khởi động cùng Windows"; GroupDescription: "Tùy chọn:"; Flags: checkedonce
[Files]
Source: "{#BOOTSTRAP_SOURCE}\*"; DestDir: "{app}\YT_NOTIFI"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#YTDOWNLOAD_SOURCE}"; DestDir: "{app}\YTDOWNLOAD"; DestName: "YTDOWNLOAD.exe"; Flags: ignoreversion
Source: "packaged_launcher.ps1"; DestDir: "{app}"; Flags: ignoreversion
[Dirs]
Name: "{localappdata}\YT_NOTIFI\config"
Name: "{localappdata}\YT_NOTIFI\state"
Name: "{localappdata}\YT_NOTIFI\logs"
[Icons]
Name: "{group}\YT_NOTIFI"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\packaged_launcher.ps1"""
Name: "{userstartup}\YT_NOTIFI"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\packaged_launcher.ps1"""; Tasks: startup
[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{app}\packaged_launcher.ps1"""; Description: "Khởi động YT_NOTIFI và YTDOWNLOAD"; Flags: postinstall nowait skipifsilent runhidden
Filename: "http://127.0.0.1:8787/setup"; Description: "Mở trang cấu hình Telegram"; Flags: shellexec postinstall skipifsilent
