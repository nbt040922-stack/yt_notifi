param([string]$InstallRoot=(Join-Path ${env:ProgramFiles} 'ContentOps\Client'),[string]$DataRoot=(Join-Path $env:ProgramData 'ContentOps\Client'),[switch]$FullClean)
$ErrorActionPreference='Stop'; $script=Join-Path $InstallRoot 'scripts\Stop-ContentOpsClient.ps1'; if(Test-Path $script){& $script -Root $InstallRoot -DataRoot $DataRoot}
foreach($task in 'ContentOps Client - YT_NOTIFI','ContentOps Client - YTDOWNLOAD','ContentOps Client - Watchdog'){Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue}
Remove-Item $InstallRoot -Recurse -Force -ErrorAction SilentlyContinue
if($FullClean){Remove-Item $DataRoot -Recurse -Force}
