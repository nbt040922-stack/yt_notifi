param([string]$InstallRoot=(Join-Path ${env:ProgramFiles} 'ContentOps\Client'),[string]$DataRoot=(Join-Path $env:ProgramData 'ContentOps\Client'))
$ErrorActionPreference='Stop'
& (Join-Path $PSScriptRoot 'scripts\Restart-ContentOpsClient.ps1') -Root $InstallRoot -DataRoot $DataRoot
