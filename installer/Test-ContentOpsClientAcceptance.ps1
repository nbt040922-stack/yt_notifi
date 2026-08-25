param(
  [string]$InstallRoot = (Join-Path ${env:ProgramFiles} 'ContentOps\Client'),
  [string]$DataRoot = (Join-Path $env:ProgramData 'ContentOps\Client'),
  [switch]$ExerciseWatchdog
)
$ErrorActionPreference='Stop'
$checks=[ordered]@{}
$checks.windows_x64=[Environment]::Is64BitOperatingSystem
foreach($name in 'python','node','npm','git'){$checks["system_$name"]= -not [bool](Get-Command $name -ErrorAction SilentlyContinue)}
$checks.system_ffmpeg=-not [bool](Get-Command ffmpeg -ErrorAction SilentlyContinue)
$checks.system_ytdlp=-not [bool](Get-Command yt-dlp -ErrorAction SilentlyContinue)
$checks.yt_notifi_package=Test-Path (Join-Path $InstallRoot 'yt_notifi\yt_notifi_bootstrap.exe')
$checks.ytdownload_package=Test-Path (Join-Path $InstallRoot 'ytdownload\YTDOWNLOAD.exe')
$checks.config=Test-Path (Join-Path $DataRoot 'config\.env')
$checks.state=Test-Path (Join-Path $DataRoot 'state')
$checks.logs=Test-Path (Join-Path $DataRoot 'logs')
foreach($item in @(@{Name='8787';Url='http://127.0.0.1:8787/health'},@{Name='8790';Url='http://127.0.0.1:8790/health'})){
  try{$null=Invoke-RestMethod $item.Url -TimeoutSec 3;$checks["health_$($item.Name)"]=$true}catch{$checks["health_$($item.Name)"]=$false}
}
$tasks=Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object TaskName -like 'ContentOps Client -*'
$checks.autostart=($tasks.Count -ge 3)
$checks.watchdog=($tasks.TaskName -contains 'ContentOps Client - Watchdog')
foreach($item in @(@{Name='8787';Port=8787},@{Name='8790';Port=8790})){
  $conn=Get-NetTCPConnection -LocalPort $item.Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  $checks["process_path_$($item.Name)"]=$false
  if($conn){try{$path=(Get-Process -Id $conn.OwningProcess -ErrorAction Stop).Path;$checks["process_path_$($item.Name)"]=$path.StartsWith($InstallRoot,[StringComparison]::OrdinalIgnoreCase)}catch{}}
}
if($ExerciseWatchdog){
  function Wait-Ready([int]$Port){1..30|%{try{$null=Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 1;return $true}catch{Start-Sleep 1}};return $false}
  $beforeYd=(Get-NetTCPConnection -LocalPort 8790 -State Listen -ErrorAction SilentlyContinue|Select -First 1 -Expand OwningProcess)
  $ytPid=(Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue|Select -First 1 -Expand OwningProcess)
  if($ytPid){Stop-Process -Id $ytPid -Force -ErrorAction SilentlyContinue}
  $checks.watchdog_yt_notifi=Wait-Ready 8787
  $afterYd=(Get-NetTCPConnection -LocalPort 8790 -State Listen -ErrorAction SilentlyContinue|Select -First 1 -Expand OwningProcess)
  $checks.ytdownload_preserved=([int]$beforeYd -eq [int]$afterYd)
  $ydPid=$afterYd; if($ydPid){Stop-Process -Id $ydPid -Force -ErrorAction SilentlyContinue}
  $checks.watchdog_ytdownload=Wait-Ready 8790
}
$checks.overall= -not ($checks.Values -contains $false)
$checks | ConvertTo-Json -Depth 5
if(-not $checks.overall){exit 2}
