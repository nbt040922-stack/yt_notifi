$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$build = Join-Path $root 'build\installer-temp'
$dist = Join-Path $root 'dist\installer-temp'
Remove-Item -Recurse -Force $build -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $dist -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $build, $dist | Out-Null
$ytdownloadSource = 'D:\YTDOWNLOAD\dist\YTDOWNLOAD 1.0.0.exe'
if (-not (Test-Path $ytdownloadSource)) { throw "Chưa có artifact YTDOWNLOAD: $ytdownloadSource" }
$python = (Get-Command python -ErrorAction Stop).Source
$pyinstaller = (Get-Command pyinstaller -ErrorAction SilentlyContinue).Source
if (-not $pyinstaller) { $pyinstaller = Join-Path (Split-Path $python) 'Scripts\pyinstaller.exe' }
if (-not (Test-Path $pyinstaller)) { throw 'Chưa có PyInstaller. Cài trong môi trường phát triển rồi chạy lại.' }
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe' }
if (-not (Test-Path $iscc)) { $iscc = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe' }
if (-not (Test-Path $iscc)) { throw 'Chưa có Inno Setup 6 (ISCC.exe).' }
Push-Location $root
try {
  & $pyinstaller --noconfirm --clean --onedir --name yt_notifi_bootstrap --distpath $dist --workpath $build `
    --paths $root --hidden-import app.main --collect-submodules app `
    --add-data "app;app" --add-data "config;config" --add-data "tools;tools" --add-data "scripts;scripts" installer\yt_notifi_bootstrap.py
  if ($LASTEXITCODE) { throw "PyInstaller thất bại ($LASTEXITCODE)" }
  $bootstrapSource = Join-Path $dist 'yt_notifi_bootstrap'
  & $iscc "/DYTDOWNLOAD_SOURCE=$ytdownloadSource" "/DBOOTSTRAP_SOURCE=$bootstrapSource" (Join-Path $PSScriptRoot 'installer.iss')
  if ($LASTEXITCODE) { throw "Inno Setup thất bại ($LASTEXITCODE)" }
  Write-Host "Đã tạo $dist\YT_NOTIFI_Setup.exe"
} finally { Pop-Location }
