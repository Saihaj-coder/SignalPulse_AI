# Starts the local Neo4j Community server (portable install under runtime/).
# Usage:  .\start_neo4j.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$jdk = (Get-ChildItem -Directory (Join-Path $root "runtime\jdk") | Select-Object -First 1).FullName
$nj  = (Get-ChildItem -Directory (Join-Path $root "runtime\neo4j") | Select-Object -First 1).FullName
$env:JAVA_HOME = $jdk

# Already running?
$running = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'neo4j-community' }
if ($running) {
    Write-Host "Neo4j appears to already be running (PID $($running.ProcessId))." -ForegroundColor Yellow
    Write-Host "Browser: http://localhost:7474"
    return
}

Start-Process -FilePath "$nj\bin\neo4j.bat" -ArgumentList "console" -WindowStyle Hidden `
    -RedirectStandardOutput "$root\runtime\neo4j-out.log" `
    -RedirectStandardError  "$root\runtime\neo4j-err.log"

Write-Host "Neo4j is starting (takes ~20-30s)..." -ForegroundColor Green
Write-Host "  Browser : http://localhost:7474"
Write-Host "  Login   : neo4j / signalpulse123"
Write-Host "  Logs    : runtime\neo4j-out.log"
