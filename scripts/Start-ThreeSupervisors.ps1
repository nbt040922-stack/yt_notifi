$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$log = Join-Path $root 'logs\supervisors'
New-Item -ItemType Directory -Force -Path $log | Out-Null
foreach($name in @('supervise_yt_notifi.ps1','supervise_ytdownload.ps1','supervise_silence.ps1')) {
    $script = Join-Path $PSScriptRoot $name
    Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',$script) `
      -WorkingDirectory $root -WindowStyle Hidden | Out-Null
}
Write-Host 'THREE_SUPERVISORS_STARTED'
