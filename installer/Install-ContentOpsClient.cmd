@echo off
setlocal
set "STAGE=%ProgramData%\ContentOps\ClientPackage"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%~dp0ContentOpsClient.zip' -DestinationPath '%STAGE%' -Force"
if errorlevel 1 exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%STAGE%\Setup-ContentOpsClient.ps1" -SourceRoot "%STAGE%"
exit /b %errorlevel%
