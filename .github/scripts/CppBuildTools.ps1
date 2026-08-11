# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

<#
.SYNOPSIS
    Cache-validation and install helpers for the C++ toolchain on self-hosted runners.

.DESCRIPTION
    Dot-sourced by .github/scripts/Ensure-CppBuildTools.ps1 and covered by
    .github/scripts/tests/CppBuildTools.Tests.ps1. Every function here is pure
    path/exit-code logic so the decision that broke the STX runner (issue #2817)
    can be unit tested off Windows.

    Targets Windows PowerShell 5.1 -- the STX job runs `shell: powershell`.
#>

function Test-IsWindowsHost {
    # $IsWindows exists in PowerShell 6+. Windows PowerShell 5.1 predates it and
    # only ever runs on Windows.
    if (Get-Variable -Name IsWindows -ErrorAction SilentlyContinue) {
        return [bool](Get-Variable -Name IsWindows -ValueOnly)
    }
    return $true
}

function Get-CMakeExeName {
    if (Test-IsWindowsHost) { return 'cmake.exe' }
    return 'cmake'
}

function Get-CppToolsRoot {
    <#
    .SYNOPSIS
        Directory the job installs its C++ toolchain into.

    .DESCRIPTION
        Deliberately NOT $env:TEMP. Windows Temp cleanup on the self-hosted STX
        runner deleted half of a cached CMake install and left the rest, which is
        what issue #2817 was. RUNNER_TOOL_CACHE lives under the runner's work
        directory, is not swept by the OS, and persists between jobs.
    #>
    if (-not [string]::IsNullOrWhiteSpace($env:GAIA_CI_TOOLS_DIR)) {
        return $env:GAIA_CI_TOOLS_DIR
    }
    if (-not [string]::IsNullOrWhiteSpace($env:RUNNER_TOOL_CACHE)) {
        return (Join-Path $env:RUNNER_TOOL_CACHE 'gaia-cpp')
    }
    throw ("No persistent tools directory available: neither GAIA_CI_TOOLS_DIR nor " +
           "RUNNER_TOOL_CACHE is set. Set GAIA_CI_TOOLS_DIR to a directory the runner " +
           "keeps between jobs (do NOT use TEMP -- see issue #2817), or run this from a " +
           "GitHub Actions job where RUNNER_TOOL_CACHE is defined.")
}

function Get-CMakeInstallDir {
    # Single source of truth for the install path: the cache lookup in
    # Ensure-CppBuildTools.ps1 and the extraction in Install-CMake must agree, or
    # the cache silently never hits and every run re-downloads.
    param(
        [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][string]$ToolsRoot
    )
    return (Join-Path $ToolsRoot "cmake-$Version-windows-x86_64")
}

function Get-W64DevkitInstallDir {
    param([Parameter(Mandatory)][string]$ToolsRoot)
    return (Join-Path $ToolsRoot 'w64devkit')
}

function Reset-ToolDirectory {
    <#
    .SYNOPSIS
        Delete a tool directory so a re-extraction starts from empty.

    .DESCRIPTION
        Without this, Expand-Archive -Force merges into whatever survived, so a
        partially deleted install can persist across runs forever.
    #>
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "Reset-ToolDirectory: refusing to delete an empty path."
    }
    $full = [System.IO.Path]::GetFullPath($Path)
    $parent = [System.IO.Path]::GetDirectoryName($full)
    if ([string]::IsNullOrEmpty($parent)) {
        throw "Reset-ToolDirectory: refusing to delete filesystem root '$full'."
    }
    if (Test-Path -LiteralPath $full) {
        Write-Host "Removing stale tool directory: $full"
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}

function Find-CMakeModulesDir {
    <#
    .SYNOPSIS
        Locate <install root>/share/cmake-<major.minor>/Modules, or return $null.

    .DESCRIPTION
        Requires CMakeSystemSpecificInformation.cmake specifically, because that is
        the file CMake itself looks for when it resolves CMAKE_ROOT. A directory
        that exists but has been partially emptied is as broken as a missing one.
    #>
    param([Parameter(Mandatory)][string]$InstallRoot)

    $shareDir = Join-Path $InstallRoot 'share'
    if (-not (Test-Path -LiteralPath $shareDir -PathType Container)) { return $null }

    $candidates = Get-ChildItem -LiteralPath $shareDir -Directory -Filter 'cmake-*' -ErrorAction SilentlyContinue
    foreach ($candidate in $candidates) {
        $modules = Join-Path $candidate.FullName 'Modules'
        $sentinel = Join-Path $modules 'CMakeSystemSpecificInformation.cmake'
        if (Test-Path -LiteralPath $sentinel -PathType Leaf) { return $modules }
    }
    return $null
}

function Invoke-ToolProbe {
    <#
    .SYNOPSIS
        Run a tool and return its exit code plus combined stdout/stderr.

    .DESCRIPTION
        $ErrorActionPreference is forced to Continue for the duration: in Windows
        PowerShell 5.1, merging a native command's stderr with 2>&1 turns each
        stderr line into an ErrorRecord, which under 'Stop' would abort the probe
        on harmless tool chatter.
    #>
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @()
    )

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Exe @Arguments 2>&1 | Out-String
        return [PSCustomObject]@{ ExitCode = $LASTEXITCODE; Output = $output }
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Test-ToolInstallation {
    <#
    .SYNOPSIS
        $true when <BinDir>/<ExecutableName> exists and exits 0 for a version probe.
    #>
    param(
        [Parameter(Mandatory)][string]$BinDir,
        [Parameter(Mandatory)][string]$ExecutableName,
        [string[]]$VersionArgs = @('--version')
    )

    $exe = Join-Path $BinDir $ExecutableName
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        Write-Host "  probe: $exe is missing"
        return $false
    }
    try {
        $probe = Invoke-ToolProbe -Exe $exe -Arguments $VersionArgs
    } catch {
        Write-Host "  probe: $exe could not be executed -- $($_.Exception.Message)"
        return $false
    }
    if ($probe.ExitCode -ne 0) {
        Write-Host "  probe: '$ExecutableName $($VersionArgs -join ' ')' exited $($probe.ExitCode)"
        return $false
    }
    return $true
}

function Test-CMakeInstallation {
    <#
    .SYNOPSIS
        $true only when a CMake install can actually configure a project.

    .DESCRIPTION
        Checks the Modules tree, NOT just bin/cmake.exe, and NOT just the exit code.
        CMAKE_ROOT resolves to share/cmake-<major.minor>, so an install with bin/
        but no Modules/ dies at configure time with
        "CMake Error: Could not find CMAKE_ROOT !!!". Windows Temp cleanup produced
        exactly that half-install on the STX runner and a cmake.exe-only check kept
        declaring it healthy, blocking every cpp/** PR (issue #2817).

        Exit codes cannot detect this: a CMake missing its Modules tree prints the
        CMAKE_ROOT error to stderr and still exits 0, for both `--version` and
        `--help-module-list` (measured on 4.4.2). So both signals are required --
        the Modules tree must be on disk AND CMake must not report a broken root.
        Requiring both also means an unusual install layout or a reworded CMake
        error still leaves one signal standing.

        Do not simplify this back to `Test-Path cmake.exe` or to an exit-code check.
    #>
    param([Parameter(Mandatory)][string]$BinDir)

    if (-not (Test-ToolInstallation -BinDir $BinDir -ExecutableName (Get-CMakeExeName))) {
        return $false
    }

    $installRoot = Split-Path -Parent $BinDir
    $modules = Find-CMakeModulesDir -InstallRoot $installRoot
    if (-not $modules) {
        Write-Host "  probe: no populated share/cmake-*/Modules under $installRoot (CMAKE_ROOT would be unresolvable)"
        return $false
    }

    $probe = Invoke-ToolProbe -Exe (Join-Path $BinDir (Get-CMakeExeName)) -Arguments @('--version')
    if ($probe.Output -match 'Could not find CMAKE_ROOT|Modules directory not found') {
        Write-Host "  probe: cmake reports a broken CMAKE_ROOT despite modules at $modules"
        return $false
    }

    Write-Host "  probe: CMAKE_ROOT modules found at $modules"
    return $true
}

function Install-CMake {
    <#
    .SYNOPSIS
        Download and extract a pinned CMake into $ToolsRoot, replacing any stale copy.
    #>
    param(
        [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][string]$ToolsRoot
    )

    $installDir = Get-CMakeInstallDir -Version $Version -ToolsRoot $ToolsRoot
    $archiveName = Split-Path -Leaf $installDir
    $binDir = Join-Path $installDir 'bin'

    New-Item -ItemType Directory -Path $ToolsRoot -Force | Out-Null
    $zip = Join-Path $ToolsRoot "$archiveName.zip"
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }

    # Download before deleting the old copy: a network failure then leaves the
    # previous install intact instead of destroying a working toolchain.
    $url = "https://github.com/Kitware/CMake/releases/download/v$Version/$archiveName.zip"
    Write-Host "Downloading CMake v$Version from $url"
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing

    Reset-ToolDirectory -Path $installDir
    Write-Host "Extracting to $installDir"
    Expand-Archive -LiteralPath $zip -DestinationPath $ToolsRoot -Force
    Remove-Item -LiteralPath $zip -Force

    if (-not (Test-CMakeInstallation -BinDir $binDir)) {
        throw ("CMake v$Version was downloaded and extracted to $installDir but the install " +
               "is still incomplete. Delete that directory on the runner and re-run; if it " +
               "recurs, the tools root ($ToolsRoot) is being swept by something.")
    }
    Write-Host "CMake v$Version installed at $binDir"
    return $binDir
}

function Install-W64Devkit {
    <#
    .SYNOPSIS
        Download and extract a pinned w64devkit (MinGW-w64) into $ToolsRoot.
    #>
    param(
        [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][string]$ToolsRoot
    )

    $installDir = Get-W64DevkitInstallDir -ToolsRoot $ToolsRoot
    $binDir = Join-Path $installDir 'bin'

    New-Item -ItemType Directory -Path $ToolsRoot -Force | Out-Null
    $installer = Join-Path $ToolsRoot "w64devkit-x64-$Version.7z.exe"
    if (Test-Path -LiteralPath $installer) { Remove-Item -LiteralPath $installer -Force }

    $url = "https://github.com/skeeto/w64devkit/releases/download/v$Version/w64devkit-x64-$Version.7z.exe"
    Write-Host "Downloading w64devkit (MinGW-w64) v$Version from $url"
    Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing

    Reset-ToolDirectory -Path $installDir
    Write-Host "Extracting to $installDir"
    & $installer "-o$ToolsRoot" -y | Out-Null
    Remove-Item -LiteralPath $installer -Force

    if (-not (Test-ToolInstallation -BinDir $binDir -ExecutableName 'g++.exe')) {
        throw ("w64devkit v$Version was downloaded and extracted to $installDir but g++ is " +
               "missing or not runnable. Delete that directory on the runner and re-run.")
    }
    Write-Host "w64devkit v$Version installed at $binDir"
    return $binDir
}

function Add-PathEntry {
    <#
    .SYNOPSIS
        Prepend a directory to PATH for this process and for later workflow steps.
    #>
    param([Parameter(Mandatory)][string]$Directory)

    $env:PATH = "$Directory$([System.IO.Path]::PathSeparator)$env:PATH"
    if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_PATH)) {
        # AppendAllText writes UTF-8 without a BOM; Add-Content/Out-File on
        # Windows PowerShell 5.1 would write ANSI or inject a mid-file BOM.
        [System.IO.File]::AppendAllText($env:GITHUB_PATH, $Directory + [Environment]::NewLine)
    }
    Write-Host "PATH += $Directory"
}
