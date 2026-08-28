# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Build the Windows one-click installer for the flagship GAIA agent.
#
# Stages a verified payload, then packs it with makensis:
#   gaia-tui.exe                 the Go terminal UI
#   gaia-agent.exe               the frozen Python agent
#   lemonade-server-minimal.msi  the Lemonade bootstrap, installed during setup
#   pathmgr.ps1                  per-user PATH edits (install + uninstall)
#
# Every binary that comes off the network is SHA-256 checked against
# binaries.lock.json from the published @amd-gaia/gaia package BEFORE it is
# staged. A mismatch stops the build -- an installer must never carry a binary
# nobody verified.
#
# Locally-built binaries (-SidecarPath / -TuiPath) are staged as-is and reported
# as unverified, because there is nothing published to compare them against.
# That is the developer path; CI always takes the lock path.
#
#   .\installer\tui\build.ps1
#   .\installer\tui\build.ps1 -SidecarPath .\hub\agents\gaia\python\packaging\dist\gaia-agent.exe
#
[CmdletBinding()]
param(
    # Locally-built sidecar to bundle instead of the published one.
    [string]$SidecarPath,
    # Locally-built TUI to bundle instead of the published one.
    [string]$TuiPath,
    # Override the bundle version (defaults to the lock's agentVersion).
    [string]$Version,
    [string]$OutDir = "dist-installer",
    # Reuse an already-downloaded payload instead of re-fetching.
    [switch]$NoFetch
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$StageDir = Join-Path $RepoRoot 'installer\tui\build\stage'
$LockPath = Join-Path $RepoRoot 'installer\tui\build\binaries.lock.json'

function Fail([string]$What, [string]$Do, [string]$Where) {
    throw "$What`n  Try: $Do`n  See: $Where"
}

function Find-MakeNSIS {
    $cmd = Get-Command makensis.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($p in @(
            "${env:ProgramFiles(x86)}\NSIS\makensis.exe",
            "$env:ProgramFiles\NSIS\makensis.exe")) {
        if (Test-Path $p) { return $p }
    }
    Fail "makensis.exe was not found." `
        "install NSIS (winget install NSIS.NSIS) or put makensis.exe on PATH" `
        "https://nsis.sourceforge.io/Download"
}

# The lock is the source of truth for what a release contains and what it
# hashes to. Read it from the published package rather than a copy in this repo,
# so the installer can never bundle a binary the release does not describe.
function Get-Lock {
    if ((Test-Path $LockPath) -and $NoFetch) {
        Write-Host "[build] reusing cached lock: $LockPath"
        return (Get-Content $LockPath -Raw | ConvertFrom-Json)
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $LockPath) | Out-Null
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("gaia-lock-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    try {
        Write-Host "[build] fetching binaries.lock.json from @amd-gaia/gaia ..."
        Push-Location $tmp
        # npm.cmd, not npm: bare `npm` resolves to npm.ps1, which is not
        # StrictMode-clean and fails with "property 'Statement' cannot be found"
        # when dot-sourced into this runspace. No 2>&1 either -- in Windows
        # PowerShell 5.1 that wraps a native command's stderr in ErrorRecords.
        $npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue)
        if (-not $npm) {
            Fail "npm.cmd was not found on PATH." `
                "install Node.js 18+, or pass -NoFetch with a cached lock" $LockPath
        }
        & $npm.Source pack '@amd-gaia/gaia' --silent | Out-Null
        $packExit = $LASTEXITCODE
        Pop-Location
        if ($packExit -ne 0) {
            Fail "could not fetch @amd-gaia/gaia from npm (exit $packExit)" `
                "check network access to registry.npmjs.org, or pass -NoFetch with a cached lock" `
                $LockPath
        }
        $tgz = Get-ChildItem $tmp -Filter '*.tgz' | Select-Object -First 1
        if (-not $tgz) { Fail "npm pack produced no tarball in $tmp" "re-run without -NoFetch" $tmp }
        & tar -xzf $tgz.FullName -C $tmp
        $src = Join-Path $tmp 'package\binaries.lock.json'
        if (-not (Test-Path $src)) {
            Fail "the published package has no binaries.lock.json" `
                "check that @amd-gaia/gaia still ships the lock in its files[]" `
                'hub/agents/gaia/npm/package.json'
        }
        Copy-Item $src $LockPath -Force
        Get-Content $LockPath -Raw | ConvertFrom-Json
    } finally {
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# Download one component, then verify it against the lock BEFORE staging it.
function Get-VerifiedBinary($Component, [string]$PlatformKey, [string]$StageAs) {
    $entry = $Component.platforms.$PlatformKey
    if (-not $entry) {
        Fail "the lock has no '$PlatformKey' build for this component." `
            "build it locally and pass -SidecarPath/-TuiPath" $LockPath
    }
    $url = "$($Component.baseUrl)/$($entry.filename)"
    $dest = Join-Path $StageDir $StageAs
    $cached = (Test-Path $dest) -and ((Get-FileHash $dest -Algorithm SHA256).Hash.ToLower() -eq $entry.sha256.ToLower())
    if ($cached) {
        Write-Host "[build] $StageAs already staged and verified"
        return
    }
    Write-Host "[build] downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing

    $actual = (Get-FileHash $dest -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $entry.sha256.ToLower()) {
        Remove-Item $dest -Force -ErrorAction SilentlyContinue
        Fail ("SHA-256 mismatch for $($entry.filename): the download does not match the published lock.`n" +
              "    expected $($entry.sha256)`n    actual   $actual") `
            "re-run the build; if it repeats, the hub artifact or the lock is wrong -- do not bypass this check" `
            $LockPath
    }
    $size = (Get-Item $dest).Length
    if ($entry.size -and $size -ne $entry.size) {
        Fail "size mismatch for $($entry.filename): expected $($entry.size) bytes, got $size" `
            "re-run the build" $LockPath
    }
    Write-Host "[build] verified $StageAs ($([math]::Round($size / 1MB, 1)) MB, sha256 ok)"
}

function Copy-LocalBinary([string]$Path, [string]$StageAs) {
    if (-not (Test-Path $Path)) {
        Fail "the binary you asked to bundle does not exist: $Path" `
            "build it first, or drop the flag to use the published one" $Path
    }
    Copy-Item $Path (Join-Path $StageDir $StageAs) -Force
    $h = (Get-FileHash (Join-Path $StageDir $StageAs) -Algorithm SHA256).Hash.ToLower()
    $mb = [math]::Round((Get-Item $Path).Length / 1MB, 1)
    Write-Host "[build] staged LOCAL $StageAs ($mb MB, sha256 $h) -- UNVERIFIED (not in the published lock)"
}

# The Lemonade bootstrap MSI, pinned to the version this repo declares. Same
# source and the same truncation guard the Agent UI's CI step uses.
function Get-LemonadeMsi([string]$LemonadeVersion) {
    $dest = Join-Path $StageDir 'lemonade-server-minimal.msi'
    if ((Test-Path $dest) -and (Get-Item $dest).Length -gt 1MB) {
        Write-Host "[build] lemonade-server-minimal.msi already staged"
        return
    }
    $url = "https://github.com/lemonade-sdk/lemonade/releases/download/v$LemonadeVersion/lemonade-server-minimal.msi"
    Write-Host "[build] downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
    $size = (Get-Item $dest).Length
    # The -minimal MSI is a ~4-6MB bootstrap that fetches the runtime on first
    # run. Guard against an obviously-truncated download or a 404 HTML body
    # rather than pinning a size that moves between upstream releases.
    if ($size -lt 1MB) {
        Remove-Item $dest -Force
        Fail "the Lemonade MSI came back smaller than 1MB ($size bytes) -- truncated, or a 404 HTML body." `
            "check that v$LemonadeVersion exists upstream" $url
    }
    Write-Host "[build] lemonade-server-minimal.msi staged ($([math]::Round($size / 1MB, 1)) MB)"
}

function Get-LemonadeVersion {
    $versionPy = Join-Path $RepoRoot 'src\gaia\version.py'
    $m = Select-String -Path $versionPy -Pattern 'LEMONADE_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $m) {
        Fail "could not parse LEMONADE_VERSION from $versionPy" `
            "check that the constant is still declared there" $versionPy
    }
    $m.Matches[0].Groups[1].Value
}

# ---------------------------------------------------------------------------

$makensis = Find-MakeNSIS
Write-Host "[build] makensis: $makensis"

if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $StageDir | Out-Null

$lock = Get-Lock
if ($lock.schemaVersion -ne '3.0') {
    Fail "binaries.lock.json is schemaVersion '$($lock.schemaVersion)', this build understands '3.0'." `
        "update installer/tui/build.ps1 to the new schema before bundling anything" $LockPath
}
if (-not $Version) { $Version = $lock.agentVersion }
Write-Host "[build] bundle version: $Version"

if ($SidecarPath) { Copy-LocalBinary $SidecarPath 'gaia-agent.exe' }
else { Get-VerifiedBinary $lock.components.sidecar 'win32-x64' 'gaia-agent.exe' }

if ($TuiPath) { Copy-LocalBinary $TuiPath 'gaia-tui.exe' }
else { Get-VerifiedBinary $lock.components.tui 'win32-x64' 'gaia-tui.exe' }

$lemonadeVersion = Get-LemonadeVersion
Get-LemonadeMsi $lemonadeVersion

Copy-Item (Join-Path $PSScriptRoot 'nsis\pathmgr.ps1') $StageDir -Force
# The brand mark: MUI_ICON reads it from the stage at compile time, and it is
# also installed so the shortcuts can name an icon explicitly.
Copy-Item (Join-Path $PSScriptRoot 'nsis\gaia.ico') $StageDir -Force

$outDirFull = if ([System.IO.Path]::IsPathRooted($OutDir)) { $OutDir } else { Join-Path $RepoRoot $OutDir }
New-Item -ItemType Directory -Force -Path $outDirFull | Out-Null
$outFile = Join-Path $outDirFull "gaia-$Version-x64-setup.exe"

# NSIS requires a strict four-part numeric version for VIProductVersion; the
# bundle version is semver, so pad it rather than let makensis reject it.
$numeric = ($Version -split '[-+]')[0]
$parts = @($numeric -split '\.')
while ($parts.Count -lt 4) { $parts += '0' }
$version4 = ($parts[0..3] -join '.')

$args = @(
    "/DGAIA_VERSION=$Version",
    "/DGAIA_VERSION_4=$version4",
    "/DLEMONADE_VERSION=$lemonadeVersion",
    "/DSTAGE_DIR=$StageDir",
    "/DOUT_FILE=$outFile",
    "/DLICENSE_FILE=$(Join-Path $RepoRoot 'LICENSE.md')",
    (Join-Path $PSScriptRoot 'nsis\installer.nsi')
)
Write-Host "[build] makensis $($args -join ' ')"
& $makensis @args
if ($LASTEXITCODE -ne 0) {
    Fail "makensis failed (exit $LASTEXITCODE)." "read its output above" (Join-Path $PSScriptRoot 'nsis\installer.nsi')
}

$mb = [math]::Round((Get-Item $outFile).Length / 1MB, 1)
Write-Host ""
Write-Host "[build] installer: $outFile ($mb MB)"
Write-Host "[build] sha256:    $((Get-FileHash $outFile -Algorithm SHA256).Hash.ToLower())"
