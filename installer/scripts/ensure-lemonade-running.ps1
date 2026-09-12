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

.NOTES
    Concurrency caveat: the drift restart below (Test-TaskCurrent returning
    $false) stops and restarts the server on this box. Only three of the five
    workflows that call this script share the serial `lemonade-eval`
    concurrency group; test_agent_sdk.yml and test_gaia_cli_windows.yml use
    per-branch concurrency groups instead and can run at the same time as an
    eval job on the same runner. A version bump here can therefore have the
    first job that observes it kill the server out from under a concurrently
    running eval on one of those two workflows. Coordinating that (a
    machine-wide mutex, or moving those two workflows into the shared slot)
    was judged not worth doing for this fix -- flagging it so the next
    version bump isn't a surprise.
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
# Bump whenever the task ACTION below changes (launch environment, args,
# redirects). The health fast-path reuses whatever the Task Scheduler started
# from the definition registered LAST time, so without this marker an edit to
# the action never reaches a runner whose server is already up -- it waits for a
# reboot or an explicit -ForceRestart. That is why the stdout/stderr redirect
# added for #3015 only ever applied on the workflows that pass -ForceRestart.
$TaskActionVersion = "2026-09-coopmat"

# Fixed ProgramData path (SYSTEM-writable): the task outlives the job that
# registered it, so its log -- and the version marker below -- have to outlive
# that job too.
$LogDir = "C:\ProgramData\GaiaLemonadeServer"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$StdoutLog = Join-Path $LogDir "lemonade-task-stdout.log"
$StderrLog = Join-Path $LogDir "lemonade-task-stderr.log"
# Written only after a server launched from this action version is confirmed
# healthy (see the end of the script). The registered task ACTION and this
# file can otherwise drift apart -- e.g. a job cancelled between
# Register-ScheduledTask and the restart loop -- so Test-TaskCurrent requires
# BOTH to agree before trusting a running server's launch environment.
$VersionMarkerFile = Join-Path $LogDir "task-version.txt"

function Test-Health {
    try { Invoke-RestMethod "http://localhost:$Port/api/v1/health" -TimeoutSec 5 | Out-Null; return $true }
    catch { return $false }
}

# What action version does the REGISTERED task claim, if any?
function Get-RegisteredTaskVersion {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { return $null }
    foreach ($a in @($task.Actions)) {
        if ($a.Arguments -match "GAIA_LEMONADE_TASK_VERSION='([^']*)'") { return $Matches[1] }
    }
    return $null
}

# Is the registered task action AND the confirmed-healthy marker both the
# version this script writes today? (See $VersionMarkerFile above for why
# both are required.)
function Test-TaskCurrent {
    $registered = Get-RegisteredTaskVersion
    if ($registered -ne $TaskActionVersion) { return $false }
    if (-not (Test-Path $VersionMarkerFile)) { return $false }
    return (Get-Content $VersionMarkerFile -Raw).Trim() -eq $TaskActionVersion
}

$DriftRestart = $false
if (-not $ForceRestart -and (Test-Health)) {
    if (Test-TaskCurrent) {
        Write-Host "Lemonade already healthy on port $Port -- reusing persistent server."
        exit 0
    }
    $foundVersion = Get-RegisteredTaskVersion
    $foundLabel = if ($foundVersion) { "'$foundVersion'" } else { "none" }
    Write-Host "Lemonade is healthy on port $Port but its scheduled task action marker is $foundLabel (expected '$TaskActionVersion') -- re-registering and restarting so the current launch environment applies."
    $DriftRestart = $true
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
#
# Wrapped in powershell.exe + Start-Process only to capture output: a Task
# Scheduler action has no console and the scheduler records its stdout/stderr
# nowhere, so a bare -Execute discards the server's output entirely. This
# captures the SERVER's streams; whether llama-server's own stderr rides on them
# or goes to a pipe Lemonade owns is unconfirmed, so do not rely on this alone
# to explain a child that will not spawn.
# ($LogDir / $StdoutLog / $StderrLog are set up near the top of the script,
# alongside $VersionMarkerFile, since Test-TaskCurrent needs them before this
# point is reached.)
# PYTHONUNBUFFERED: Python block-buffers stdout when it is a pipe rather than a
# console, so without this a server that never exits never flushes and its stdout
# log stays empty. Measured: stdout 0 bytes without it, 39 with; stderr arrives
# either way (Python line-buffers stderr). So an empty STDERR log is not a
# buffering symptom -- it means the server wrote nothing there.
#
# GGML_VK_DISABLE_COOPMAT: every OTHER way GAIA starts Lemonade sets it
# (start-lemonade.ps1/.bat/.sh) and this task was the one launch path that did
# not, which made the persistent server the only one running the Vulkan
# cooperative-matrix path. On the eval runner that is the difference between the
# job that serves the RAG embedder and the gate that cannot (#3016).
$InnerCmd  = "`$env:PYTHONUNBUFFERED='1'; " +
             "`$env:GGML_VK_DISABLE_COOPMAT='1'; " +
             "`$env:GAIA_LEMONADE_TASK_VERSION='$TaskActionVersion'; " +
             "Start-Process -FilePath '$ServerExe' -ArgumentList '--port $Port' " +
             "-RedirectStandardOutput '$StdoutLog' -RedirectStandardError '$StderrLog' " +
             "-NoNewWindow -Wait"
try {
    $action    = New-ScheduledTaskAction -Execute "powershell.exe" `
                    -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$InnerCmd`""
    $trigger   = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
    Write-Host "Registered scheduled task '$TaskName' (stdout: $StdoutLog, stderr: $StderrLog)."
} catch {
    if ($DriftRestart -and (Test-Health)) {
        Write-Host "WARN: could not re-register scheduled task ($($_.Exception.Message)). Keeping the existing healthy server on port $Port -- it is still running the OLD launch environment (action marker did not match '$TaskActionVersion'), so this run does NOT pick up the current launch environment."
        exit 0
    }
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

# Only now -- with a server launched from THIS action version confirmed
# healthy -- record the marker Test-TaskCurrent trusts on the next run. Doing
# this earlier (e.g. right after Register-ScheduledTask) would let a job that
# registers but never gets a healthy start still mark the version current.
Set-Content -Path $VersionMarkerFile -Value $TaskActionVersion -NoNewline

# Warm the model so the first inference isn't a cold pull+load.
Write-Host "Warming model: $WarmModel"
try {
    $b = @{ model_name = $WarmModel } | ConvertTo-Json
    Invoke-RestMethod "http://localhost:$Port/api/v1/pull" -Method POST -ContentType "application/json" -Body $b -TimeoutSec 1200 | Out-Null
    Write-Host "Pulled $WarmModel."
} catch { Write-Host "WARN: warm pull failed: $($_.Exception.Message)" }

Write-Host "Lemonade ready on port $Port (persistent task '$TaskName')."
exit 0
