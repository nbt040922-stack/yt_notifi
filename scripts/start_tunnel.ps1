$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

try {
    $health = Invoke-RestMethod "http://127.0.0.1:8787/health"
    if ($health.status -ne "ok" -or $health.service -ne "YT_NOTIFI") { throw "Unexpected health response" }
    Write-Host "LOCAL HEALTH    PASS"
} catch {
    Write-Host "LOCAL HEALTH    FAIL"
    throw "Start YT_NOTIFI first: .\scripts\run.ps1"
}

$configured = [Environment]::GetEnvironmentVariable("CLOUDFLARED_PATH")
if (-not $configured -and (Test-Path (Join-Path $root ".env"))) {
    $line = Get-Content (Join-Path $root ".env") | Where-Object { $_ -match '^CLOUDFLARED_PATH=' } | Select-Object -First 1
    if ($line) { $configured = $line.Substring("CLOUDFLARED_PATH=".Length).Trim().Trim('"') }
}
$projectBinary = Join-Path $root "tools\cloudflared.exe"
$command = Get-Command cloudflared -ErrorAction SilentlyContinue
$cloudflared = if ($configured) { $configured } elseif (Test-Path $projectBinary) { $projectBinary } elseif ($command) { $command.Source } else { $null }

if (-not $cloudflared -or -not (Test-Path $cloudflared)) {
    throw "cloudflared.exe not found. Install from Cloudflare, add it to PATH, place it at tools\cloudflared.exe, or set CLOUDFLARED_PATH. No binary was downloaded."
}

Write-Host "Starting free Cloudflare Quick Tunnel..."
Write-Host "Copy the generated https://*.trycloudflare.com URL into .env:"
Write-Host "PUBLIC_CALLBACK_URL=https://your-generated-host.trycloudflare.com"
Write-Host "Then run: .\scripts\test_public_callback.ps1"
Write-Host "Then run: .\scripts\subscribe.ps1"
& $cloudflared tunnel --url http://127.0.0.1:8787
