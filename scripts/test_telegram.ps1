$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
python -m app.telegram
