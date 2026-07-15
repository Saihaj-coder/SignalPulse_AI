# SignalPulse AI — launch the enterprise web console (Windows)
#
#   .\start_neo4j.ps1
#   .\run_chat.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "Virtualenv not found. Create .venv and install requirements first."
}

Write-Host "Starting SignalPulse AI console..." -ForegroundColor Cyan
Write-Host " Neo4j should already be running (.\start_neo4j.ps1)" -ForegroundColor DarkGray
Write-Host " Open http://localhost:8501 when ready" -ForegroundColor DarkGray
& $py -m uvicorn webapp:app --host 127.0.0.1 --port 8501 --reload
