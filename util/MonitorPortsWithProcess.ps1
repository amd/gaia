# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Function to get netstat output for specified ports
function Get-NetstatForPorts {
    param (
        [int]$startPort,
        [int]$endPort,
        [int[]]$additionalPorts
    )
    
    $netstatOutput = netstat -ano | Select-String -Pattern "TCP"
    
    $filteredOutput = $netstatOutput | Where-Object {
        $line = $_ -split '\s+'
        $localAddress = $line[2]
        $port = [int]($localAddress -split ':')[-1]
        
        ($port -ge $startPort -and $port -le $endPort) -or ($additionalPorts -contains $port)
    }
    
    return $filteredOutput
}

# Function to get process information
function Get-ProcessInfo {
    param (
        [int]$processId
    )
    
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($process) {
        return "$($process.Name) (ID: $pid)"
    } else {
        return "Unknown (ID: $pid)"
    }
}

# Monitor specified port ranges
$startPort = 8000
$endPort = 9000
$additionalPorts = @(11434)

Write-Host "Monitoring ports $startPort to $endPort and $additionalPorts"
Write-Host "Press Ctrl+C to stop monitoring"

while ($true) {
    Clear-Host
    $result = Get-NetstatForPorts -startPort $startPort -endPort $endPort -additionalPorts $additionalPorts
    
    if ($result) {
        Write-Host "Active connections on specified ports:"
        $result | ForEach-Object {
            $line = $_ -split '\s+'
            $processId = $line[-1]
            $processInfo = Get-ProcessInfo -processId $processId  # Changed parameter name to match function
            Write-Host "$_ - Process: $processInfo"
        }
    } else {
        Write-Host "No active connections on specified ports."
    }
    
    Start-Sleep -Seconds 5
}