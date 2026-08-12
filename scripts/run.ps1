$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
