$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$yd = if($env:YTDOWNLOAD_ROOT){$env:YTDOWNLOAD_ROOT}else{Join-Path ([IO.Path]::GetPathRoot($root)) 'YTDOWNLOAD'}
$env:CONTENTOPS_HEADLESS='1'; $env:CONTENTOPS_BRIDGE_PORT='8790'
& (Join-Path $PSScriptRoot 'Watch-Service.ps1') -ServiceName 'ytdownload' `
  -FilePath (Join-Path $yd 'node_modules\electron\dist\electron.exe') -ArgumentList @('.') `
  -WorkingDirectory $yd -HealthUrl 'http://127.0.0.1:8790/health' `
  -LogDirectory (Join-Path $root 'logs\supervisors')
