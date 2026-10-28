param([string]$Python = "$PSScriptRoot\.venv\Scripts\python.exe")
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed.' }
& $Python -m PyInstaller --noconfirm --windowed --onedir --name ModelDockChat --add-data "chatdock/translations.json:chatdock" --icon assets\modeldock-chat.ico --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
& $Python tools\prepare_release.py
if ($LASTEXITCODE -ne 0) { throw 'Preparing release failed.' }
$zipPath = Join-Path $PSScriptRoot 'dist\ModelDockChat-0.2.0-windows-x64.zip'
Compress-Archive -LiteralPath (Join-Path $PSScriptRoot 'dist\ModelDockChat') -DestinationPath $zipPath -Force
$digest = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
"$digest  ModelDockChat-0.2.0-windows-x64.zip" | Set-Content -LiteralPath "$zipPath.sha256" -Encoding ascii
Write-Host "Release ready: $zipPath"
