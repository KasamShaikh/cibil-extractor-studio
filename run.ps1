# CIBIL Extractor Studio - local launcher (Windows / PowerShell)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
}

$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt

Write-Host ""
Write-Host "CIBIL Extractor Studio is starting..." -ForegroundColor Green
Write-Host "Open http://127.0.0.1:8010 in your browser." -ForegroundColor Green
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

& $py -m uvicorn backend.app:app --host 127.0.0.1 --port 8010
