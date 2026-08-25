param([string]$Root = (Join-Path ${env:ProgramFiles} 'ContentOps\Client'),[string]$DataRoot = (Join-Path $env:ProgramData 'ContentOps\Client'),[switch]$Interactive)
$ErrorActionPreference='Stop'; & (Join-Path $PSScriptRoot 'Repair-ContentOpsEnvironment.ps1') -Root $Root -DataRoot $DataRoot | Out-Null
$runtime=Join-Path $DataRoot 'runtime'; $log=Join-Path $DataRoot 'logs'
function Start-Owned($name,$exe,$args,$wd,$port,[switch]$Visible){
  $pidFile=Join-Path $runtime "$name.pid"; if(Test-Path $pidFile){$old=[int](Get-Content $pidFile); if(Get-Process -Id $old -ErrorAction SilentlyContinue){return}}
  $startArgs=@{FilePath=$exe;WorkingDirectory=$wd;RedirectStandardOutput=(Join-Path $log "$name.stdout.log");RedirectStandardError=(Join-Path $log "$name.stderr.log");PassThru=$true}
  if($args -and $args.Count -gt 0){$startArgs.ArgumentList=$args}
  if(-not $Visible){$startArgs.WindowStyle='Hidden'}
  $p=Start-Process @startArgs
  Set-Content -Path $pidFile -Value $p.Id -Encoding ASCII
}
$yt=Join-Path $Root 'yt_notifi'; $yd=Join-Path $Root 'ytdownload'
$electron=Join-Path $yd 'YTDOWNLOAD.exe'
if(-not (Test-Path $electron)){throw 'YTDOWNLOAD_RUNTIME_MISSING'}
$ytExe=Join-Path $yt 'yt_notifi_bootstrap.exe'; if(-not (Test-Path $ytExe)){throw 'YT_NOTIFI_BOOTSTRAP_MISSING'}
$env:YT_NOTIFI_PACKAGED='1'; $env:YT_NOTIFI_DATA_DIR=$DataRoot
Start-Owned 'yt_notifi' $ytExe @() $yt 8787 -Visible:$Interactive
Start-Owned 'ytdownload' $electron @('.') $yd 8790
