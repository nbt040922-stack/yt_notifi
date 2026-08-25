$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$silence = if($env:SILENCE_CUTTER_ROOT){$env:SILENCE_CUTTER_ROOT}else{Join-Path ([IO.Path]::GetPathRoot($root)) 'Silence_cutter'}
$py = Join-Path $silence '.venv_asr_test\Scripts\python.exe'
$env:SILENCE_CUTTER_DATA_DIR = Join-Path $silence 'data'
& (Join-Path $PSScriptRoot 'Watch-Service.ps1') -ServiceName 'silence' `
  -FilePath $py -ArgumentList @('contentops_process_bridge.py') -WorkingDirectory $silence `
  -HealthUrl 'http://127.0.0.1:8791/health' -LogDirectory (Join-Path $root 'logs\supervisors')
