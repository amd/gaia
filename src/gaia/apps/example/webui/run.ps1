# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Local development script for Example App - builds and runs the app

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Example App - MCP Integration Demo" -ForegroundColor Cyan

# Install dependencies locally (required for Electron on Windows)
Write-Host "Installing dependencies..." -ForegroundColor Yellow
Set-Location $ScriptDir
npm install --no-workspaces

# Build installer
Write-Host "Building installer..." -ForegroundColor Yellow
npm run make
Write-Host "Build complete: $ScriptDir\out\make\" -ForegroundColor Green

# Run in development mode
Write-Host "Starting development mode..." -ForegroundColor Yellow
Set-Location $ScriptDir
npm start