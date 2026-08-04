# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

<#
.SYNOPSIS
    Make a working CMake and C++ compiler available to the rest of the job.

.DESCRIPTION
    Used by the STX self-hosted integration job in .github/workflows/build_cpp.yml.

    Every candidate toolchain -- one already on PATH, one cached from a previous
    run, one bundled with Visual Studio -- is functionally probed before it is
    accepted. A candidate that fails the probe is discarded and the next source is
    tried; if nothing works the tool is re-downloaded into a clean directory. If
    that also fails the script throws rather than leaving a broken tool on PATH.

.PARAMETER CMakeVersion
    Pinned CMake version downloaded when no usable CMake is found.

.PARAMETER W64DevkitVersion
    Pinned w64devkit (MinGW-w64) version downloaded when MSVC and g++ are absent.

.EXAMPLE
    .\.github\scripts\Ensure-CppBuildTools.ps1 -CMakeVersion 3.31.4 -W64DevkitVersion 2.5.0
#>

[CmdletBinding()]
param(
    [string]$CMakeVersion = '3.31.4',
    [string]$W64DevkitVersion = '2.5.0'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

. (Join-Path $PSScriptRoot 'CppBuildTools.ps1')

$toolsRoot = Get-CppToolsRoot
Write-Host "C++ tools root: $toolsRoot"

# ---------------------------------------------------------------------------
# CMake: PATH -> previous download -> Visual Studio -> fresh download
# ---------------------------------------------------------------------------
$cmakeBin = $null

$onPath = Get-Command cmake -ErrorAction SilentlyContinue
if ($onPath) {
    $candidate = Split-Path -Parent $onPath.Source
    Write-Host "Checking CMake already on PATH: $candidate"
    if (Test-CMakeInstallation -BinDir $candidate) {
        Write-Host "[OK] Using CMake from PATH"
        $cmakeBin = $candidate
    } else {
        Write-Host "::warning::CMake on PATH at $candidate is incomplete -- ignoring it"
    }
}

if (-not $cmakeBin) {
    $cached = Join-Path (Get-CMakeInstallDir -Version $CMakeVersion -ToolsRoot $toolsRoot) 'bin'
    Write-Host "Checking cached CMake: $cached"
    if (Test-CMakeInstallation -BinDir $cached) {
        Write-Host "[OK] Using cached CMake"
        $cmakeBin = $cached
    } else {
        Write-Host "Cached CMake is absent or incomplete -- it will be re-downloaded"
    }
}

if (-not $cmakeBin -and (Test-IsWindowsHost)) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $vswhere) {
        $vsCmake = & $vswhere -latest `
            -requires Microsoft.VisualStudio.Component.VC.CMake.Project `
            -find "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin" 2>$null
        if ($vsCmake) {
            $vsCmake = @($vsCmake)[0]
            Write-Host "Checking VS-bundled CMake: $vsCmake"
            if (Test-CMakeInstallation -BinDir $vsCmake) {
                Write-Host "[OK] Using VS-bundled CMake"
                $cmakeBin = $vsCmake
            } else {
                Write-Host "::warning::VS-bundled CMake at $vsCmake is incomplete -- ignoring it"
            }
        }
    }
}

if (-not $cmakeBin) {
    $cmakeBin = Install-CMake -Version $CMakeVersion -ToolsRoot $toolsRoot
}

Add-PathEntry -Directory $cmakeBin

# ---------------------------------------------------------------------------
# C++ compiler: MSVC -> g++ on PATH -> previous download -> fresh download
# ---------------------------------------------------------------------------
$hasCompiler = $false

if (Test-IsWindowsHost) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $vswhere) {
        $vsPath = & $vswhere -latest -property installationPath 2>$null
        if ($vsPath) {
            Write-Host "[OK] MSVC found at $(@($vsPath)[0])"
            $hasCompiler = $true
        }
    }
}

if (-not $hasCompiler) {
    $gxx = Get-Command g++ -ErrorAction SilentlyContinue
    if ($gxx) {
        Write-Host "[OK] g++ found on PATH at $($gxx.Source)"
        $hasCompiler = $true
    }
}

if (-not $hasCompiler) {
    $cachedGxx = Join-Path (Get-W64DevkitInstallDir -ToolsRoot $toolsRoot) 'bin'
    Write-Host "Checking cached w64devkit: $cachedGxx"
    if (Test-ToolInstallation -BinDir $cachedGxx -ExecutableName 'g++.exe') {
        Write-Host "[OK] Using cached w64devkit"
        Add-PathEntry -Directory $cachedGxx
        $hasCompiler = $true
    } else {
        Write-Host "Cached w64devkit is absent or incomplete -- it will be re-downloaded"
        Add-PathEntry -Directory (Install-W64Devkit -Version $W64DevkitVersion -ToolsRoot $toolsRoot)
        $hasCompiler = $true
    }
}

Write-Host "Build tools ready"
