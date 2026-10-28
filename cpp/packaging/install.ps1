# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Install gaia-native, the GAIA flagship agent as one static binary, on Windows.
#
#   irm https://raw.githubusercontent.com/amd/gaia/main/cpp/packaging/install.ps1 | iex
#
# $env:GAIA_NATIVE_VERSION = "0.1.0"   install that version instead of the newest
# $env:GAIA_NATIVE_DIR = "C:\tools"     install there instead of %LOCALAPPDATA%\gaia-native

$ErrorActionPreference = 'Stop'
$repo = 'amd/gaia'
$dir = if ($env:GAIA_NATIVE_DIR) { $env:GAIA_NATIVE_DIR } else { Join-Path $env:LOCALAPPDATA 'gaia-native' }

if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
    throw 'gaia-native install: only Windows x64 is built yet.'
}
$asset = 'gaia-native-windows-x64.exe'

if ($env:GAIA_NATIVE_VERSION) {
    $tag = "gaia-native-v$($env:GAIA_NATIVE_VERSION)"
} else {
    # The repo's "latest" release is GAIA itself, so find the newest gaia-native tag,
    # paging because GAIA cuts many releases of its own.
    $tag = $null
    for ($page = 1; -not $tag -and $page -le 10; $page++) {
        $releases = @(Invoke-RestMethod "https://api.github.com/repos/$repo/releases?per_page=100&page=$page")
        if ($releases.Count -eq 0) { break }
        $tag = ($releases | Where-Object { $_.tag_name -like 'gaia-native-v*' } | Select-Object -First 1).tag_name
    }
    if (-not $tag) {
        throw "gaia-native install: no gaia-native release found at github.com/$repo/releases; set GAIA_NATIVE_VERSION to pick one."
    }
}
$base = "https://github.com/$repo/releases/download/$tag"

$tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Write-Host "Downloading $asset ($tag)..."
    Invoke-WebRequest "$base/$asset" -OutFile (Join-Path $tmp $asset) -UseBasicParsing
    Invoke-WebRequest "$base/SHA256SUMS" -OutFile (Join-Path $tmp 'SHA256SUMS') -UseBasicParsing

    $line = Get-Content (Join-Path $tmp 'SHA256SUMS') | Where-Object { $_ -match " $([regex]::Escape($asset))$" }
    if (-not $line) { throw "gaia-native install: SHA256SUMS has no entry for $asset." }
    $expected = ($line -split ' ')[0].ToLower()
    $actual = (Get-FileHash (Join-Path $tmp $asset) -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) {
        throw "gaia-native install: checksum mismatch for $asset (expected $expected, got $actual); not installing."
    }

    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $target = Join-Path $dir 'gaia-native.exe'
    Move-Item -Force (Join-Path $tmp $asset) $target
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

Write-Host "Installed $target ($(& $target --version))."
# Edit the raw registry value so %VAR% entries in the user PATH stay unexpanded.
$envKey = Get-Item 'HKCU:\Environment'
$userPath = [string]$envKey.GetValue('Path', '', 'DoNotExpandEnvironmentNames')
$entries = @($userPath -split ';' | Where-Object { $_ })
if (-not ($entries | Where-Object { $_.TrimEnd('\') -ieq $dir.TrimEnd('\') })) {
    $kind = if ($userPath) { $envKey.GetValueKind('Path') } else { 'ExpandString' }
    Set-ItemProperty -Path 'HKCU:\Environment' -Name 'Path' -Value (($entries + $dir) -join ';') -Type $kind
    # Setting and clearing a variable broadcasts the change so new terminals see it.
    [Environment]::SetEnvironmentVariable('GAIA_NATIVE_PATH_REFRESH', '1', 'User')
    [Environment]::SetEnvironmentVariable('GAIA_NATIVE_PATH_REFRESH', $null, 'User')
    $env:Path = "$env:Path;$dir"
    Write-Host "Added $dir to your user PATH; open a new terminal to run 'gaia-native'."
}
Write-Host 'It talks to a local Lemonade Server (https://lemonade-server.ai), or to any OpenAI-compatible'
Write-Host 'server via LEMONADE_BASE_URL and LEMONADE_API_KEY. Try: gaia-native "what''s in this folder?"'
