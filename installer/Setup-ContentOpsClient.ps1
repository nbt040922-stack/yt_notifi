param([string]$SourceRoot='',[string]$YtDownloadRoot='',[string]$InstallRoot=(Join-Path ${env:ProgramFiles} 'ContentOps\Client'),[string]$DataRoot=(Join-Path $env:ProgramData 'ContentOps\Client'))
$ErrorActionPreference='Stop'; New-Item -ItemType Directory -Force -Path $InstallRoot,$DataRoot | Out-Null
$repoRoot=if($SourceRoot){$SourceRoot}elseif(Test-Path (Join-Path $PSScriptRoot 'yt_notifi')){$PSScriptRoot}else{Split-Path $PSScriptRoot -Parent}
$ytSource=if(Test-Path (Join-Path $repoRoot 'yt_notifi')){Join-Path $repoRoot 'yt_notifi'}else{$repoRoot}
$ydSource=$null; if($YtDownloadRoot){$ydSource=Join-Path $YtDownloadRoot 'dist\win-unpacked'; if(-not(Test-Path $ydSource)){$ydSource=$YtDownloadRoot}}
if(($null -eq $ydSource -or -not(Test-Path $ydSource)) -and (Test-Path (Join-Path $PSScriptRoot 'ytdownload\YTDOWNLOAD.exe'))){$ydSource=Join-Path $PSScriptRoot 'ytdownload'}
foreach($pair in @(@{From=$ytSource;To=(Join-Path $InstallRoot 'yt_notifi')},@{From=$ydSource;To=(Join-Path $InstallRoot 'ytdownload')})){if(-not(Test-Path $pair.From)){throw "SOURCE_MISSING: $($pair.From)"}; New-Item -ItemType Directory -Force -Path $pair.To | Out-Null; robocopy $pair.From $pair.To /E /XD .git .venv build node_modules dist __pycache__ config state logs /XF '*.log' '*.pyc' | Out-Null; if($LASTEXITCODE -gt 7){throw 'COPY_FAILED'}}
$scriptDir=if(Test-Path (Join-Path $PSScriptRoot 'scripts')){Join-Path $PSScriptRoot 'scripts'}else{Join-Path (Split-Path $PSScriptRoot -Parent) 'installer\scripts'}
$scriptDest=Join-Path $InstallRoot 'scripts'
New-Item -ItemType Directory -Force -Path $scriptDest | Out-Null
Copy-Item (Join-Path $scriptDir '*.ps1') $scriptDest -Force
& (Join-Path $InstallRoot 'scripts\Repair-ContentOpsEnvironment.ps1') -Root $InstallRoot -DataRoot $DataRoot | Out-Null
# Autostart is owned by the Inno Setup Startup shortcut, matching the proven
# deployment. Do not register a second logon mechanism here.
$env:YT_NOTIFI_PACKAGED='1'; $env:YT_NOTIFI_DATA_DIR=$DataRoot
$bootstrap=Join-Path $InstallRoot 'yt_notifi\yt_notifi_bootstrap.exe'
if(-not(Test-Path $bootstrap)){throw 'YT_NOTIFI_BOOTSTRAP_MISSING'}
$bootstrapProcess=Start-Process -FilePath $bootstrap -WorkingDirectory (Split-Path $bootstrap) -PassThru
Set-Content -Path (Join-Path $DataRoot 'runtime\yt_notifi-bootstrap.pid') -Value $bootstrapProcess.Id -Encoding ASCII
$ready=$false
1..60 | ForEach-Object {
  if(-not $ready){ try { Invoke-RestMethod 'http://127.0.0.1:8787/health' -TimeoutSec 2 | Out-Null; Invoke-RestMethod 'http://127.0.0.1:8790/health' -TimeoutSec 2 | Out-Null; $ready=$true } catch { Start-Sleep -Seconds 1 } }
}
& (Join-Path $InstallRoot 'scripts\Status-ContentOpsClient.ps1') -Root $InstallRoot -DataRoot $DataRoot
if(-not $ready){throw "HEALTH_CHECK_FAILED: xem log tại $DataRoot\logs"}
