$ErrorActionPreference = 'Continue'
$appRoot = $PSScriptRoot
if ((Split-Path -Leaf $appRoot) -ieq 'installer') {
  $appRoot = Split-Path -Parent $appRoot
}
$dataRoot = Join-Path $env:LOCALAPPDATA 'YT_NOTIFI'
$logDir = Join-Path $dataRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'launcher.log'
$exe = Join-Path $appRoot 'YT_NOTIFI\yt_notifi_bootstrap.exe'
$downloadExe = Join-Path $appRoot 'YTDOWNLOAD\YTDOWNLOAD.exe'
if (-not (Test-Path $exe)) { $exe = Join-Path $appRoot 'dist\yt_notifi_bootstrap\yt_notifi_bootstrap.exe' }
if (-not (Test-Path $downloadExe)) { $downloadExe = Join-Path $appRoot 'build\YTDOWNLOAD\YTDOWNLOAD.exe' }
$env:YT_NOTIFI_PACKAGED = '1'
$env:YT_NOTIFI_DATA_DIR = $dataRoot
$env:YT_NOTIFI_BIND_HOST = if ($env:YT_NOTIFI_BIND_HOST) { $env:YT_NOTIFI_BIND_HOST } else { '0.0.0.0' }
$env:CONTENTOPS_HEADLESS = '1'
$env:CONTENTOPS_BRIDGE_PORT = '8790'
$env:SILENCE_CUTTER_BRIDGE_URL = 'http://127.0.0.1:8791'
$env:SILENCE_CUTTER_LAN_URL = ''
$env:SILENCE_CUTTER_LAN_TOKEN = ''
$backoff = @(2, 5, 15, 30, 60)
$restartCount = 0
while ($true) {
  $started = Get-Date
  Add-Content $log "$(Get-Date -Format o) START ytdownload=$downloadExe yt_notifi=$exe"
  $download = Start-Process -FilePath $downloadExe -WorkingDirectory (Split-Path $downloadExe) -PassThru -WindowStyle Hidden
  $bridgeReady = $false
  1..60 | ForEach-Object {
    if (-not $bridgeReady) { try { $bridgeReady = (Invoke-WebRequest 'http://127.0.0.1:8790/health' -UseBasicParsing -TimeoutSec 1).StatusCode -eq 200 } catch {} }
    if (-not $bridgeReady) { Start-Sleep -Milliseconds 500 }
  }
  if (-not $bridgeReady) { Add-Content $log "$(Get-Date -Format o) YTDOWNLOAD_HEALTH_FAILED"; Stop-Process -Id $download.Id -Force -ErrorAction SilentlyContinue; $restartCount++; Start-Sleep -Seconds 5; continue }
  # The launcher owns YTDOWNLOAD; bootstrap runs only the YT_NOTIFI worker.
  # This preserves the proven old process topology and avoids a duplicate
  # YTDOWNLOAD supervisor inside the frozen backend.
  $p = Start-Process -FilePath $exe -ArgumentList @('--worker') -WorkingDirectory (Split-Path $exe) -PassThru -WindowStyle Hidden
  while ((Get-Process -Id $p.Id -ErrorAction SilentlyContinue) -and (Get-Process -Id $download.Id -ErrorAction SilentlyContinue)) { Start-Sleep -Seconds 2 }
  Add-Content $log "$(Get-Date -Format o) EXIT yt_notifi=$((Get-Process -Id $p.Id -ErrorAction SilentlyContinue) -eq $null) ytdownload=$((Get-Process -Id $download.Id -ErrorAction SilentlyContinue) -eq $null)"
  Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
  Stop-Process -Id $download.Id -Force -ErrorAction SilentlyContinue
  $restartCount++
  if ($restartCount -gt 10) { Add-Content $log "$(Get-Date -Format o) STOP bounded-restarts"; break }
  $delay = $backoff[[Math]::Min($restartCount - 1, $backoff.Count - 1)]
  Start-Sleep -Seconds $delay
}
