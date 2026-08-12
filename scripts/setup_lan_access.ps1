$ErrorActionPreference = "Stop"
$ruleName = "YT_NOTIFI Dashboard LAN"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script as Administrator."
}
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Enabled True `
    -Profile Private -Protocol TCP -LocalPort 8787 -RemoteAddress LocalSubnet | Out-Null
Write-Host "Firewall rule ready: $ruleName (Private, TCP 8787, LocalSubnet)"
