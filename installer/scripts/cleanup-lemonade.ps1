# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

<#
.SYNOPSIS
    Free a Lemonade port and reap stray Lemonade/llama-server processes before a
    fresh server starts.

.DESCRIPTION
    Self-hosted (stx / strix-halo) runners reuse the same machine across jobs, so
    a crashed or leaked Lemonade Server from a prior job keeps listening on the
    port. The next job's ``lemonade-server serve`` / ``LemonadeServer.exe`` then
    dies with ``[Errno 10048] only one usage of each socket address`` (a bind
    collision on the port, IPv4 or IPv6), and any leaked ``llama-server`` backend
    child keeps a model resident so the next ``/load`` returns
    ``llama-server failed to start``.

    This script makes the start deterministic: it kills whatever owns the port
    (both IPv4 and IPv6 listeners), reaps orphaned Lemonade/llama-server
    processes by name, then blocks until the port is actually free so the caller
    can bind it. It is idempotent and safe to run when nothing is listening.

.PARAMETER Port
    TCP port the next Lemonade Server will bind (default: 13305).

.PARAMETER WaitSeconds
    Max seconds to wait for the port to become free after killing owners
    (default: 15).

.EXAMPLE
    .\installer\scripts\cleanup-lemonade.ps1 -Port 13305
#>

param(
    [Parameter(Mandatory = $false)]
    [int]$Port = 13305,

    [Parameter(Mandatory = $false)]
    [int]$WaitSeconds = 15
)

# Best-effort cleanup: never abort the caller because a process was already gone.
$ErrorActionPreference = "Continue"

Write-Host "=== Cleaning up stray Lemonade processes (port $Port) ==="

function Stop-PortOwners {
    param([int]$TargetPort)
    # -State Listen covers both the IPv4 (0.0.0.0/127.0.0.1) and IPv6 (::1)
    # listeners; the bind collision we hit was on ('::1', 13305).
    $conns = Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) { return $false }
    $killed = $false
    foreach ($processId in ($conns.OwningProcess | Select-Object -Unique)) {
        if ($processId -and $processId -ne 0) {
            Write-Host "[cleanup] Port $TargetPort held by PID $processId - killing"
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
            $killed = $true
        }
    }
    return $killed
}

# 1. Kill whatever currently owns the port.
[void](Stop-PortOwners -TargetPort $Port)

# 2. Reap Lemonade servers and llama-server backend children by name. These are
#    never a runner process, so name-based reaping only removes leaked GAIA
#    servers -- it catches orphans that already released the port but still hold
#    a model / VRAM and would fail the next /load.
$names = @("LemonadeServer", "lemonade-server", "llama-server")
foreach ($name in $names) {
    Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "[cleanup] Stopping stray $($_.ProcessName) (PID $($_.Id))"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}

# 3. Block until the port is actually free so the caller's bind succeeds.
$deadline = (Get-Date).AddSeconds($WaitSeconds)
while ((Get-Date) -lt $deadline) {
    # Re-kill any owner that reappears (a dying parent can respawn a child).
    if (-not (Stop-PortOwners -TargetPort $Port)) {
        Write-Host "[OK] Port $Port is free"
        return
    }
    Start-Sleep -Milliseconds 500
}

Write-Host "[WARN] Port $Port still shows a listener after ${WaitSeconds}s; continuing anyway"
