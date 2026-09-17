# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

<#
.SYNOPSIS
    Add or remove a directory on the current user's PATH.

.DESCRIPTION
    Called by installer/nsis/gaia.nsi at install and uninstall time. It is a
    separate script, and not NSIS registry calls, for two reasons:

    * NSIS truncates strings at NSIS_MAX_STRLEN (1024 in the stock build) with
      no error, so reading a long PATH and writing it back silently drops
      whatever did not fit.
    * .NET's [Environment]::SetEnvironmentVariable writes REG_SZ, which turns
      a PATH containing %USERPROFILE% into a literal string Windows never
      expands. This edits the registry value directly and keeps its kind.

    Both actions are idempotent: adding a directory that is already there, or
    removing one that is not, succeeds and changes nothing.

    Only HKCU\Environment is touched — never the machine PATH — so no
    elevation is needed and nothing another user depends on can be broken.

    The caller broadcasts WM_SETTINGCHANGE; this script does not.

.PARAMETER Action
    Add or Remove.

.PARAMETER Directory
    The absolute directory to add to or remove from the user PATH.

.EXAMPLE
    .\gaia-path.ps1 -Action Add -Directory "C:\Users\me\AppData\Local\Programs\GAIA"
    .\gaia-path.ps1 -Action Remove -Directory "C:\Users\me\AppData\Local\Programs\GAIA"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Add', 'Remove')]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$Directory
)

$ErrorActionPreference = 'Stop'

function Get-NormalizedPathEntry {
    param([string]$Entry)
    # Trailing separators and surrounding quotes are cosmetic in a PATH entry;
    # comparing without them stops a second install appending a duplicate.
    return $Entry.Trim().Trim('"').TrimEnd('\', '/')
}

function Get-RawUserPath {
    # DoNotExpandEnvironmentNames keeps %USERPROFILE% as written. Expanding it
    # here and writing the result back would bake this machine's paths into
    # entries the user deliberately left portable.
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $false)
    if ($null -eq $key) {
        return [pscustomobject]@{
            Value = ''
            Kind  = [Microsoft.Win32.RegistryValueKind]::ExpandString
        }
    }
    try {
        $hasPath = @($key.GetValueNames()) -contains 'Path'
        if (-not $hasPath) {
            return [pscustomobject]@{
                Value = ''
                Kind  = [Microsoft.Win32.RegistryValueKind]::ExpandString
            }
        }
        $value = $key.GetValue(
            'Path', '',
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
        return [pscustomobject]@{
            Value = [string]$value
            Kind  = $key.GetValueKind('Path')
        }
    } finally {
        $key.Close()
    }
}

function Set-RawUserPath {
    param(
        [string]$Value,
        [Microsoft.Win32.RegistryValueKind]$Kind
    )
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
    if ($null -eq $key) {
        $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
    }
    try {
        $key.SetValue('Path', $Value, $Kind)
    } finally {
        $key.Close()
    }
}

try {
    $target = Get-NormalizedPathEntry $Directory
    if ([string]::IsNullOrWhiteSpace($target)) {
        throw "-Directory is empty. Pass the absolute path of the folder to add to or remove from the user PATH."
    }

    $current = Get-RawUserPath
    $entries = @($current.Value -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $matched = @($entries | Where-Object { (Get-NormalizedPathEntry $_) -ieq $target })

    switch ($Action) {
        'Add' {
            if ($matched.Count -gt 0) {
                Write-Host "PATH already contains $target - nothing to do."
                exit 0
            }
            if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
                throw "$Directory does not exist, so it will not be added to the PATH. Install GAIA before running this script."
            }
            $updated = @($entries + $target) -join ';'
            Set-RawUserPath -Value $updated -Kind $current.Kind
            Write-Host "Added $target to the user PATH."
        }
        'Remove' {
            if ($matched.Count -eq 0) {
                Write-Host "PATH does not contain $target - nothing to do."
                exit 0
            }
            $kept = @($entries | Where-Object { (Get-NormalizedPathEntry $_) -ine $target })
            Set-RawUserPath -Value ($kept -join ';') -Kind $current.Kind
            Write-Host "Removed $target from the user PATH."
        }
    }
    exit 0
} catch {
    Write-Host "PATH update failed ($Action $Directory): $($_.Exception.Message)"
    exit 1
}
