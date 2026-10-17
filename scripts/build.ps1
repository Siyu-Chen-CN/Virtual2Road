$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$manifestPath = Join-Path $projectRoot 'packaging\fit2road.manifest'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create .venv first: python -m venv .venv'
}

Push-Location $projectRoot
try {
    & $python -m pip install -r packaging\requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install build dependencies.' }
    & $python -m PyInstaller --noconfirm --onefile --windowed --noupx --name fit2road --manifest $manifestPath --specpath build src\fit2road_gui.py
    if ($LASTEXITCODE -ne 0) { throw 'Failed to build fit2road.exe.' }
    Write-Host "Built: $(Join-Path $projectRoot 'dist\fit2road.exe')"
}
finally {
    Pop-Location
}
