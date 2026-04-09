# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Start the GAIA Agent UI (backend + frontend dev server) on Windows
# Usage: .\installer\scripts\start-agent-ui.ps1 [-BackendOnly] [-FrontendOnly] [-Port 4200] [-DevPort 5174]

param(
    [switch]$BackendOnly,
    [switch]$FrontendOnly,
    [int]$Port = 4200,
    [int]$DevPort = 5174,
    [switch]$NoDebug,
    [switch]$Help
)

if ($Help) {
    Write-Host @"
Usage: .\installer\scripts\start-agent-ui.ps1 [OPTIONS]

Start the GAIA Agent UI backend and/or frontend dev server.

Options:
  -BackendOnly    Start only the FastAPI backend
  -FrontendOnly   Start only the Vite dev server
  -Port PORT      Backend port (default: 4200)
  -DevPort PORT   Frontend dev port (default: 5174)
  -NoDebug        Disable debug logging
  -Help           Show this help

Prerequisite: Lemonade Server must be running (lemonade-server serve)
"@
    exit 0
}

$RunBackend = -not $FrontendOnly
$RunFrontend = -not $BackendOnly

# ── Resolve project root ─────────────────────────────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$WebUIDir = Join-Path $ProjectRoot "src\gaia\apps\webui"

Write-Host "=========================================="
Write-Host "   GAIA Agent UI"
Write-Host "=========================================="
Write-Host "  Project:  $ProjectRoot"
Write-Host "  Backend:  http://localhost:$Port"
if ($RunFrontend) { Write-Host "  Frontend: http://localhost:$DevPort" }
Write-Host ""

# ── Check prerequisites ──────────────────────────────────────────────
if ($RunBackend) {
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Host "[ERROR] 'uv' not found. Install it: https://docs.astral.sh/uv/" -ForegroundColor Red
        exit 1
    }

    # Check that gaia is installed (editable install)
    Push-Location $ProjectRoot
    $gaiaCheck = & uv run python -c "import gaia" 2>&1
    Pop-Location
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] GAIA is not installed. Run the following from the project root:" -ForegroundColor Red
        Write-Host ""
        Write-Host "  cd $ProjectRoot"
        Write-Host '  uv venv && uv pip install -e ".[dev,rag]"'
        Write-Host ""
        Write-Host "See docs/reference/dev.mdx for full setup instructions."
        exit 1
    }
}

if ($RunFrontend) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Host "[ERROR] 'npm' not found. Install Node.js: https://nodejs.org/" -ForegroundColor Red
        exit 1
    }
    if (-not (Test-Path (Join-Path $WebUIDir "node_modules"))) {
        Write-Host "[INFO] Installing frontend dependencies..."
        Push-Location $WebUIDir
        & cmd.exe /c "npm install"
        Pop-Location
    }
}

# ── Track processes for cleanup ──────────────────────────────────────
$BackendProc = $null
$FrontendProc = $null

# ── Start backend ────────────────────────────────────────────────────
if ($RunBackend) {
    Write-Host "=== Starting Backend ==="

    # Kill existing process on the port
    $existing = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.State -eq 'Listen' }
    if ($existing) {
        foreach ($conn in $existing) {
            Write-Host "[WARN] Port $Port in use by PID $($conn.OwningProcess) - killing..."
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2
    }

    $debugFlag = if ($NoDebug) { "" } else { "--debug" }
    $BackendProc = Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c uv run python -m gaia.ui.server $debugFlag --port $Port" `
        -WorkingDirectory $ProjectRoot `
        -PassThru -NoNewWindow

    Write-Host "[OK] Backend started (PID $($BackendProc.Id))"

    # Wait for health check
    Write-Host "  Waiting for backend..."
    $maxWait = 30
    $ready = $false
    for ($i = 1; $i -le $maxWait; $i++) {
        Start-Sleep -Seconds 1
        try {
            $null = Invoke-RestMethod -Uri "http://localhost:$Port/api/health" -TimeoutSec 2 -ErrorAction Stop
            Write-Host "[OK] Backend ready (${i}s)" -ForegroundColor Green
            $ready = $true
            break
        } catch {}
    }

    if (-not $ready) {
        Write-Host "[ERROR] Backend failed to start within ${maxWait}s" -ForegroundColor Red
        if ($BackendProc -and -not $BackendProc.HasExited) { $BackendProc | Stop-Process -Force }
        exit 1
    }
    Write-Host ""
}

# ── Start frontend ───────────────────────────────────────────────────
if ($RunFrontend) {
    Write-Host "=== Starting Frontend ==="
    $FrontendProc = Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c npm run dev -- --port $DevPort" `
        -WorkingDirectory $WebUIDir `
        -PassThru

    Write-Host "[OK] Frontend started (PID $($FrontendProc.Id))"
    Start-Sleep -Seconds 3
    Write-Host ""
}

# ── Summary ──────────────────────────────────────────────────────────
Write-Host "=========================================="
Write-Host "  GAIA Agent UI is running!" -ForegroundColor Green
Write-Host "=========================================="
if ($RunFrontend) {
    Write-Host "  Open: http://localhost:$DevPort"
} else {
    Write-Host "  API:  http://localhost:$Port"
}
Write-Host ""
Write-Host "  Backend PID:  $($BackendProc.Id)"
if ($FrontendProc) { Write-Host "  Frontend PID: $($FrontendProc.Id)" }
Write-Host ""
Write-Host "  Press Ctrl+C to stop, or run:" -ForegroundColor DarkGray
if ($BackendProc)  { Write-Host "    Stop-Process -Id $($BackendProc.Id)   # backend" -ForegroundColor DarkGray }
if ($FrontendProc) { Write-Host "    Stop-Process -Id $($FrontendProc.Id)   # frontend" -ForegroundColor DarkGray }
Write-Host ""

# ── Wait for backend (keeps script alive, Ctrl+C exits) ─────────────
try {
    if ($BackendProc) {
        $BackendProc.WaitForExit()
    } elseif ($FrontendProc) {
        $FrontendProc.WaitForExit()
    }
} finally {
    # Cleanup on exit
    if ($BackendProc -and -not $BackendProc.HasExited) {
        Write-Host "  Stopping backend..."
        $BackendProc | Stop-Process -Force -ErrorAction SilentlyContinue
    }
    if ($FrontendProc -and -not $FrontendProc.HasExited) {
        Write-Host "  Stopping frontend..."
        $FrontendProc | Stop-Process -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Done."
}
