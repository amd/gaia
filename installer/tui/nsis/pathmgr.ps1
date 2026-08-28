# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Add or remove one directory from the per-user PATH.
#
# Not done in NSIS directly: a stock makensis build caps strings at
# NSIS_MAX_STRLEN (1024), so reading a longer PATH into a register and writing
# it back silently TRUNCATES the user's PATH. .NET reads and writes the whole
# value, and SetEnvironmentVariable broadcasts WM_SETTINGCHANGE itself so new
# shells pick the change up without a logoff.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('Add', 'Remove')][string]$Action,
    [Parameter(Mandatory = $true)][string]$Directory
)

$ErrorActionPreference = 'Stop'

$target = [EnvironmentVariableTarget]::User
$current = [Environment]::GetEnvironmentVariable('Path', $target)
if ($null -eq $current) { $current = '' }

# Split on ';' and drop empties so a trailing separator does not become a ""
# entry, which Windows resolves as the current directory.
$entries = @($current -split ';' | Where-Object { $_ -ne '' })
$normalized = $Directory.TrimEnd('\')
$kept = @($entries | Where-Object { $_.TrimEnd('\') -ne $normalized })

if ($Action -eq 'Add') {
    if ($kept.Count -eq $entries.Count) {
        # PREPENDED, not appended. Windows searches the user's existing entries
        # before an appended one, so a leftover gaia-agent from an earlier pip
        # install shadowed the frozen binary this installer just placed -- the
        # agent that ran was a different build than the one that shipped.
        $kept = @($normalized) + $kept
    } else {
        # Already present: leave the PATH byte-for-byte alone rather than
        # rewriting it to move our entry around.
        Write-Output "PATH already contains $normalized"
        exit 0
    }
} elseif ($kept.Count -eq $entries.Count) {
    Write-Output "PATH did not contain $normalized"
    exit 0
}

[Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), $target)
Write-Output "$Action $normalized : PATH now has $($kept.Count) entries"
