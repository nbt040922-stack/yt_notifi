$ErrorActionPreference = "Stop"
$taskName = "ContentOps Production"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}
Write-Host "Startup task removed: $taskName"
