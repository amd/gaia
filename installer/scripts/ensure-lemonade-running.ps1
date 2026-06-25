<#
.SYNOPSIS
    Ensure a healthy Lemonade server is running on $Port, persisting across CI jobs.

.DESCRIPTION
    GitHub Actions terminates any process a job spawns when the job ends, so a
    server started inline never survives to the next job. This launches the
    version-matched Lemonade server -- via the `lemonade-server` shim so it binds
    $Port, not the tray launcher's default -- as a Windows **Scheduled Task**
    instead: the Task Scheduler owns it, not the job, so it persists across jobs
    and reboots. The task also auto-starts at boot.

    Idempotent + self-healing:
      - If a healthy server is already on $Port, returns immediately.
      - Otherwise (re)registers + starts the task, retrying until healthy, then
        warms $WarmModel (pull) so the first test request isn't a cold load.

    Use -ForceRestart to recycle the server even if it's currently healthy.

.PARAMETER Port           Server port (default 13305).
.PARAMETER ServerExe      Path to LemonadeServer.exe (used to locate the sibling
                          `lemonade-server` shim). Defaults to LEMONADE_SERVER_PATH.
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

# Resolve the launcher. v10.x ships two server binaries side-by-side:
#   - LemonadeServer.exe  -- tray/launcher; ignores the legacy `serve --port`
#                            args, so it always binds its own default port.
#   - lemonade-server.exe -- shim that correctly translates `serve --no-tray
#                            --port N` into the new server invocation.
# We must launch the shim, or the server comes up on the wrong port and the
# health check below (on $Port) never passes -- see installer/scripts/start-lemonade.ps1.
if (-not $ServerExe -or -not (Test-Path $ServerExe)) {
    $ServerExe = (Get-ChildItem `
        "C:\Users\*\AppData\Local\lemonade_server\bin\LemonadeServer.exe", `
        "C:\windows\system32\config\systemprofile\AppData\Local\lemonade_server\bin\LemonadeServer.exe", `
        "C:\Program Files*\Lemonade Server\bin\LemonadeServer.exe" `
        -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
}

# The shim sits next to LemonadeServer.exe; fall back to PATH then the repo venv.
$shimNextToExe = if ($ServerExe) { Join-Path (Split-Path $ServerExe) "lemonade-server.exe" } else { $null }
$ServerShim = $null
foreach ($cand in @(
    $shimNextToExe,
    (Get-Command lemonade-server -ErrorAction SilentlyContinue).Source,
    ".\.venv\Scripts\lemonade-server.exe"
)) { if ($cand -and (Test-Path $cand)) { $ServerShim = (Resolve-Path $cand).Path; break } }
if (-not $ServerShim) {
    $ServerShim = (Get-ChildItem `
        "C:\Users\*\AppData\Local\lemonade_server\bin\lemonade-server.exe", `
        "C:\windows\system32\config\systemprofile\AppData\Local\lemonade_server\bin\lemonade-server.exe", `
        "C:\Program Files*\Lemonade Server\bin\lemonade-server.exe" `
        -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
}
if (-not $ServerShim) { Write-Host "ERROR: lemonade-server shim not found on the runner."; exit 1 }
Write-Host "Server shim: $ServerShim"

# (Re)register a Scheduled Task that runs the server as SYSTEM, auto-starting at
# boot and restarting on failure. Force overwrites any prior definition.
try {
    $action    = New-ScheduledTaskAction -Execute $ServerShim -Argument "serve --no-tray --port $Port"
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
