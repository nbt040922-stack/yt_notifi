Set-StrictMode -Version Latest

function Get-ProjectRoot([string]$ScriptDirectory) {
    return (Resolve-Path (Join-Path $ScriptDirectory "..")).Path
}

function Import-DotEnv([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
        $name, $value = $trimmed.Split("=", 2)
        if ($name -match '^[A-Za-z_][A-Za-z0-9_]*$') {
            [Environment]::SetEnvironmentVariable($name, $value.Trim().Trim('"').Trim("'"), "Process")
        }
    }
}

function Get-VenvPython([string]$Root) {
    $python = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Python virtual environment missing.`nRun setup first."
    }
    return (Resolve-Path -LiteralPath $python).Path
}

function Find-LauncherExecutable([string]$Configured, [string]$ProjectPath, [string]$CommandName) {
    if ($Configured -and (Test-Path -LiteralPath $Configured)) { return (Resolve-Path -LiteralPath $Configured).Path }
    if (Test-Path -LiteralPath $ProjectPath) { return (Resolve-Path -LiteralPath $ProjectPath).Path }
    $command = Get-Command $CommandName -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Assert-YtDlp([string]$Root) {
    $path = Find-LauncherExecutable $env:YTDLP_PATH (Join-Path $Root "tools\yt-dlp.exe") "yt-dlp"
    if (-not $path) {
        throw "yt-dlp is required for YT_NOTIFI polling.`nConfigure YTDLP_PATH or place yt-dlp.exe in tools\"
    }
    return $path
}

function Enter-LauncherMutex([string]$Name = "Local\YT_NOTIFI_LAUNCHER") {
    $mutex = [Threading.Mutex]::new($false, $Name)
    try {
        if ($mutex.WaitOne(0)) { return $mutex }
    } catch [Threading.AbandonedMutexException] {
        return $mutex
    }
    $mutex.Dispose()
    return $null
}

function Get-ProcessStartUtc([Diagnostics.Process]$Process) {
    return $Process.StartTime.ToUniversalTime().ToString("o")
}

function Test-RuntimeProcess([int]$ProcessId, [string]$StartedAt, [string]$CommandMarker) {
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        $actualStart = $process.StartTime.ToUniversalTime()
        $expectedStart = [DateTime]::Parse($StartedAt).ToUniversalTime()
        if ([Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -gt 2) { return $false }
        $info = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        return [bool]($info.CommandLine -and $info.CommandLine.Contains($CommandMarker))
    } catch {
        return $false
    }
}

function Write-RuntimeState([string]$Path, [object]$State) {
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$Path.tmp"
    $State | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Read-RuntimeState([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json } catch { return $null }
}

function Remove-RuntimeState([string]$Path) {
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath "$Path.tmp" -Force -ErrorAction SilentlyContinue
}

function Get-RuntimeStatus([string]$Path) {
    $runtime = Read-RuntimeState $Path
    if (-not $runtime) { return [pscustomobject]@{ Status = "STOPPED"; Runtime = $null } }
    $running = Test-RuntimeProcess ([int]$runtime.launcher_pid) ([string]$runtime.launcher_started_at) "start_all.ps1"
    return [pscustomobject]@{ Status = $(if ($running) { "RUNNING" } else { "STALE" }); Runtime = $runtime }
}

function Wait-LocalHealth(
    [int]$WatcherProcessId,
    [int]$TimeoutSeconds = 30,
    [scriptblock]$IsRunning = { param($id) [bool](Get-Process -Id $id -ErrorAction SilentlyContinue) },
    [scriptblock]$HealthProbe = {
        $health = Invoke-RestMethod "http://127.0.0.1:8787/health" -TimeoutSec 2
        return $health.status -eq "ok" -and $health.service -eq "YT_NOTIFI"
    }
) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        if (-not (& $IsRunning $WatcherProcessId)) { return "EXITED" }
        try {
            if (& $HealthProbe) {
                Start-Sleep -Milliseconds 250
                return $(if (& $IsRunning $WatcherProcessId) { "OK" } else { "EXITED" })
            }
        } catch {}
        Start-Sleep -Milliseconds 250
    }
    return "TIMEOUT"
}

function Protect-LogText([string]$Text) {
    foreach ($secret in @($env:TELEGRAM_BOT_TOKEN, $env:TELEGRAM_CHAT_ID)) {
        if ($secret) { $Text = $Text.Replace($secret, "[REDACTED]") }
    }
    return $Text
}

function Write-LauncherLog([string]$Path, [string]$Message) {
    $safe = Protect-LogText $Message
    Add-Content -LiteralPath $Path -Encoding UTF8 -Value "$(Get-Date -Format o) $safe"
}

function Stop-OwnedProcess([int]$ProcessId, [string]$StartedAt, [string]$Marker, [switch]$WhatIf) {
    if (-not (Test-RuntimeProcess $ProcessId $StartedAt $Marker)) { return $false }
    if ($WhatIf) { return $true }
    Stop-Process -Id $ProcessId -ErrorAction SilentlyContinue
    try { (Get-Process -Id $ProcessId -ErrorAction Stop).WaitForExit(5000) } catch { return $true }
    if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
    return $true
}
