param(
    [Parameter(Mandatory)][string]$ServiceName,
    [Parameter(Mandatory)][string]$FilePath,
    [string[]]$ArgumentList = @(),
    [Parameter(Mandatory)][string]$WorkingDirectory,
    [Parameter(Mandatory)][string]$HealthUrl,
    [Parameter(Mandatory)][string]$LogDirectory,
    [int]$HealthTimeoutSeconds = 90,
    [int]$RestartDelaySeconds = 2
)

$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null
$log = Join-Path $LogDirectory "$ServiceName-supervisor.log"
$stdout = Join-Path $LogDirectory "$ServiceName.stdout.log"
$stderr = Join-Path $LogDirectory "$ServiceName.stderr.log"

function Log([string]$message) { Add-Content -LiteralPath $log -Value "$(Get-Date -Format o) $message" }
function Stop-Tree([int]$pid) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$pid" -ErrorAction SilentlyContinue
    foreach($child in $children){ Stop-Tree ([int]$child.ProcessId) }
    Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
}
function Ready {
    try {
        $response = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 3
        return $response.status -in @('READY','ok')
    } catch { return $false }
}

while ($true) {
    $process = $null
    try {
        Log "START file=$FilePath health=$HealthUrl"
        $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory `
            -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
        $deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
        while((Get-Date) -lt $deadline -and -not (Ready)) {
            if($process.HasExited){ throw "PROCESS_EXITED_BEFORE_READY:$($process.ExitCode)" }
            Start-Sleep -Seconds 1
        }
        if(-not (Ready)){ throw 'HEALTH_TIMEOUT' }
        Log "READY pid=$($process.Id)"
        while(-not $process.HasExited) {
            Start-Sleep -Seconds 2
            if(-not (Ready)){ throw 'HEALTH_FAILED' }
        }
        Log "EXIT code=$($process.ExitCode)"
    } catch {
        Log "RESTART reason=$($_.Exception.Message)"
    } finally {
        if($process -and -not $process.HasExited){ Stop-Tree $process.Id }
    }
    Start-Sleep -Seconds $RestartDelaySeconds
}
