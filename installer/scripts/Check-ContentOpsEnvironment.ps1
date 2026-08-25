param(
  [string]$Root = (Join-Path ${env:ProgramFiles} 'ContentOps\Client'),
  [string]$DataRoot = (Join-Path $env:ProgramData 'ContentOps\Client'),
  [switch]$AsJson
)
$ErrorActionPreference = 'Stop'
$checks = [ordered]@{}
$checks.windows = ($IsWindows -or $env:OS -eq 'Windows_NT')
$checks.architecture = ([Environment]::Is64BitOperatingSystem)
$checks.contentops_runtime = Test-Path (Join-Path $Root 'yt_notifi\yt_notifi_bootstrap.exe')
$checks.ytdownload_runtime = Test-Path (Join-Path $Root 'ytdownload\YTDOWNLOAD.exe')
$checks.ytdlp_bundled = Test-Path (Join-Path $Root 'yt_notifi\tools\yt-dlp.exe')
$checks.ffmpeg_bundled = Test-Path (Join-Path $Root 'yt_notifi\tools\ffmpeg.exe')
$checks.ffprobe_bundled = Test-Path (Join-Path $Root 'yt_notifi\tools\ffprobe.exe')
foreach($port in 8787,8790){
  $checks["port_$port"] = -not [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}
foreach($dir in 'config','state','logs','runtime'){
  $path = Join-Path $DataRoot $dir
  New-Item -ItemType Directory -Force -Path $path | Out-Null
  $checks["writable_$dir"] = try { $probe=Join-Path $path '.write-test'; [IO.File]::WriteAllText($probe,'ok'); Remove-Item $probe -Force; $true } catch { $false }
}
$checks.ok = ($checks.windows -and $checks.architecture -and $checks.contentops_runtime -and $checks.ytdownload_runtime -and $checks.ytdlp_bundled -and $checks.ffmpeg_bundled -and $checks.ffprobe_bundled -and $checks.port_8787 -and $checks.port_8790 -and $checks.writable_config -and $checks.writable_state -and $checks.writable_logs -and $checks.writable_runtime)
if($AsJson){$checks | ConvertTo-Json -Depth 5}else{$checks.GetEnumerator() | Format-Table -AutoSize}
if(-not $checks.ok){ exit 2 }
