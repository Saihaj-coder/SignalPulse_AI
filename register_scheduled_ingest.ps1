# SignalPulse AI - register / unregister Windows Task Scheduler job
#
# Usage:
#   .\register_scheduled_ingest.ps1
#   .\register_scheduled_ingest.ps1 -Cadence Weekly -Time 03:00
#   .\register_scheduled_ingest.ps1 -Cadence BiWeekly -Time 03:00
#   .\register_scheduled_ingest.ps1 -TrialMonths 3      # auto-expires after 3 months
#   .\register_scheduled_ingest.ps1 -TrialMonths 0      # run indefinitely (no end date)
#   .\register_scheduled_ingest.ps1 -Unregister
#
# Notes:
# - Your laptop must be able to run at the scheduled time (on, or wake-from-sleep).
# - Fully shut down = job will not run.
# - Task is registered with WakeToRun so sleep can work if the PC is allowed to wake.
# - By default this is a TRIAL: the task stops firing after -TrialMonths and then
#   deletes itself. Extend or make permanent by re-running with a new value (or 0).

param(
    [ValidateSet("Weekly", "BiWeekly")]
    [string]$Cadence = "Weekly",

    [string]$Time = "13:00",

    [ValidateSet("weekly", "demo", "full", "smoke")]
    [string]$IngestProfile = "weekly",

    # Length of the trial window in months. 0 = no end date (runs indefinitely).
    [int]$TrialMonths = 3,

    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$taskName = "SignalPulseAI_ScheduledIngest"
$runner = Join-Path $root "run_scheduled_ingest.ps1"

if (-not (Test-Path $runner)) {
    Write-Error "Missing $runner"
}

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Unregistered scheduled task '$taskName' (if it existed)." -ForegroundColor Green
    exit 0
}

$argList = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`" -IngestProfile $IngestProfile"
if ($Cadence -eq "BiWeekly") {
    $argList += " -BiWeekly"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $argList `
    -WorkingDirectory $root

# Weekly Sunday trigger; bi-weekly cadence is enforced inside the runner via last_ingest.json.
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At $Time

# Trial window: stop firing after N months, then let Windows delete the task.
$endText = "never (runs indefinitely)"
if ($TrialMonths -gt 0) {
    $endDate = (Get-Date).AddMonths($TrialMonths)
    $trigger.EndBoundary = $endDate.ToString("s")
    $endText = $endDate.ToString("yyyy-MM-dd")
}

$settingsParams = @{
    AllowStartIfOnBatteries    = $true
    DontStopIfGoingOnBatteries = $true
    StartWhenAvailable         = $true
    WakeToRun                  = $true
    MultipleInstances          = "IgnoreNew"
}
# Auto-remove the task shortly after its trial end date passes.
if ($TrialMonths -gt 0) {
    $settingsParams["DeleteExpiredTaskAfter"] = (New-TimeSpan -Days 1)
}
$settings = New-ScheduledTaskSettingsSet @settingsParams

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "SignalPulse AI: start Neo4j if needed, run $IngestProfile ingest, stop Neo4j when safe." |
    Out-Null

Write-Host "Registered scheduled task '$taskName'." -ForegroundColor Green
Write-Host "  Cadence : $Cadence (Sunday at $Time; bi-weekly skips if last success was under 13 days ago)"
Write-Host "  Profile : $IngestProfile"
Write-Host "  Trial   : ends $endText$(if ($TrialMonths -gt 0) { ' (task self-deletes after expiry)' })"
Write-Host "  Script  : $runner"
Write-Host "  Wake    : enabled (WakeToRun) - laptop must not be fully shut down"
Write-Host ""
Write-Host "Manage in: Task Scheduler -> Task Scheduler Library -> $taskName"
Write-Host "Extend/permanent: re-run with -TrialMonths <n> (use 0 for no end date)"
Write-Host "Unregister now  : .\register_scheduled_ingest.ps1 -Unregister"
