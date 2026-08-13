$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$taskName = "ContentOps Production"
$script = Join-Path $PSScriptRoot "start_production_hidden.vbs"
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = "`"$script`""
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument $arguments -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Startup task ready: $taskName"
