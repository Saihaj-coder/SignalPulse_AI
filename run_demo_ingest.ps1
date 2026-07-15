# SignalPulse AI — demo / weekly ingest refresh (Windows)
# Usage:
#   .\run_demo_ingest.ps1              # --profile demo
#   .\run_demo_ingest.ps1 -Weekly      # --profile weekly (same depth; for scheduled habit)
#   .\run_demo_ingest.ps1 -Force       # reprocess even unchanged docs
#
# Prerequisite: Neo4j must be running (.\start_neo4j.ps1).

param(
    [switch]$Weekly,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Virtualenv not found at .venv. Create it first (see README)." -ForegroundColor Red
    exit 1
}

# Quick Neo4j check
& $py -c "from signalpulse import graph; graph.verify_connectivity(); print('Neo4j: OK')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Neo4j is not reachable. Run .\start_neo4j.ps1 and wait ~30s." -ForegroundColor Red
    exit 1
}

$profile = if ($Weekly) { "weekly" } else { "demo" }
$args = @("run_pipeline.py", "--profile", $profile)
if ($Force) { $args += "--force" }

Write-Host "Starting ingest profile='$profile' (this can take a while — LLM extraction)..." -ForegroundColor Green
& $py @args
exit $LASTEXITCODE
