# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# This script is used for testing the GAIA installer locally by building installer variants

# Add parameters for installer mode and token
param(
    [Parameter()]
    [ValidateSet("Generic", "Hybrid", "NPU")]
    [string]$Mode = "Generic",

    [Parameter()]
    [string]$OGA_TOKEN = ""
)

# Check if token is properly defined when NPU mode is selected
if ($Mode -eq "NPU") {
    if ([string]::IsNullOrEmpty($OGA_TOKEN)) {
        Write-Error "Error: GitHub token is required for NPU mode. Please provide a valid token using -OGA_TOKEN parameter."
        exit 1
    }
}

# Build the appropriate installer based on mode
switch ($Mode) {
    "Generic" {
        & "C:\Program Files (x86)\NSIS\makensis.exe" Installer.nsi
    }
    "Hybrid" {
        & "C:\Program Files (x86)\NSIS\makensis.exe" /DMODE=HYBRID Installer.nsi
    }
    "NPU" {
        & "C:\Program Files (x86)\NSIS\makensis.exe" /DMODE=NPU /DOGA_TOKEN="${OGA_TOKEN}" Installer.nsi
    }
}