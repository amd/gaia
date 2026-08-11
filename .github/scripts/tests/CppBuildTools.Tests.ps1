# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

<#
.SYNOPSIS
    Unit tests for the C++ build-tool cache validation logic (issue #2817).

.DESCRIPTION
    The STX self-hosted runner cached CMake under $env:TEMP and decided whether to
    re-download it by testing `bin\cmake.exe` alone. Windows Temp cleanup deleted
    `share\` and left `bin\`, so the guard saw a healthy cache, skipped the
    re-download and put a CMake that cannot find CMAKE_ROOT on PATH. Every cpp/**
    PR then failed at configure time.

    These tests pin the repaired behaviour: a cache is valid only when the binary
    AND its Modules tree are present AND the binary actually runs.

    Runs on Linux/macOS (the fake CMake binaries are shell scripts, which Windows
    cannot execute as `cmake.exe`). The logic under test is path/exit-code logic,
    not Windows API logic, so it validates the decision that broke on the runner.

.EXAMPLE
    pwsh -File .github/scripts/tests/CppBuildTools.Tests.ps1
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent $PSScriptRoot) 'CppBuildTools.ps1')

$script:Passed = 0
$script:Failed = 0

function Assert-Equal {
    param($Expected, $Actual, [string]$Name)
    if ($Expected -eq $Actual) {
        $script:Passed++
        Write-Host "  [PASS] $Name"
    } else {
        $script:Failed++
        Write-Host "  [FAIL] $Name (expected '$Expected', got '$Actual')"
    }
}

function Assert-Throws {
    param([scriptblock]$Action, [string]$Name)
    try {
        & $Action
        $script:Failed++
        Write-Host "  [FAIL] $Name (expected a terminating error, none was raised)"
    } catch {
        $script:Passed++
        Write-Host "  [PASS] $Name"
    }
}

function New-TestRoot {
    $path = Join-Path ([System.IO.Path]::GetTempPath()) ("gaia-cpptools-test-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $path -Force | Out-Null
    return $path
}

# Builds a CMake install tree that mirrors the real archive layout:
#   <root>/bin/cmake[.exe]
#   <root>/share/cmake-3.31/Modules/CMakeSystemSpecificInformation.cmake
function New-FakeCMakeInstall {
    param(
        [Parameter(Mandatory)][string]$Root,
        [switch]$NoBinary,
        [switch]$NoShare,
        [switch]$EmptyModules,
        [switch]$BinaryFails,
        [switch]$ReportsBrokenRoot
    )

    $binDir = Join-Path $Root 'bin'
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null

    if (-not $NoBinary) {
        $exe = Join-Path $binDir (Get-CMakeExeName)
        $exitCode = if ($BinaryFails) { 1 } else { 0 }
        # Real CMake prints the CMAKE_ROOT error to stderr and still exits 0
        # (measured on 4.4.2), so the fake reproduces that exact combination.
        $complaint = if ($ReportsBrokenRoot) {
            "echo 'CMake Error: Could not find CMAKE_ROOT !!!' 1>&2`n"
        } else { '' }
        $body = "#!/bin/sh`n$complaint" + "echo 'cmake version 3.31.4'`nexit $exitCode`n"
        Set-Content -LiteralPath $exe -Value $body -NoNewline
        if (-not (Test-IsWindowsHost)) { & chmod '+x' $exe }
    }

    if (-not $NoShare) {
        $modules = Join-Path (Join-Path (Join-Path $Root 'share') 'cmake-3.31') 'Modules'
        New-Item -ItemType Directory -Path $modules -Force | Out-Null
        if (-not $EmptyModules) {
            Set-Content -LiteralPath (Join-Path $modules 'CMakeSystemSpecificInformation.cmake') -Value '# fake'
        }
    }

    return $binDir
}

Write-Host "`nTest-CMakeInstallation"
$root = New-TestRoot
try {
    $bin = New-FakeCMakeInstall -Root (Join-Path $root 'complete')
    Assert-Equal $true (Test-CMakeInstallation -BinDir $bin) 'complete install is valid'

    # The #2817 regression itself: bin/ survived Temp cleanup, share/ did not.
    $bin = New-FakeCMakeInstall -Root (Join-Path $root 'no-share') -NoShare
    Assert-Equal $false (Test-CMakeInstallation -BinDir $bin) 'binary present but share/ deleted is INVALID'
    # Negative control: the check this replaced accepted that same broken install.
    Assert-Equal $true (Test-Path -LiteralPath (Join-Path $bin (Get-CMakeExeName))) `
        'negative control: the old cmake.exe-only check would have accepted it'

    $bin = New-FakeCMakeInstall -Root (Join-Path $root 'empty-modules') -EmptyModules
    Assert-Equal $false (Test-CMakeInstallation -BinDir $bin) 'Modules/ without CMakeSystemSpecificInformation.cmake is invalid'

    # CMake exits 0 while printing "Could not find CMAKE_ROOT", so the text of the
    # probe matters, not just its exit code.
    $bin = New-FakeCMakeInstall -Root (Join-Path $root 'complains') -ReportsBrokenRoot
    Assert-Equal $false (Test-CMakeInstallation -BinDir $bin) 'cmake reporting a broken CMAKE_ROOT is invalid even at exit 0'
    Assert-Equal 0 (Invoke-ToolProbe -Exe (Join-Path $bin (Get-CMakeExeName)) -Arguments @('--version')).ExitCode `
        'negative control: that same install exits 0, so an exit-code-only probe would accept it'

    $bin = New-FakeCMakeInstall -Root (Join-Path $root 'no-binary') -NoBinary
    Assert-Equal $false (Test-CMakeInstallation -BinDir $bin) 'missing binary is invalid'

    $bin = New-FakeCMakeInstall -Root (Join-Path $root 'broken-binary') -BinaryFails
    Assert-Equal $false (Test-CMakeInstallation -BinDir $bin) 'binary that exits non-zero is invalid'

    Assert-Equal $false (Test-CMakeInstallation -BinDir (Join-Path $root 'does-not-exist')) 'absent directory is invalid'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "`nTest-ToolInstallation (generic binary probe)"
$root = New-TestRoot
try {
    $bin = New-FakeCMakeInstall -Root (Join-Path $root 'ok')
    Assert-Equal $true  (Test-ToolInstallation -BinDir $bin -ExecutableName (Get-CMakeExeName)) 'runnable binary is valid'
    $bin = New-FakeCMakeInstall -Root (Join-Path $root 'bad') -BinaryFails
    Assert-Equal $false (Test-ToolInstallation -BinDir $bin -ExecutableName (Get-CMakeExeName)) 'failing binary is invalid'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "`nInvoke-ToolProbe"
$root = New-TestRoot
try {
    # Windows PowerShell 5.1 turns merged native stderr into ErrorRecords, which
    # under $ErrorActionPreference='Stop' would abort the probe on tool chatter.
    $bin = New-FakeCMakeInstall -Root (Join-Path $root 'noisy') -ReportsBrokenRoot
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Stop'
    try {
        $probe = Invoke-ToolProbe -Exe (Join-Path $bin (Get-CMakeExeName)) -Arguments @('--version')
        Assert-Equal $true ($probe.Output -match 'Could not find CMAKE_ROOT') 'captures stderr without aborting under EAP=Stop'
        Assert-Equal 'Stop' $ErrorActionPreference 'leaves the caller ErrorActionPreference untouched'
    } finally {
        $ErrorActionPreference = $saved
    }
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "`nInstall path helpers"
Assert-Equal (Join-Path '/tools' 'cmake-3.31.4-windows-x86_64') (Get-CMakeInstallDir -Version '3.31.4' -ToolsRoot '/tools') `
    'cache lookup and extraction share one CMake install path'
Assert-Equal (Join-Path '/tools' 'w64devkit') (Get-W64DevkitInstallDir -ToolsRoot '/tools') `
    'cache lookup and extraction share one w64devkit install path'

Write-Host "`nGet-CppToolsRoot"
$savedOverride = $env:GAIA_CI_TOOLS_DIR
$savedToolCache = $env:RUNNER_TOOL_CACHE
try {
    $env:GAIA_CI_TOOLS_DIR = '/tmp/explicit-tools'
    $env:RUNNER_TOOL_CACHE = '/tmp/tool-cache'
    Assert-Equal '/tmp/explicit-tools' (Get-CppToolsRoot) 'explicit override wins'

    $env:GAIA_CI_TOOLS_DIR = ''
    Assert-Equal (Join-Path '/tmp/tool-cache' 'gaia-cpp') (Get-CppToolsRoot) 'falls back to the runner tool cache'

    $env:RUNNER_TOOL_CACHE = ''
    Assert-Throws { Get-CppToolsRoot } 'throws instead of silently using TEMP when no persistent dir is known'
} finally {
    $env:GAIA_CI_TOOLS_DIR = $savedOverride
    $env:RUNNER_TOOL_CACHE = $savedToolCache
}

Write-Host "`nReset-ToolDirectory"
$root = New-TestRoot
try {
    $stale = Join-Path $root 'stale'
    New-Item -ItemType Directory -Path (Join-Path $stale 'bin') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path (Join-Path $stale 'bin') 'leftover.txt') -Value 'x'
    Reset-ToolDirectory -Path $stale
    Assert-Equal $false (Test-Path -LiteralPath $stale) 'stale directory is removed before re-extraction'

    Reset-ToolDirectory -Path (Join-Path $root 'never-existed')
    Assert-Equal $true $true 'absent directory is a no-op'

    Assert-Throws { Reset-ToolDirectory -Path ([System.IO.Path]::DirectorySeparatorChar.ToString()) } 'refuses to delete a filesystem root'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Passed: $script:Passed  Failed: $script:Failed"
if ($script:Failed -gt 0) { exit 1 }
exit 0
