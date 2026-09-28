# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

<#
.SYNOPSIS
    Build the GAIA Windows installer from already-built binaries.

.DESCRIPTION
    Packages gaia-tui.exe and gaia-agent.exe into a single per-user .exe you
    can hand to someone else (installer/nsis/gaia.nsi does the laying down).

    It does NOT build the binaries — it packages them. Build them first:

        cd tui; go build -o bin/gaia-tui.exe ./cmd/gaia
        python hub/agents/gaia/python/packaging/freeze.py --onefile

    Both binaries are verified to exist and to be real Windows executables
    before makensis runs, so a missing or half-written input fails here with
    the command that produces it, rather than shipping an installer that
    installs nothing.

.PARAMETER TuiBinary
    Path to gaia-tui.exe. Defaults to tui\bin\gaia-tui.exe.

.PARAMETER AgentBinary
    Path to the frozen gaia-agent.exe. Defaults to the one-file output of
    freeze.py: hub\agents\gaia\python\packaging\dist\gaia-agent.exe.

.PARAMETER Version
    Version stamped into the installer. Defaults to __version__ in
    src\gaia\version.py.

.PARAMETER OutputDir
    Where the installer is written. Defaults to dist\installer.

.PARAMETER MakeNsis
    Path to makensis.exe. Defaults to whatever is on PATH, then the standard
    NSIS install locations.

.EXAMPLE
    .\installer\scripts\build-gaia-installer.ps1

.EXAMPLE
    .\installer\scripts\build-gaia-installer.ps1 -Version 0.23.1-mybranch `
        -AgentBinary C:\builds\gaia-agent.exe
#>

param(
    [string]$TuiBinary,
    [string]$AgentBinary,
    [string]$Version,
    [string]$OutputDir,
    [string]$MakeNsis
)

$ErrorActionPreference = "Stop"

# Every failure here is something the caller can fix, so it is printed as the
# instruction and nothing else -- a PowerShell stack trace would bury it.
function Fail {
    param([string]$Message)
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host ""
    exit 1
}

$REPO_ROOT = (Resolve-Path "$PSScriptRoot\..\..").Path
$NSIS_SCRIPT = Join-Path $REPO_ROOT "installer\nsis\gaia.nsi"

if (-not $TuiBinary)   { $TuiBinary   = Join-Path $REPO_ROOT "tui\bin\gaia-tui.exe" }
if (-not $AgentBinary) { $AgentBinary = Join-Path $REPO_ROOT "hub\agents\gaia\python\packaging\dist\gaia-agent.exe" }
if (-not $OutputDir)   { $OutputDir   = Join-Path $REPO_ROOT "dist\installer" }

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  GAIA Windows Installer Builder" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# ── Inputs ───────────────────────────────────────────────────────────────────

Write-Host "[1/4] Checking inputs..." -ForegroundColor Yellow

function Assert-Executable {
    param(
        [string]$Path,
        [string]$Label,
        [string]$BuildCommand
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Fail "$Label not found at`n    $Path`nBuild it first:`n    $BuildCommand`nOr point this script at an existing one with the matching parameter."
    }
    $item = Get-Item -LiteralPath $Path
    if ($item.Length -lt 1MB) {
        Fail "$Label at`n    $Path`nis only $($item.Length) bytes, which is far too small to be a real build. Delete it and re-run:`n    $BuildCommand"
    }
    # A frozen build that died mid-write, or a Git LFS pointer, is a file with
    # the right name and no MZ header. Catch it here rather than at install time
    # on someone else's machine.
    $header = [System.IO.File]::ReadAllBytes($Path)[0..1]
    if ($header[0] -ne 0x4D -or $header[1] -ne 0x5A) {
        Fail "$Label at`n    $Path`nis not a Windows executable (no MZ header). Rebuild it:`n    $BuildCommand"
    }
    $sizeMb = [math]::Round($item.Length / 1MB, 1)
    Write-Host "  $Label`: $Path ($sizeMb MB)" -ForegroundColor Green
}

Assert-Executable -Path $TuiBinary -Label "gaia-tui.exe" `
    -BuildCommand "cd tui; go build -o bin/gaia-tui.exe ./cmd/gaia"
Assert-Executable -Path $AgentBinary -Label "gaia-agent.exe" `
    -BuildCommand "python hub/agents/gaia/python/packaging/freeze.py --onefile"

$TuiBinary = (Resolve-Path -LiteralPath $TuiBinary).Path
$AgentBinary = (Resolve-Path -LiteralPath $AgentBinary).Path

if (-not (Test-Path -LiteralPath $NSIS_SCRIPT -PathType Leaf)) {
    Fail "The installer script is missing at`n    $NSIS_SCRIPT`nThis script must be run from a full GAIA checkout."
}

# ── Version ──────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "[2/4] Resolving version..." -ForegroundColor Yellow

if (-not $Version) {
    $versionFile = Join-Path $REPO_ROOT "src\gaia\version.py"
    if (-not (Test-Path -LiteralPath $versionFile -PathType Leaf)) {
        Fail "Cannot read the version: $versionFile does not exist. Pass -Version explicitly."
    }
    $match = Select-String -LiteralPath $versionFile -Pattern '^__version__\s*=\s*"([^"]+)"' |
        Select-Object -First 1
    if (-not $match) {
        Fail "No __version__ assignment found in $versionFile. Pass -Version explicitly."
    }
    $Version = $match.Matches[0].Groups[1].Value
}

# VIProductVersion needs exactly four numeric parts, so a version with a
# suffix ("0.23.1-mybranch") keeps its full text everywhere the user sees it
# and contributes only its numeric head here.
$numeric = ($Version -split '[^0-9.]')[0].TrimEnd('.')
$parts = @($numeric -split '\.' | Where-Object { $_ -ne '' })
if ($parts.Count -eq 0) {
    Fail "Version '$Version' has no numeric part, so the installer's file-version resource cannot be built. Use something like 0.23.1 or 0.23.1-mybranch."
}
while ($parts.Count -lt 4) { $parts += '0' }
$version4 = ($parts[0..3]) -join '.'

Write-Host "  Version: $Version (file version $version4)" -ForegroundColor Green

# ── makensis ────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "[3/4] Locating makensis..." -ForegroundColor Yellow

if (-not $MakeNsis) {
    $onPath = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($onPath) {
        $MakeNsis = $onPath.Source
    } else {
        $candidates = @(
            (Join-Path ${env:ProgramFiles(x86)} "NSIS\makensis.exe"),
            (Join-Path $env:ProgramFiles "NSIS\makensis.exe")
        )
        $MakeNsis = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
            Select-Object -First 1
    }
}
if (-not $MakeNsis -or -not (Test-Path -LiteralPath $MakeNsis -PathType Leaf)) {
    Fail "makensis.exe not found. Install NSIS 3.x:`n    winget install NSIS.NSIS`nThen re-run, or pass -MakeNsis <path to makensis.exe>."
}
Write-Host "  makensis: $MakeNsis" -ForegroundColor Green

# ── Build ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "[4/4] Building installer..." -ForegroundColor Yellow

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$OutputDir = (Resolve-Path -LiteralPath $OutputDir).Path
$outFile = Join-Path $OutputDir "gaia-setup-$Version.exe"
if (Test-Path -LiteralPath $outFile) { Remove-Item -LiteralPath $outFile -Force }

$nsisArgs = @(
    "/V3",
    "/DGAIA_VERSION=$Version",
    "/DVERSION_4PART=$version4",
    "/DTUI_BINARY=$TuiBinary",
    "/DAGENT_BINARY=$AgentBinary",
    "/DOUT_FILE=$outFile",
    $NSIS_SCRIPT
)

& $MakeNsis $nsisArgs
if ($LASTEXITCODE -ne 0) {
    Fail "makensis failed with exit code $LASTEXITCODE. The output above names the line it stopped on."
}
if (-not (Test-Path -LiteralPath $outFile -PathType Leaf)) {
    Fail "makensis reported success but $outFile was not written. Check the OutFile line in $NSIS_SCRIPT."
}

$installerSize = [math]::Round((Get-Item -LiteralPath $outFile).Length / 1MB, 1)

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Installer built" -ForegroundColor Green
Write-Host "  $outFile" -ForegroundColor Green
Write-Host "  $installerSize MB" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Send that file. On the target machine it installs per-user (no" -ForegroundColor Gray
Write-Host "admin rights), puts gaia-tui on PATH, and leaves Lemonade and the" -ForegroundColor Gray
Write-Host "models to 'gaia init'." -ForegroundColor Gray
