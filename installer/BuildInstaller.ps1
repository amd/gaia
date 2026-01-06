# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# This script is used for building the GAIA installer

# Add parameter for OGA token which is required for NPU functionality
param(
    [Parameter()]
    [string]$OGA_TOKEN = ""
)

# Define the path to makensis.exe
$nsisPath = "C:\Program Files (x86)\NSIS\makensis.exe"

# Build the installer
if ([string]::IsNullOrEmpty($OGA_TOKEN)) {
    & $nsisPath "Installer.nsi"
} else {
    & $nsisPath "/DOGA_TOKEN=$OGA_TOKEN" "Installer.nsi"
}

# Verify the installer was created successfully
if (Test-Path "gaia-windows-setup.exe") {
    Write-Host "gaia-windows-setup.exe has been created successfully."
} else {
    Write-Host "gaia-windows-setup.exe was not found."
    exit 1
}