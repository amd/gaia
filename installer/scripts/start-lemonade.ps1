# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

<#
.SYNOPSIS
    Start Lemonade Server, pull and load models for testing.

.DESCRIPTION
    This script starts lemonade-server, pulls models, and loads them.
    Everything runs in a single PowerShell session to avoid process lifecycle issues.

.PARAMETER ModelName
    Primary model to pull and load (default: Qwen3-4B-Instruct-2507-GGUF)

.PARAMETER AdditionalModels
    Comma-separated list of additional models to pull (but not load)

.PARAMETER Port
    Server port (default: 8000)

.PARAMETER CtxSize
    Context size (default: 8192)

.PARAMETER InitWaitTime
    Seconds to wait after loading model (default: 10)

.PARAMETER ClearCache
    Clear model cache before pulling (default: false)

.PARAMETER NoModel
    Start the server only (skip pull/load). Use when the caller registers/loads
    its own models afterwards (e.g. a custom user-model via LemonadeClient).

.EXAMPLE
    .\installer\scripts\start-lemonade.ps1 -ModelName "Qwen3-0.6B-GGUF"

.EXAMPLE
    # Start the server robustly, then let the caller register a custom embedder:
    .\installer\scripts\start-lemonade.ps1 -Port 13305 -NoModel

.EXAMPLE
    .\installer\scripts\start-lemonade.ps1 -ModelName "nomic-embed-text-v2-moe-GGUF" -AdditionalModels "Qwen3-0.6B-GGUF,Qwen3-VL-4B-Instruct-GGUF" -InitWaitTime 30 -ClearCache
#>

param(
    [Parameter(Mandatory=$false)]
    [string]$ModelName = "Qwen3-4B-Instruct-2507-GGUF",

    [Parameter(Mandatory=$false)]
    [string]$AdditionalModels = "",

    [Parameter(Mandatory=$false)]
    [int]$Port = 8000,

    [Parameter(Mandatory=$false)]
    [int]$CtxSize = 32768,

    [Parameter(Mandatory=$false)]
    [int]$InitWaitTime = 10,

    [Parameter(Mandatory=$false)]
    [switch]$ClearCache,

    [Parameter(Mandatory=$false)]
    [switch]$NoModel
)

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
    # Resolve LemonadeServer.exe from LEMONADE_SERVER_PATH or the install dirs.
    $lemonadeServerExe = $null
    foreach ($cand in @(
        $env:LEMONADE_SERVER_PATH,
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
    $env:GGML_VK_DISABLE_COOPMAT = "1"
    $serverProcess = Start-Process -FilePath $lemonadeServerExe `
        -ArgumentList "--port", "$Port" `
        -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput "lemonade-server-stdout.log" `
        -RedirectStandardError "lemonade-server-stderr.log"
    Write-Host "[OK] Started server PID: $($serverProcess.Id)"
    Write-Host "     Logs: lemonade-server-stdout.log, lemonade-server-stderr.log"

    # Export process ID for cleanup (GitHub Actions)
    if ($env:GITHUB_OUTPUT) {
        "lemonade-process-id=$($serverProcess.Id)" >> $env:GITHUB_OUTPUT
    }
    if ($env:GITHUB_ENV) {
        "LEMONADE_PROCESS_ID=$($serverProcess.Id)" >> $env:GITHUB_ENV
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
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:${Port}/api/v1/health" -TimeoutSec 5
            Write-Host "[OK] Server ready (waited $waited seconds)"
            $ready = $true
        } catch {
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

    # Start-only mode: the caller registers/loads its own models (e.g. a custom
    # user-model via LemonadeClient), so skip the built-in pull/load path.
    if ($NoModel) {
        Write-Host "=========================================="
        Write-Host "✅ LEMONADE SERVER READY (no model preloaded)"
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
    Write-Host "Pull exit code: $LASTEXITCODE"
    if ($pullOutput) {
        $pullOutput | ForEach-Object { Write-Host "  $_" }
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
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:${Port}/api/v1/health" -TimeoutSec 5
            Write-Host "[OK] Server responsive after pull"
            $healthy = $true
        } catch {
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
    Write-Host "✅ LEMONADE SERVER READY"
    Write-Host "=========================================="
    Write-Host "Model: $ModelName"
    Write-Host "Port: $Port"
    Write-Host "Process ID: $($serverProcess.Id)"
    Write-Host ""

    exit 0

} catch {
    Write-Host ""
    Write-Host "=========================================="
    Write-Host "❌ LEMONADE SERVER SETUP FAILED"
    Write-Host "=========================================="
    Write-Host "Error: $_"
    exit 1
}
