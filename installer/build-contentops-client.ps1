param([string]$Output=(Join-Path (Split-Path $PSScriptRoot -Parent) 'dist\ContentOpsClient'))
$ErrorActionPreference='Stop'; $root=Split-Path $PSScriptRoot -Parent; $yd='D:\YTDOWNLOAD'; if(-not(Test-Path $yd)){throw 'YTDOWNLOAD root missing'}
Remove-Item $Output -Recurse -Force -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Force -Path $Output | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Output 'yt_notifi'),(Join-Path $Output 'ytdownload') | Out-Null
$ydPortable=Join-Path $yd 'dist\YTDOWNLOAD 1.0.0.exe'
if(Test-Path $ydPortable){
  Copy-Item $ydPortable (Join-Path $Output 'ytdownload\YTDOWNLOAD.exe') -Force
}else{
  robocopy (Join-Path $yd 'dist\win-unpacked') (Join-Path $Output 'ytdownload') /E /XD .git node_modules dist test /XF '*.log' | Out-Null
  if($LASTEXITCODE -gt 7){throw 'YTDOWNLOAD copy failed'}
}
$pyOut=Join-Path $Output 'yt_notifi_build'
& python -m PyInstaller --noconfirm --clean --onedir --name yt_notifi_bootstrap --distpath $pyOut --workpath (Join-Path $Output 'pyinstaller-work') --specpath (Join-Path $Output 'pyinstaller-spec') --paths $root --collect-submodules app --add-data "$root\app\dashboard.html;app" --add-data "$root\app\setup.html;app" --add-data "$root\config;config" --add-binary "$yd\dist\win-unpacked\resources\bin\fallback\yt-dlp.exe;tools" --add-binary "$yd\dist\win-unpacked\resources\bin\fallback\ffmpeg.exe;tools" --add-binary "C:\Users\nbt04\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffprobe.exe;tools" (Join-Path $PSScriptRoot 'yt_notifi_bootstrap.py')
if($LASTEXITCODE){throw 'YT_NOTIFI_FREEZE_FAILED'}
robocopy (Join-Path $pyOut 'yt_notifi_bootstrap') (Join-Path $Output 'yt_notifi') /E | Out-Null
if($LASTEXITCODE -gt 7){throw 'YT_NOTIFI_RUNTIME_COPY_FAILED'}
New-Item -ItemType Directory -Force -Path (Join-Path $Output 'yt_notifi\tools') | Out-Null
Copy-Item (Join-Path $yd 'dist\win-unpacked\resources\bin\fallback\yt-dlp.exe'),(Join-Path $yd 'dist\win-unpacked\resources\bin\fallback\ffmpeg.exe'), 'C:\Users\nbt04\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffprobe.exe' (Join-Path $Output 'yt_notifi\tools') -Force
Remove-Item $pyOut,(Join-Path $Output 'pyinstaller-work'),(Join-Path $Output 'pyinstaller-spec') -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $PSScriptRoot 'Setup-ContentOpsClient.ps1'),(Join-Path $PSScriptRoot 'Update-ContentOpsClient.ps1'),(Join-Path $PSScriptRoot 'Uninstall-ContentOpsClient.ps1') $Output -Force
Copy-Item (Join-Path $PSScriptRoot 'Start-ContentOpsClient.ps1'),(Join-Path $PSScriptRoot 'Stop-ContentOpsClient.ps1'),(Join-Path $PSScriptRoot 'Restart-ContentOpsClient.ps1'),(Join-Path $PSScriptRoot 'Status-ContentOpsClient.ps1') $Output -Force
Copy-Item (Join-Path $PSScriptRoot 'README-INSTALL.txt') $Output -Force
Copy-Item (Join-Path $PSScriptRoot 'Test-ContentOpsClientAcceptance.ps1') $Output -Force
Copy-Item (Join-Path $PSScriptRoot 'scripts') (Join-Path $Output 'scripts') -Recurse -Force
Copy-Item (Join-Path $PSScriptRoot 'client-manifest.json') $Output -Force
$ytSha=(git -C $root rev-parse HEAD).Trim(); $ydSha=(git -C $yd rev-parse HEAD).Trim()
@{YT_NOTIFI=$ytSha;YTDOWNLOAD=$ydSha;built_at=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $Output 'build-identity.json') -Encoding UTF8
@('yt_notifi\yt_notifi_bootstrap.exe','ytdownload\YTDOWNLOAD.exe','yt_notifi\tools\yt-dlp.exe','yt_notifi\tools\ffmpeg.exe','yt_notifi\tools\ffprobe.exe') | ForEach-Object { Get-FileHash (Join-Path $Output $_) -Algorithm SHA256 } | ForEach-Object { '{0}  {1}' -f $_.Hash,($_.Path.Substring($Output.Length+1).Replace('\','/')) } | Set-Content (Join-Path $Output 'SHA256SUMS.txt') -Encoding ASCII
Compress-Archive -Path (Join-Path $Output '*') -DestinationPath "$Output.zip" -Force
Write-Output "PACKAGE_READY=$Output.zip"
