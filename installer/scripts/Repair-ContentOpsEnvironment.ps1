param([string]$Root = (Join-Path ${env:ProgramFiles} 'ContentOps\Client'),[string]$DataRoot = (Join-Path $env:ProgramData 'ContentOps\Client'))
$ErrorActionPreference = 'Stop'
foreach($dir in 'config','state','logs','runtime'){New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot $dir) | Out-Null}
$envFile=Join-Path $DataRoot 'config\.env'
if(-not (Test-Path $envFile)){ @('# ContentOps Client local configuration','# Add Telegram credentials here; never commit this file') | Set-Content -Path $envFile -Encoding UTF8 }
& (Join-Path $PSScriptRoot 'Check-ContentOpsEnvironment.ps1') -Root $Root -DataRoot $DataRoot -AsJson
