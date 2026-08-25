param([string]$PackageRoot=$PSScriptRoot,[string]$InstallRoot=(Join-Path ${env:ProgramFiles} 'ContentOps\Client'),[string]$DataRoot=(Join-Path $env:ProgramData 'ContentOps\Client'))
$ErrorActionPreference='Stop'; & (Join-Path $InstallRoot 'scripts\Stop-ContentOpsClient.ps1') -Root $InstallRoot -DataRoot $DataRoot
& (Join-Path $PackageRoot 'Setup-ContentOpsClient.ps1') -SourceRoot $PackageRoot -YtDownloadRoot (Join-Path $PackageRoot '..\YTDOWNLOAD') -InstallRoot $InstallRoot -DataRoot $DataRoot
