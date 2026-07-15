# Stops the local Neo4j Community server started by start_neo4j.ps1.
# Usage:  .\stop_neo4j.ps1
$ErrorActionPreference = "SilentlyContinue"

$procs = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'neo4j-community' }

if (-not $procs) {
    Write-Host "Neo4j does not appear to be running." -ForegroundColor Yellow
    return
}

foreach ($p in $procs) {
    Stop-Process -Id $p.ProcessId -Force
    Write-Host "Stopped Neo4j process (PID $($p.ProcessId))." -ForegroundColor Green
}
