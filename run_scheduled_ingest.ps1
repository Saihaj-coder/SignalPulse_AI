# SignalPulse AI — unattended ingest (Windows)
#
# Starts Neo4j if needed, waits until it is reachable, runs the pipeline,
# logs the result, then stops Neo4j only if this script started it and the
# chat console is not using it.
#
# Usage:
#   .\run_scheduled_ingest.ps1
#   .\run_scheduled_ingest.ps1 -IngestProfile weekly
#   .\run_scheduled_ingest.ps1 -IngestProfile weekly -BiWeekly
#   .\run_scheduled_ingest.ps1 -KeepNeo4jRunning
#   .\run_scheduled_ingest.ps1 -Force
#
# Register with Task Scheduler via:
#   .\register_scheduled_ingest.ps1

param(
    [ValidateSet("weekly", "demo", "full", "smoke")]
    [string]$IngestProfile = "weekly",

    [switch]$BiWeekly,
    [switch]$Force,
    [switch]$KeepNeo4jRunning,
    [int]$Neo4jWaitSeconds = 120,
    [int]$MinFreeGb = 5
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $root "data\processed"
$logFile = Join-Path $logDir "scheduled_ingest.log"
$stampFile = Join-Path $logDir "last_ingest.json"
$startedNeo4j = $false
$exitCode = 1

function Write-Log {
    param([string]$Message, [string]$Color = "White")
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line -ForegroundColor $Color
    Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
}

function Test-Neo4jRunning {
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "neo4j-community" }
    return [bool]$procs
}

function Test-ChatConsoleRunning {
    $conn = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
    return [bool]$conn
}

function Get-Neo4jDataSizeGb {
    $dataPath = Get-ChildItem -Directory (Join-Path $root "runtime\neo4j") -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $dataPath) { return $null }
    $dbPath = Join-Path $dataPath.FullName "data"
    if (-not (Test-Path $dbPath)) { return $null }
    $sum = (Get-ChildItem $dbPath -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    if ($null -eq $sum) { return 0 }
    return [math]::Round($sum / 1GB, 3)
}

try {
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }

    Write-Log "==== Scheduled ingest start (profile=$IngestProfile, biweekly=$BiWeekly) ====" "Cyan"

    if (-not (Test-Path $py)) {
        Write-Log "Virtualenv not found at .venv. Create it first (see README)." "Red"
        $exitCode = 1
        return
    }

    # --- Disk headroom (local Neo4j Community is disk-bound, not license-capped) ---
    $drive = (Get-Item $root).PSDrive.Name
    $freeGb = [math]::Round((Get-PSDrive $drive).Free / 1GB, 2)
    $neoSize = Get-Neo4jDataSizeGb
    Write-Log "Free disk on ${drive}: ${freeGb} GB; Neo4j data size: $(if ($null -eq $neoSize) { 'n/a' } else { "$neoSize GB" })"
    if ($freeGb -lt $MinFreeGb) {
        Write-Log "Aborting: free disk ($freeGb GB) is below minimum ($MinFreeGb GB)." "Red"
        $exitCode = 1
        return
    }

    # --- Bi-weekly gate (same pipeline profile; skip if last success was < 13 days ago) ---
    if ($BiWeekly -and (Test-Path $stampFile) -and -not $Force) {
        try {
            $stamp = Get-Content $stampFile -Raw | ConvertFrom-Json
            $finished = [datetime]$stamp.finished_at
            $ageDays = ((Get-Date).ToUniversalTime() - $finished.ToUniversalTime()).TotalDays
            if ($ageDays -lt 13) {
                Write-Log "Bi-weekly gate: last ingest was $([math]::Round($ageDays,1)) days ago (< 13). Skipping." "Yellow"
                $exitCode = 0
                return
            }
        } catch {
            Write-Log "Could not read last_ingest.json for bi-weekly gate; continuing. ($($_.Exception.Message))" "Yellow"
        }
    }

    # --- Neo4j lifecycle ---
    $alreadyRunning = Test-Neo4jRunning
    if (-not $alreadyRunning) {
        Write-Log "Starting Neo4j..."
        & (Join-Path $root "start_neo4j.ps1")
        $startedNeo4j = $true
    } else {
        Write-Log "Neo4j already running; leaving it as-is."
    }

    Write-Log "Waiting up to $Neo4jWaitSeconds s for Bolt connectivity..."
    $deadline = (Get-Date).AddSeconds($Neo4jWaitSeconds)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        & $py -c "from signalpulse import graph; graph.verify_connectivity()" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 5
    }
    if (-not $ready) {
        Write-Log "Neo4j did not become reachable in time." "Red"
        $exitCode = 1
        return
    }
    Write-Log "Neo4j: OK" "Green"

    # --- Pipeline ---
    $pipelineArgs = @("run_pipeline.py", "--profile", $IngestProfile)
    if ($Force) { $pipelineArgs += "--force" }

    Write-Log "Starting ingest profile='$IngestProfile' (LLM extraction can take a while)..." "Green"
    & $py @pipelineArgs
    $exitCode = $LASTEXITCODE
    if ($exitCode -eq 0) {
        Write-Log "Ingest finished successfully." "Green"
    } else {
        Write-Log "Ingest finished with exit code $exitCode." "Red"
    }
}
catch {
    $exitCode = 1
    Write-Log "Scheduled ingest failed: $($_.Exception.Message)" "Red"
}
finally {
    if ($startedNeo4j -and -not $KeepNeo4jRunning) {
        if (Test-ChatConsoleRunning) {
            Write-Log "Chat console is using port 8501; leaving Neo4j running." "Yellow"
        } else {
            Write-Log "Stopping Neo4j (started by this job)..."
            & (Join-Path $root "stop_neo4j.ps1")
        }
    } elseif ($startedNeo4j -and $KeepNeo4jRunning) {
        Write-Log "Keeping Neo4j running (-KeepNeo4jRunning)."
    }
    Write-Log "==== Scheduled ingest end (exit=$exitCode) ====" "Cyan"
}

exit $exitCode
