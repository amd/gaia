# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Local development script for JAX - builds and runs the app

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = git rev-parse --show-toplevel

Write-Host "JAX - Jira Agent Experience" -ForegroundColor Cyan

# Build installer
Write-Host "Building installer..." -ForegroundColor Yellow
Set-Location $RootDir
Remove-Item -Recurse -Force node_modules, package-lock.json -ErrorAction SilentlyContinue
Set-Location $ScriptDir
npm install --prefix .
npm run make
Write-Host "Build complete: $ScriptDir\out\make\" -ForegroundColor Green

# Restore workspace and run dev
Write-Host "Starting development mode..." -ForegroundColor Yellow
Set-Location $RootDir
Write-Host "Restoring workspace dependencies..." -ForegroundColor Yellow
npm install
New-Item -ItemType Directory -Force -Path "$ScriptDir\node_modules" | Out-Null
Remove-Item -Recurse -Force "$ScriptDir\node_modules\electron" -ErrorAction SilentlyContinue
Copy-Item -Recurse -Force "$RootDir\node_modules\electron" "$ScriptDir\node_modules\electron"
Set-Location $ScriptDir
npm start