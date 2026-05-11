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

.EXAMPLE
    .\installer\scripts\start-lemonade.ps1 -ModelName "Qwen3-0.6B-GGUF"

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
    [switch]$ClearCache
)

$ErrorActionPreference = "Stop"

try {
    Write-Host "=========================================="
    Write-Host "   LEMONADE SERVER SETUP"
    Write-Host "=========================================="
    Write-Host ""

    # Check port availability
    Write-Host "=== Checking Port $Port ==="
    $portInUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($portInUse) {
        # Get unique PIDs (may have multiple connections on same port)
        $processIds = $portInUse.OwningProcess | Select-Object -Unique
        foreach ($processId in $processIds) {
            Write-Host "[WARN] Port $Port in use by PID: $processId - killing orphaned process"
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2
    } else {
        Write-Host "[OK] Port $Port is available"
    }
    Write-Host ""

    # Check installation. v10.x splits the old single binary into:
    #   - ``LemonadeServer.exe`` (PascalCase) -- the actual server, but
    #     it appears to be a tray/launcher with a different CLI shape
    #     than the legacy ``lemonade-server serve --port ...`` we rely on.
    #   - ``lemonade-server.exe`` -- a deprecation shim that still
    #     translates ``serve --no-tray --port N`` correctly into the new
    #     server invocation (proven working in test_gaia_cli_windows.yml).
    #   - ``lemonade.exe`` -- the v10.x CLI for non-server commands (pull
    #     etc.). ``lemonade-server pull`` is fully removed; calling it
    #     prints a deprecation message and exits non-zero.
    #
    # So we deliberately use the shim for ``serve`` (it handles the
    # arg translation) and ``lemonade`` for ``pull``. The shim refuses
    # ``--ctx-size`` -- pin context per-model via the /load API instead.
    Write-Host "=== Checking Installation ==="
    $lemonadeServerExe = $null
    $lemonadeServerCmd = Get-Command "lemonade-server" -ErrorAction SilentlyContinue
    if ($lemonadeServerCmd) {
        $lemonadeServerExe = $lemonadeServerCmd.Source
        Write-Host "[OK] Found 'lemonade-server' on PATH: $lemonadeServerExe"
    } elseif (Test-Path ".\.venv\Scripts\lemonade-server.exe") {
        $lemonadeServerExe = ".\.venv\Scripts\lemonade-server.exe"
        Write-Host "[OK] Found 'lemonade-server' in .venv: $lemonadeServerExe"
    } else {
        Write-Host "[ERROR] lemonade-server not found on PATH or in .venv"
        exit 1
    }

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

    # Start server via the ``lemonade-server`` shim. We deliberately
    # don't call ``LemonadeServer.exe`` directly -- on v10.2.0 it appears
    # to be a tray/GUI launcher with a different CLI shape that emits no
    # console output and never binds to the requested port when invoked
    # with the legacy ``serve --port N --no-tray`` args. The shim still
    # translates that arg shape correctly (proven by test_gaia_cli_windows
    # which uses the same incantation against the same v10.2.0 runner).
    # ``--ctx-size`` is omitted because the shim refuses it in v10.x; the
    # ctx_size is set per-model on the /api/v1/load call below.
    Write-Host "=== Starting Server ==="
    $env:GGML_VK_DISABLE_COOPMAT = "1"
    $serverProcess = Start-Process -FilePath $lemonadeServerExe `
        -ArgumentList "serve", "--port", "$Port", "--no-tray" `
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
        throw "Server startup timeout"
    }
    Write-Host ""

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
