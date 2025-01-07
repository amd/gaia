# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# This script is used for testing the GAIA installer locally by building all installer variants (NPU-only, GPU-only, and Hybrid)

# The following token is for access to ryzenai-sw-ea repository
$OGA_TOKEN = "<add token here>"

# Check if token is properly defined
if ([string]::IsNullOrEmpty($OGA_TOKEN) -or $OGA_TOKEN -eq "<add token here>") {
    Write-Error "Error: GitHub token is not properly configured. Please set a valid token in the script."
    exit 1
}

& "C:\Program Files (x86)\NSIS\makensis.exe" /DOGA_TOKEN="${OGA_TOKEN}" Installer.nsi
& "C:\Program Files (x86)\NSIS\makensis.exe" /DMODE=NPU /DOGA_TOKEN="${OGA_TOKEN}" Installer.nsi
& "C:\Program Files (x86)\NSIS\makensis.exe" /DMODE=HYBRID /DOGA_TOKEN="${OGA_TOKEN}" Installer.nsi