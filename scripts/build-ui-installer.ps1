# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

<#
.SYNOPSIS
    Build GAIA Agent UI installer for Windows.

.DESCRIPTION
    Builds the GAIA Agent UI desktop application and creates a Windows installer.

    Two distribution modes:
    1. Electron Desktop App (via electron-forge, produces .exe installer)
    2. Browser-based App (via gaia chat --ui, no installer needed)

    This script handles the Electron Desktop App build.

.PARAMETER Mode
    Build mode: "electron" (default) or "browser"
    - electron: Full Electron desktop app with installer
    - browser: Just build the frontend (for gaia chat --ui)

.PARAMETER SkipNodeInstall
    Skip npm install step (use existing node_modules)

.EXAMPLE
    .\build-ui-installer.ps1
    .\build-ui-installer.ps1 -Mode browser
    .\build-ui-installer.ps1 -SkipNodeInstall
#>

param(
    [ValidateSet("electron", "browser")]
    [string]$Mode = "electron",
    [switch]$SkipNodeInstall
)

$ErrorActionPreference = "Stop"
$REPO_ROOT = (Resolve-Path "$PSScriptRoot\..").Path
$WEBUI_DIR = "$REPO_ROOT\src\gaia\apps\chat\webui"
$ELECTRON_DIR = "$REPO_ROOT\src\gaia\electron"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  GAIA Agent UI Installer Builder" -ForegroundColor Cyan
Write-Host "  Mode: $Mode" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ── Prerequisites ────────────────────────────────────────────────────────────

Write-Host "[1/5] Checking prerequisites..." -ForegroundColor Yellow

# Check Node.js
try {
    $nodeVersion = & node --version 2>$null
    Write-Host "  Node.js: $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Node.js not found. Install from https://nodejs.org" -ForegroundColor Red
    exit 1
}

# Check npm
try {
    $npmVersion = & npm --version 2>$null
    Write-Host "  npm: v$npmVersion" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: npm not found." -ForegroundColor Red
    exit 1
}

# ── Install Dependencies ────────────────────────────────────────────────────

Write-Host ""
Write-Host "[2/5] Installing dependencies..." -ForegroundColor Yellow

if (-not $SkipNodeInstall) {
    Push-Location $WEBUI_DIR
    try {
        & npm ci
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  npm ci failed, trying npm install..." -ForegroundColor Yellow
            & npm install
        }
        Write-Host "  Frontend dependencies installed" -ForegroundColor Green
    } finally {
        Pop-Location
    }
} else {
    Write-Host "  Skipping npm install (--SkipNodeInstall)" -ForegroundColor Gray
}

# ── Build Frontend ──────────────────────────────────────────────────────────

Write-Host ""
Write-Host "[3/5] Building frontend..." -ForegroundColor Yellow

Push-Location $WEBUI_DIR
try {
    & npm run build
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: Frontend build failed" -ForegroundColor Red
        exit 1
    }

    # Verify build output
    if (Test-Path "$WEBUI_DIR\dist\index.html") {
        $jsSize = (Get-ChildItem "$WEBUI_DIR\dist\assets\*.js" | Measure-Object -Property Length -Sum).Sum
        $cssSize = (Get-ChildItem "$WEBUI_DIR\dist\assets\*.css" | Measure-Object -Property Length -Sum).Sum
        Write-Host "  Build output: $([math]::Round($jsSize/1024))KB JS, $([math]::Round($cssSize/1024))KB CSS" -ForegroundColor Green
    } else {
        Write-Host "  ERROR: dist/index.html not found" -ForegroundColor Red
        exit 1
    }
} finally {
    Pop-Location
}

if ($Mode -eq "browser") {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Browser build complete!" -ForegroundColor Green
    Write-Host "  Run: gaia chat --ui" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    exit 0
}

# ── Build Electron App ──────────────────────────────────────────────────────

Write-Host ""
Write-Host "[4/5] Packaging Electron app..." -ForegroundColor Yellow

Push-Location $WEBUI_DIR
try {
    & npx electron-forge package
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: Electron packaging failed" -ForegroundColor Red
        exit 1
    }
    Write-Host "  Electron app packaged" -ForegroundColor Green
} finally {
    Pop-Location
}

# ── Create Installer ───────────────────────────────────────────────────────

Write-Host ""
Write-Host "[5/5] Creating installer..." -ForegroundColor Yellow

Push-Location $WEBUI_DIR
try {
    & npx electron-forge make
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: Installer creation failed" -ForegroundColor Red
        exit 1
    }

    # Find the output installer
    $installer = Get-ChildItem -Path "$WEBUI_DIR\out\make" -Filter "*.exe" -Recurse | Select-Object -First 1
    if ($installer) {
        $installerSize = [math]::Round($installer.Length / 1MB, 1)
        Write-Host "  Installer: $($installer.Name) ($($installerSize) MB)" -ForegroundColor Green
        Write-Host "  Location: $($installer.FullName)" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: No .exe installer found in output" -ForegroundColor Yellow
        # Check for other outputs
        Get-ChildItem -Path "$WEBUI_DIR\out\make" -Recurse | ForEach-Object {
            Write-Host "    Found: $($_.FullName)" -ForegroundColor Gray
        }
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  GAIA Agent UI installer build complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
