# Run in PowerShell on 64-bit Windows with Python 3.13 installed.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
py -3.13 -m venv .venv-build
if ($LASTEXITCODE -ne 0) { throw 'Python 3.13 is required to build.' }
& .\.venv-build\Scripts\python.exe -m pip install -r requirements-windows-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& .\.venv-build\Scripts\python.exe packaging\prepare_ocr_assets.py
if ($LASTEXITCODE -ne 0) { throw 'OCR asset download or checksum verification failed.' }
$ocrPath = Join-Path (Get-Location) 'ocr_assets'
$staticPath = Join-Path (Get-Location) 'static'
& .\.venv-build\Scripts\python.exe -m PyInstaller --clean --noconfirm --onefile --console --name ExamGen --add-data "${staticPath};static" --add-data "${ocrPath};ocr_assets" --distpath dist\windows --workpath build\windows --specpath build desktop.py
if ($LASTEXITCODE -ne 0) { throw 'Windows executable build failed.' }
Write-Host 'Built dist\windows\ExamGen.exe. Double-click it to run.'
