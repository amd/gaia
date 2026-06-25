<#
.SYNOPSIS
    Ensure a healthy Lemonade server is running on $Port, persisting across CI jobs.

.DESCRIPTION
    GitHub Actions terminates any process a job spawns when the job ends, so a
    server started inline never survives to the next job. This launches
    LemonadeServer.exe with `--port $Port` (so it binds $Port regardless of a
    stale config.json) as a Windows **Scheduled Task** instead: the Task Scheduler
    owns it, not the job, so it persists across jobs and reboots. The task also
    auto-starts at boot.

    Idempotent + self-healing:
      - If a healthy server is already on $Port, returns immediately.
      - Otherwise (re)registers + starts the task, retrying until healthy, then
        warms $WarmModel (pull) so the first test request isn't a cold load.

    Use -ForceRestart to recycle the server even if it's currently healthy.

.PARAMETER Port           Server port (default 13305).
.PARAMETER ServerExe      Path to LemonadeServer.exe. Defaults to LEMONADE_SERVER_PATH.
.PARAMETER WarmModel      Model to pull so the backend is warm (default Gemma-4-E4B-it-GGUF).
.PARAMETER ForceRestart   Restart the task even if the server is already healthy.
#>
[CmdletBinding()]
param(
    [int]$Port = 13305,
    [string]$ServerExe = $env:LEMONADE_SERVER_PATH,
    [string]$WarmModel = "Gemma-4-E4B-it-GGUF",
    [switch]$ForceRestart
)

$ErrorActionPreference = "Continue"
$TaskName = "GaiaLemonadeServer"
function Test-Health {
    try { Invoke-RestMethod "http://localhost:$Port/api/v1/health" -TimeoutSec 5 | Out-Null; return $true }
    catch { return $false }
}

if (-not $ForceRestart -and (Test-Health)) {
    Write-Host "Lemonade already healthy on port $Port -- reusing persistent server."
    exit 0
}

# Resolve the server binary. In v10.x the server is LemonadeServer.exe; the
# legacy `lemonade-server` shim was deprecated in v10.5 and is no longer
# installed, so we launch LemonadeServer.exe directly. There is no `serve`
# subcommand -- the listen port lives in config.json, and a stale config in this
# profile (e.g. a pre-v10.1 default of 8000) is what pins the server to the wrong
# port. We pass `--port` to override it AND clear the stale config so the 13305
# default regenerates. Cross-check installer/scripts/start-lemonade.ps1.
if (-not $ServerExe -or -not (Test-Path $ServerExe)) {
    $ServerExe = (Get-ChildItem `
        "C:\Users\*\AppData\Local\lemonade_server\bin\LemonadeServer.exe", `
        "C:\windows\system32\config\systemprofile\AppData\Local\lemonade_server\bin\LemonadeServer.exe", `
        "C:\Program Files*\Lemonade Server\bin\LemonadeServer.exe" `
        -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
}
if (-not $ServerExe) { Write-Host "ERROR: LemonadeServer.exe not found on the runner."; exit 1 }
Write-Host "Server binary: $ServerExe"

# Clear any stale config.json so it can't keep pinning the server to an old port.
# The SYSTEM scheduled task reads the SYSTEM profile cache. --port below also
# overrides, but a clean config is the documented path back to the 13305 default.
# Best-effort: missing files are expected and fine.
foreach ($cfg in @(
    "C:\windows\system32\config\systemprofile\.cache\lemonade\config.json",
    "C:\windows\system32\config\systemprofile\AppData\Local\lemonade_server\config.json"
)) {
    if (Test-Path $cfg) { Write-Host "Removing stale config: $cfg"; Remove-Item $cfg -Force -ErrorAction SilentlyContinue }
}

# (Re)register a Scheduled Task that runs the server as SYSTEM, auto-starting at
# boot and restarting on failure. Force overwrites any prior definition.
try {
    $action    = New-ScheduledTaskAction -Execute $ServerExe -Argument "--port $Port"
    $trigger   = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
    Write-Host "Registered scheduled task '$TaskName'."
} catch {
    Write-Host "ERROR: could not register scheduled task (need admin?): $($_.Exception.Message)"
    exit 1
}

# Start the task, retrying until the server answers health checks. Each retry
# fully stops the task + any stray process so a wedged start can't block the port.
$healthy = $false
for ($attempt = 1; $attempt -le 4 -and -not $healthy; $attempt++) {
    Write-Host "--- start attempt $attempt ---"
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Get-Process LemonadeServer, lemonade-server, lemonade, llama-server, llama-server-dev, lemonade-server-dev `
        -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 4
    Start-ScheduledTask -TaskName $TaskName
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 3
        if (Test-Health) { $healthy = $true; Write-Host "Healthy after attempt $attempt (~$((($i+1)*3))s)."; break }
    }
    if (-not $healthy) { Write-Host "attempt $attempt did not become healthy in 60s" }
}
if (-not $healthy) { Write-Host "ERROR: Lemonade server not healthy on $Port after retries."; exit 1 }

# Warm the model so the first inference isn't a cold pull+load.
Write-Host "Warming model: $WarmModel"
try {
    $b = @{ model_name = $WarmModel } | ConvertTo-Json
    Invoke-RestMethod "http://localhost:$Port/api/v1/pull" -Method POST -ContentType "application/json" -Body $b -TimeoutSec 1200 | Out-Null
    Write-Host "Pulled $WarmModel."
} catch { Write-Host "WARN: warm pull failed: $($_.Exception.Message)" }

Write-Host "Lemonade ready on port $Port (persistent task '$TaskName')."
exit 0
