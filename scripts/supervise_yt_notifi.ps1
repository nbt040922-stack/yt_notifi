$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
& (Join-Path $PSScriptRoot 'Watch-Service.ps1') -ServiceName 'yt_notifi' `
  -FilePath (Join-Path $root '.venv\Scripts\python.exe') `
  -ArgumentList @('-m','uvicorn','app.main:app','--app-dir',$root,'--host','127.0.0.1','--port','8787') `
  -WorkingDirectory $root -HealthUrl 'http://127.0.0.1:8787/health' `
  -LogDirectory (Join-Path $root 'logs\supervisors')
