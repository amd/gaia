<#
Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT

.SYNOPSIS
Monitors and verifies the cleanup of GAIA processes and related resources.

.DESCRIPTION
This script performs the following tasks:
1. Identifies the main GAIA process and its child processes
2. Checks for ports 8000 and 8001 usage
3. Attempts to terminate the main GAIA process
4. Verifies cleanup by checking for:
   - Remaining child processes
   - Port usage after termination
   - Complete termination of main process

.NOTES
- Requires administrative privileges for some operations
- Targets ports 8000 and 8001 specifically
- Uses force termination of processes
#>

# 1. First, get the GAIA process and its children before termination
$gaiaProcess = Get-Process gaia -ErrorAction SilentlyContinue
if ($gaiaProcess) {
    Write-Host "Found GAIA process: PID $($gaiaProcess.Id)"
    
    # Get child processes before termination
    $childProcesses = Get-CimInstance Win32_Process | 
        Where-Object { $_.ParentProcessId -eq $gaiaProcess.Id }
    
    Write-Host "`nChild processes before termination:"
    $childProcesses | ForEach-Object {
        Write-Host "PID: $($_.ProcessId), Name: $($_.Name)"
    }

    # Check ports before termination
    Write-Host "`nPorts in use before termination:"
    Get-NetTCPConnection -LocalPort 8000,8001 -ErrorAction SilentlyContinue | 
        ForEach-Object {
            $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
            Write-Host "Port $($_.LocalPort) - PID: $($_.OwningProcess), Process: $($proc.ProcessName)"
        }

    # 2. Stop only the main GAIA process and observe what happens
    Write-Host "`nStopping main GAIA process..."
    Stop-Process -Id $gaiaProcess.Id -Force

    # 3. Wait a moment and check what remains
    Start-Sleep -Seconds 2
    Write-Host "`nChecking for remaining processes..."

    # Check if main process is still running
    $gaiaStillRunning = Get-Process -Id $gaiaProcess.Id -ErrorAction SilentlyContinue
    if ($gaiaStillRunning) {
        Write-Host "WARNING: Main GAIA process is still running!"
    }

    # Check if any child processes are still running
    $remainingChildren = Get-CimInstance Win32_Process | 
        Where-Object { $_.ParentProcessId -eq $gaiaProcess.Id }
    
    if ($remainingChildren) {
        Write-Host "`nRemaining child processes:"
        $remainingChildren | ForEach-Object {
            Write-Host "PID: $($_.ProcessId), Name: $($_.Name)"
        }
    }

    # Check remaining ports
    Write-Host "`nPorts still in use after termination:"
    Get-NetTCPConnection -LocalPort 8000,8001 -ErrorAction SilentlyContinue | 
        ForEach-Object {
            $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
            Write-Host "Port $($_.LocalPort) - PID: $($_.OwningProcess), Process: $($proc.ProcessName)"
        }
} else {
    Write-Host "No GAIA process found running"
}
