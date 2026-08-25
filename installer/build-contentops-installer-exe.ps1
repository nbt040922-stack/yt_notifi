param([string]$PackageRoot='D:\ContentOpsClient',[string]$Output='D:\ContentOpsClient\ContentOps_Client_Setup.exe')
$ErrorActionPreference='Stop'
$zip=Join-Path (Split-Path $PackageRoot -Parent) ((Split-Path $PackageRoot -Leaf)+'.zip')
$cmd=Join-Path (Split-Path $PSScriptRoot -Parent) 'installer\Install-ContentOpsClient.cmd'
if(-not(Test-Path $zip)){throw "PACKAGE_ZIP_MISSING: $zip"}
if(-not(Test-Path $cmd)){throw "BOOTSTRAP_MISSING: $cmd"}
$sed=Join-Path $env:TEMP 'ContentOpsClient.sed'
@"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=1
HideExtractAnimation=0
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=Nhấn Install để cài ContentOps Client.
DisplayLicense=
FinishMessage=Cài đặt ContentOps Client đã hoàn tất.
TargetName=$Output
FriendlyName=ContentOps Client
AppLaunched=Install-ContentOpsClient.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
SourceFiles=SourceFiles
[SourceFiles]
SourceFiles0=$zip
SourceFiles1=$cmd
[Strings]
"@ | Set-Content -Path $sed -Encoding ASCII
& iexpress.exe /N $sed
if($LASTEXITCODE){throw 'IEXPRESS_BUILD_FAILED'}
if(-not(Test-Path $Output)){throw "INSTALLER_EXE_MISSING: $Output"}
Add-Content (Join-Path $PackageRoot 'SHA256SUMS.txt') (('{0}  ContentOps_Client_Setup.exe' -f (Get-FileHash $Output -Algorithm SHA256).Hash))
Write-Output "INSTALLER_READY=$Output"
