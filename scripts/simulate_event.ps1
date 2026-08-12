$ErrorActionPreference = "Stop"
$root = Join-Path $PSScriptRoot ".."
$uri = "http://127.0.0.1:8787/youtube/websub"
Invoke-RestMethod -Method Post -Uri $uri -ContentType "application/atom+xml" -InFile (Join-Path $root "tests/fixtures/youtube_event.xml")
