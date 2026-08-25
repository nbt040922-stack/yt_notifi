param([string]$Root = (Join-Path ${env:ProgramFiles} 'ContentOps\Client'),[string]$DataRoot = (Join-Path $env:ProgramData 'ContentOps\Client'))
& (Join-Path $PSScriptRoot 'Stop-ContentOpsClient.ps1') -Root $Root -DataRoot $DataRoot; Start-Sleep 2; & (Join-Path $PSScriptRoot 'Start-ContentOpsClient.ps1') -Root $Root -DataRoot $DataRoot
