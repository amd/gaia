# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# GAIA Agent UI - Install Script (PowerShell)
# Usage: irm https://raw.githubusercontent.com/amd/gaia/main/scripts/install-ui.ps1 | iex
#
# Installs GAIA Agent UI globally via npm. After install, run `gaia-ui` from anywhere.

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  GAIA Agent UI Installer" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ── Prerequisites ────────────────────────────────────────────────────────────

Write-Host "Checking prerequisites..." -ForegroundColor Yellow

# Check Node.js
try {
    $nodeVersion = & node -v 2>$null
    Write-Host "  Node.js: $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Node.js is not installed." -ForegroundColor Red
    Write-Host "  Install Node.js 18+ from https://nodejs.org"
    exit 1
}

$nodeMajor = [int]($nodeVersion -replace 'v','').Split('.')[0]
if ($nodeMajor -lt 18) {
    Write-Host "  ERROR: Node.js 18+ is required. Current version: $nodeVersion" -ForegroundColor Red
    exit 1
}

# Check npm
try {
    $npmVersion = & npm.cmd -v 2>$null
    Write-Host "  npm: v$npmVersion" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: npm is not installed." -ForegroundColor Red
    exit 1
}

# Check Python gaia (optional)
try {
    $gaiaVersion = & gaia --version 2>$null
    Write-Host "  gaia CLI: installed" -ForegroundColor Green
} catch {
    Write-Host "  WARNING: 'gaia' CLI not found (optional)" -ForegroundColor Yellow
    Write-Host "    Install with: pip install amd-gaia"
    Write-Host "    Required for full functionality (LLM backend)"
}

Write-Host ""

# ── Install ──────────────────────────────────────────────────────────────────

Write-Host "Installing GAIA Agent UI..." -ForegroundColor Yellow
& npm.cmd install -g @amd-gaia/agent-ui@latest
if ($LASTEXITCODE -ne 0) { throw "Failed to install GAIA Agent UI" }

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  GAIA Agent UI installed successfully!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Usage:"
Write-Host "    gaia-ui              Start the app (backend + browser)"
Write-Host "    gaia-ui --serve      Serve frontend only"
Write-Host "    gaia-ui --help       Show all options"
Write-Host ""
Write-Host "  Prerequisites for full functionality:"
Write-Host "    pip install amd-gaia   Install Python backend"
Write-Host "    lemonade-server serve  Start LLM server"
Write-Host ""
Write-Host "  Documentation: https://amd-gaia.ai/guides/chat-ui"
Write-Host ""
