# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

<#
.SYNOPSIS
    The one CI launcher for Lemonade Server on Windows runners.

.DESCRIPTION
    Two modes, picked by -JobScoped:

    Persistent (default) -- a server that outlives the job.
        GitHub Actions terminates any process a job spawns when the job ends, so
        a server started inline never survives to the next job. This mode
        launches LemonadeServer.exe with `--port $Port` (so it binds $Port
        regardless of a stale config.json) as a Windows **Scheduled Task**: the
        Task Scheduler owns it, not the job, so it persists across jobs and
        reboots. The task also auto-starts at boot.

        Idempotent + self-healing:
          - If a healthy server is already on $Port, returns immediately.
          - Otherwise (re)registers + starts the task, retrying until healthy,
            then warms $WarmModel (pull) so the first test request isn't a cold
            load. A failed warm pull only warns.

        Use -ForceRestart to recycle the server even if it's currently healthy.

    Job-scoped (-JobScoped) -- a server owned by the calling step.
        Frees $Port (cleanup-lemonade.ps1), starts LemonadeServer.exe as a child
        of this session with its output in lemonade-server-{stdout,stderr}.log
        in the CURRENT directory, optionally installs a backend, then pulls
        $ModelName (+ $AdditionalModels) and loads $ModelName at $CtxSize.
        Exports LEMONADE_PROCESS_ID (process env, GITHUB_ENV, and the
        `lemonade-process-id` step output) so the caller can stop it.

    Both modes set LEMONADE_BASE_URL (process env + GITHUB_ENV) in CI.
    Passing an option that belongs to the other mode is an error.

.PARAMETER Port
    Server port (default 13305, Lemonade's default since v10.1). Both modes.

.PARAMETER ServerExe
    Path to LemonadeServer.exe. Defaults to LEMONADE_SERVER_PATH. Both modes;
    when unset or missing, each mode searches the install dirs.

.PARAMETER WarmModel
    Persistent only. Model to pull so the backend is warm
    (default Gemma-4-E4B-it-GGUF).

.PARAMETER ForceRestart
    Persistent only. Restart the task even if the server is already healthy.

.PARAMETER JobScoped
    Start a server owned by this step instead of the persistent task.

.PARAMETER ModelName
    Job-scoped only. Primary model to pull and load
    (default Qwen3-4B-Instruct-2507-GGUF).

.PARAMETER AdditionalModels
    Job-scoped only. Comma-separated models to pull (but not load).

.PARAMETER CtxSize
    Job-scoped only. ctx_size for the /load call (default 32768).

.PARAMETER InitWaitTime
    Job-scoped only. Seconds to wait after loading the model (default 10).

.PARAMETER ClearCache
    Job-scoped only. Clear the model cache before pulling.

.PARAMETER NoModel
    Job-scoped only. Start the server only (skip pull/load). Use when the caller
    registers/loads its own models afterwards (e.g. a custom user-model via
    LemonadeClient).

.PARAMETER Backend
    Job-scoped only. Lemonade backend to install before pulling, in
    recipe:backend form (e.g. "flm:npu"). Required for backends whose models are
    contributed at runtime rather than shipped in the built-in catalog: FLM
    models are enumerated by shelling out to the `flm` CLI, so with no FLM
    backend installed the catalog holds no *-FLM name and every pull 400s with
    "no built-in model ...".

.EXAMPLE
    # Persistent server for an eval job, warmed with the agent model:
    powershell -ExecutionPolicy Bypass -File installer\scripts\ensure-lemonade-running.ps1 -WarmModel "Gemma-4-E4B-it-GGUF"

.EXAMPLE
    .\installer\scripts\ensure-lemonade-running.ps1 -JobScoped -ModelName "Qwen3-0.6B-GGUF"

.EXAMPLE
    # NPU/FLM: without -Backend the FLM half of the catalog is empty.
    .\installer\scripts\ensure-lemonade-running.ps1 -JobScoped -ModelName "gemma4-it-e2b-FLM" -Backend "flm:npu"

.EXAMPLE
    # Start the server robustly, then let the caller register a custom embedder:
    .\installer\scripts\ensure-lemonade-running.ps1 -JobScoped -Port 13305 -NoModel

.EXAMPLE
    .\installer\scripts\ensure-lemonade-running.ps1 -JobScoped -ModelName "Qwen3-0.6B-GGUF" -AdditionalModels "Qwen3-VL-4B-Instruct-GGUF" -InitWaitTime 30 -ClearCache

.NOTES
    Concurrency caveat (persistent mode): the drift restart below
    (Test-TaskCurrent returning $false) stops and restarts the server on this
    box. Not every persistent-mode caller shares the serial `lemonade-eval`
    concurrency group; test_agent_sdk.yml and test_gaia_cli_windows.yml use
    per-branch concurrency groups instead and can run at the same time as an
    eval job on the same runner. A version bump here can therefore have the
    first job that observes it kill the server out from under a concurrently
    running eval on one of those two workflows. Coordinating that (a
    machine-wide mutex, or moving those two workflows into the shared slot)
    was judged not worth doing -- flagging it so the next version bump isn't a
    surprise.
#>
[CmdletBinding()]
param(
    [int]$Port = 13305,
    [string]$ServerExe = $env:LEMONADE_SERVER_PATH,

    # Persistent mode
    [string]$WarmModel = "Gemma-4-E4B-it-GGUF",
    [switch]$ForceRestart,

    # Job-scoped mode
    [switch]$JobScoped,
    [string]$ModelName = "Qwen3-4B-Instruct-2507-GGUF",
    [string]$AdditionalModels = "",
    [int]$CtxSize = 32768,
    [int]$InitWaitTime = 10,
    [switch]$ClearCache,
    [switch]$NoModel,
    [string]$Backend = ""
)

$PersistentOnly = @("WarmModel", "ForceRestart")
$JobScopedOnly = @("ModelName", "AdditionalModels", "CtxSize", "InitWaitTime", "ClearCache", "NoModel", "Backend")
# Captured first: inside a Where-Object block $PSBoundParameters is that block's own.
$Bound = $PSBoundParameters
$WrongMode = @(if ($JobScoped) { $PersistentOnly } else { $JobScopedOnly }) |
    Where-Object { $Bound.ContainsKey($_) }
if ($WrongMode) {
    $modeName = if ($JobScoped) { "-JobScoped" } else { "persistent (no -JobScoped)" }
    Write-Host "ERROR: $($WrongMode -join ', ') not valid in $modeName mode. See the .PARAMETER notes at the top of $PSCommandPath."
    exit 1
}

# In CI, name this server for every later GAIA client in the job, so none of
# them follows a leftover record of GAIA's own Lemonade to a dead port.
if ($env:GITHUB_ACTIONS -eq "true") {
    $env:LEMONADE_BASE_URL = "http://localhost:$Port/api/v1"
    if ($env:GITHUB_ENV) {
        Add-Content -Path $env:GITHUB_ENV -Value "LEMONADE_BASE_URL=http://localhost:$Port/api/v1"
    }
}

# GPU environment every CI launch sets, in both modes.
# GGML_VK_DISABLE_COOPMAT: the CI runner launches still set it, although GAIA's
# own launches no longer do -- it halves every model's prompt speed on Strix
# Halo. The persistent task was once the one runner launch path that did not,
# which made it the only server running the Vulkan cooperative-matrix path. On
# the eval runner that is the difference between the job that serves the RAG
# embedder and the gate that cannot (#3016). Changing this changes the task
# action: bump $TaskActionVersion below.
$LaunchEnv = [ordered]@{ GGML_VK_DISABLE_COOPMAT = "1" }

function Test-Health {
    try { Invoke-RestMethod "http://localhost:$Port/api/v1/health" -TimeoutSec 5 | Out-Null; return $true }
    catch { return $false }
}

# ============================================================================
# Job-scoped mode
# ============================================================================
if ($JobScoped) {
    $ErrorActionPreference = "Stop"

    try {
        Write-Host "=========================================="
        Write-Host "   LEMONADE SERVER SETUP"
        Write-Host "=========================================="
        Write-Host ""

        # Free the port and reap stray Lemonade/llama-server processes from prior
        # jobs on this shared runner (otherwise the server dies with a winerror
        # 10048 bind collision). Shared with the inline-start CI workflows.
        & "$PSScriptRoot\cleanup-lemonade.ps1" -Port $Port
        Write-Host ""

        # Check installation. v10.x ships:
        #   - ``LemonadeServer.exe`` -- the actual server. Launch it directly with a
        #     bare ``--port N`` (there is no ``serve`` subcommand). ``--ctx-size`` is
        #     not a launch arg -- pin context per-model via the /load API below.
        #   - ``lemonade.exe`` -- the v10.x CLI for non-server commands (``pull`` etc.).
        # The old ``lemonade-server`` shim was deprecated in v10.5 and is no longer
        # installed, so we use LemonadeServer.exe for serving and ``lemonade`` for pull.
        Write-Host "=== Checking Installation ==="
        # Resolve LemonadeServer.exe from -ServerExe (LEMONADE_SERVER_PATH) or the install dirs.
        $lemonadeServerExe = $null
        foreach ($cand in @(
            $ServerExe,
            "$env:LOCALAPPDATA\lemonade_server\bin\LemonadeServer.exe",
            "$env:ProgramFiles\Lemonade Server\bin\LemonadeServer.exe",
            "${env:ProgramFiles(x86)}\Lemonade Server\bin\LemonadeServer.exe"
        )) {
            if ($cand -and (Test-Path $cand)) { $lemonadeServerExe = (Resolve-Path $cand).Path; break }
        }
        if (-not $lemonadeServerExe) {
            $found = Get-ChildItem `
                "C:\Users\*\AppData\Local\lemonade_server\bin\LemonadeServer.exe", `
                "C:\Program Files*\Lemonade Server\bin\LemonadeServer.exe" `
                -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($found) { $lemonadeServerExe = $found.FullName }
        }
        if (-not $lemonadeServerExe) {
            Write-Host "[ERROR] LemonadeServer.exe not found (set LEMONADE_SERVER_PATH or install Lemonade Server)"
            exit 1
        }
        Write-Host "[OK] Found server: $lemonadeServerExe"

        $lemonadeCli = $null
        $lemonadeCliCmd = Get-Command "lemonade" -ErrorAction SilentlyContinue
        if ($lemonadeCliCmd) {
            $lemonadeCli = $lemonadeCliCmd.Source
            Write-Host "[OK] Found CLI 'lemonade' at: $lemonadeCli"
        } else {
            $lemonadeCli = $lemonadeServerExe
            Write-Host "[WARN] 'lemonade' not on PATH; falling back to '$lemonadeServerExe' for pull operations"
        }
        Write-Host ""

        # Clear cache if requested
        if ($ClearCache) {
            Write-Host "=== Clearing Model Cache ==="
            $cacheDir = "$env:LOCALAPPDATA\lemonade-server\models"
            if (Test-Path $cacheDir) {
                Write-Host "Removing: $cacheDir"
                Remove-Item $cacheDir -Recurse -Force -ErrorAction SilentlyContinue
            }
            Write-Host ""
        }

        # Start the server. v10.5 removed the `lemonade-server` shim, so launch
        # LemonadeServer.exe directly with a bare `--port` (there is no `serve`
        # subcommand in v10.x). `--ctx-size` is set per-model on the /api/v1/load
        # call below. (Verified on the stx runner: `LemonadeServer.exe --port N`
        # binds N -- the legacy `serve --port` shape is what never bound.)
        Write-Host "=== Starting Server ==="
        foreach ($name in $LaunchEnv.Keys) { Set-Item -Path "env:$name" -Value $LaunchEnv[$name] }
        $serverProcess = Start-Process -FilePath $lemonadeServerExe `
            -ArgumentList "--port", "$Port" `
            -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput "lemonade-server-stdout.log" `
            -RedirectStandardError "lemonade-server-stderr.log"
        Write-Host "[OK] Started server PID: $($serverProcess.Id)"
        Write-Host "     Logs: lemonade-server-stdout.log, lemonade-server-stderr.log"

        # Export process ID for cleanup. Set it on the live process env too (not
        # just GITHUB_ENV, which only reaches *later* steps) so a caller that starts
        # the server and runs its tests in the SAME step can stop it in a finally.
        $env:LEMONADE_PROCESS_ID = "$($serverProcess.Id)"
        # Add-Content, never `>>`: Windows PowerShell 5.1 appends UTF-16, and when this
        # runs inside another action's step that file then fails to parse as output.
        if ($env:GITHUB_OUTPUT) {
            Add-Content -Path $env:GITHUB_OUTPUT -Value "lemonade-process-id=$($serverProcess.Id)"
        }
        if ($env:GITHUB_ENV) {
            Add-Content -Path $env:GITHUB_ENV -Value "LEMONADE_PROCESS_ID=$($serverProcess.Id)"
        }
        Write-Host ""

        # Wait for server
        Write-Host "=== Waiting for Server ==="
        $maxWait = 60
        $waited = 0
        $ready = $false
        while ($waited -lt $maxWait -and -not $ready) {
            Start-Sleep -Seconds 2
            $waited += 2
            if (Test-Health) {
                Write-Host "[OK] Server ready (waited $waited seconds)"
                $ready = $true
            } else {
                Write-Host "Waiting... ($waited/$maxWait seconds)"
            }
        }

        if (-not $ready) {
            Write-Host "[ERROR] Server failed to start"
            # Dump the server's own output so a startup failure is diagnosable
            # (missing exe, bind collision, backend crash) instead of an opaque
            # health-check timeout.
            Write-Host "=== Server stdout (last 100 lines) ==="
            if (Test-Path "lemonade-server-stdout.log") {
                Get-Content "lemonade-server-stdout.log" -Tail 100
            }
            Write-Host "=== Server stderr (last 100 lines) ==="
            if (Test-Path "lemonade-server-stderr.log") {
                Get-Content "lemonade-server-stderr.log" -Tail 100
            }
            throw "Server startup timeout"
        }
        Write-Host ""

        # Install the requested backend before any pull. Mirrors what
        # ``gaia init --profile npu`` does (InitCommand._install_backend): check
        # recipe status via /system-info, install via /install only when needed.
        if ($Backend) {
            Write-Host "=== Installing Backend: $Backend ==="
            $recipe, $backendKey = $Backend.Split(":", 2)
            if (-not $recipe -or -not $backendKey) {
                throw "Invalid -Backend '$Backend': expected 'recipe:backend' (e.g. 'flm:npu')"
            }
            $sysinfo = Invoke-RestMethod -Uri "http://localhost:${Port}/api/v1/system-info" -TimeoutSec 30
            $backendInfo = $sysinfo.recipes.$recipe.backends.$backendKey
            if (-not $backendInfo) {
                throw "Lemonade does not know backend '$Backend' (recipes: $($sysinfo.recipes.PSObject.Properties.Name -join ', '))"
            }
            $state = $backendInfo.state
            Write-Host "Reported state for ${recipe}:${backendKey}: $state"

            if ($state -eq "unsupported") {
                # Hardware genuinely can't run it -- say so here rather than letting
                # the pull fail later with an opaque "model not registered".
                throw "Backend '$Backend' is unsupported on this machine: $($backendInfo.message)"
            }

            if ($state -eq "installed") {
                Write-Host "Already installed; skipping install"
            } else {
                # /install takes recipe + backend as separate fields; a combined
                # "spec" is rejected with 400 "Both 'recipe' and 'backend' are required".
                $installBody = @{ recipe = $recipe; backend = $backendKey } | ConvertTo-Json
                try {
                    $installResponse = Invoke-RestMethod -Uri "http://localhost:${Port}/api/v1/install" `
                        -Method POST -ContentType "application/json" -Body $installBody -TimeoutSec 900
                } catch {
                    # The JSON body carries the actionable reason; the exception
                    # message is only "(400) Bad Request".
                    $detail = if ($_.ErrorDetails) { $_.ErrorDetails.Message } else { $_.Exception.Message }
                    throw "Installing backend '$Backend' failed: $detail"
                }
                Write-Host "[OK] Backend installed: $($installResponse | ConvertTo-Json -Compress)"
            }

            # The backend contributes its models to the catalog only once it is
            # live; re-read /system-info so a silent no-op install fails here rather
            # than as a confusing pull error.
            $sysinfo = Invoke-RestMethod -Uri "http://localhost:${Port}/api/v1/system-info" -TimeoutSec 30
            $state = $sysinfo.recipes.$recipe.backends.$backendKey.state
            if ($state -ne "installed") {
                throw "Backend '$Backend' still reports state '$state' after install (expected 'installed'); try '$($sysinfo.recipes.$recipe.backends.$backendKey.action)'"
            }
            Write-Host "[OK] Backend '$Backend' is installed"
            Write-Host ""
        }

        # Start-only mode: the caller registers/loads its own models (e.g. a custom
        # user-model via LemonadeClient), so skip the built-in pull/load path.
        if ($NoModel) {
            Write-Host "=========================================="
            Write-Host "[OK] LEMONADE SERVER READY (no model preloaded)"
            Write-Host "=========================================="
            Write-Host "Port: $Port"
            Write-Host "Process ID: $($serverProcess.Id)"
            Write-Host ""
            exit 0
        }

        # Pull primary model.
        #
        # v10.x routes pull through the ``lemonade`` CLI rather than
        # ``lemonade-server pull``; the legacy form prints
        # ``This command is deprecated. Use 'lemonade pull --help' instead.``
        # and exits non-zero. ``$lemonadeCli`` resolves to plain ``lemonade``
        # when available and falls back to ``$lemonadeServerExe`` for older
        # installs.
        Write-Host "=== Pulling Primary Model: $ModelName ==="
        $pullOutput = & $lemonadeCli pull $ModelName 2>&1
        $pullExit = $LASTEXITCODE
        Write-Host "Pull exit code: $pullExit"
        if ($pullOutput) {
            $pullOutput | ForEach-Object { Write-Host "  $_" }
        }
        if ($pullExit -ne 0) {
            throw "Pull of primary model '$ModelName' failed (exit $pullExit)"
        }
        Write-Host ""

        # Pull additional models
        if ($AdditionalModels) {
            Write-Host "=== Pulling Additional Models ==="
            $models = $AdditionalModels.Split(",")
            foreach ($model in $models) {
                $model = $model.Trim()
                Write-Host "Pulling: $model"
                $pullOutput = & $lemonadeCli pull $model 2>&1
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "  [WARN] Pull failed with exit code: $LASTEXITCODE"
                } else {
                    Write-Host "  [OK] $model pulled"
                }
            }
            Write-Host ""
        }

        # Wait for server to stabilize after pull (retry health check)
        Write-Host "Waiting for server to stabilize after pull..."
        $maxRetry = 10
        $retry = 0
        $healthy = $false
        while ($retry -lt $maxRetry -and -not $healthy) {
            Start-Sleep -Seconds 2
            $retry++
            if (Test-Health) {
                Write-Host "[OK] Server responsive after pull"
                $healthy = $true
            } else {
                Write-Host "Waiting for server after pull... ($retry/$maxRetry)"
            }
        }

        if (-not $healthy) {
            Write-Host "[ERROR] Server not responding after pull"
            throw "Server died after pull"
        }
        Write-Host ""

        # Load model. Now that ``--ctx-size`` is no longer accepted on
        # ``serve``, we pin the requested ctx_size on the load call instead so
        # the server slot has the right size when subsequent completions hit it.
        Write-Host "=== Loading Model: $ModelName (ctx_size=$CtxSize) ==="
        try {
            $loadBody = @{ model_name = $ModelName; ctx_size = $CtxSize } | ConvertTo-Json
            $loadResponse = Invoke-RestMethod -Uri "http://localhost:${Port}/api/v1/load" `
                -Method POST -ContentType "application/json" -Body $loadBody -TimeoutSec 120
            Write-Host "[OK] Model loaded: $($loadResponse | ConvertTo-Json -Compress)"
        } catch {
            Write-Host "[ERROR] Load failed: $($_.Exception.Message)"
            if ($_.ErrorDetails) {
                Write-Host "Error details: $($_.ErrorDetails.Message)"
            }
            Write-Host ""
            Write-Host "=== Server Logs (last 100 lines) ==="
            if (Test-Path "lemonade-server-stdout.log") {
                Get-Content "lemonade-server-stdout.log" -Tail 100
            }
            if (Test-Path "lemonade-server-stderr.log") {
                Get-Content "lemonade-server-stderr.log" -Tail 100
            }
            throw "Model load failed"
        }
        Write-Host ""

        # Wait for initialization
        Write-Host "Waiting $InitWaitTime seconds for model initialization..."
        Start-Sleep -Seconds $InitWaitTime

        Write-Host "=========================================="
        Write-Host "[OK] LEMONADE SERVER READY"
        Write-Host "=========================================="
        Write-Host "Model: $ModelName"
        Write-Host "Port: $Port"
        Write-Host "Process ID: $($serverProcess.Id)"
        Write-Host ""

        exit 0

    } catch {
        Write-Host ""
        Write-Host "=========================================="
        Write-Host "[FAIL] LEMONADE SERVER SETUP FAILED"
        Write-Host "=========================================="
        Write-Host "Error: $_"
        exit 1
    }
}

# ============================================================================
# Persistent mode (default)
# ============================================================================
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
# default regenerates. The search includes the SYSTEM profile because the task
# runs as SYSTEM.
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
# PYTHONUNBUFFERED: Python block-buffers stdout when it is a pipe rather than a
# console, so without this a server that never exits never flushes and its stdout
# log stays empty. Measured: stdout 0 bytes without it, 39 with; stderr arrives
# either way (Python line-buffers stderr). So an empty STDERR log is not a
# buffering symptom -- it means the server wrote nothing there.
$TaskEnv = [ordered]@{ PYTHONUNBUFFERED = "1" }
foreach ($name in $LaunchEnv.Keys) { $TaskEnv[$name] = $LaunchEnv[$name] }
$TaskEnv["GAIA_LEMONADE_TASK_VERSION"] = $TaskActionVersion
$InnerCmd  = (($TaskEnv.Keys | ForEach-Object { "`$env:$_='$($TaskEnv[$_])'; " }) -join "") +
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
