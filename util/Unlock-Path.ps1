<#
.SYNOPSIS
    Finds and optionally kills processes that have locked a file or folder.

.DESCRIPTION
    Uses the Windows Restart Manager API to identify which processes are
    holding handles to a specified file or folder. For folders, recursively
    scans all files inside to find locks. Prompts for confirmation before
    terminating any processes.

.PARAMETER Path
    The path to the file or folder to check for locks.

.EXAMPLE
    .\Unlock-Path.ps1 -Path "C:\locked\folder"

.EXAMPLE
    .\Unlock-Path.ps1 "C:\myfile.txt"
#>

param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Path
)

# Add the Restart Manager API type definitions
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Diagnostics;
using System.Collections.Generic;

public class RestartManager
{
    [StructLayout(LayoutKind.Sequential)]
    public struct RM_UNIQUE_PROCESS
    {
        public int dwProcessId;
        public System.Runtime.InteropServices.ComTypes.FILETIME ProcessStartTime;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct RM_PROCESS_INFO
    {
        public RM_UNIQUE_PROCESS Process;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)]
        public string strAppName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)]
        public string strServiceShortName;
        public int ApplicationType;
        public int AppStatus;
        public int TSSessionId;
        [MarshalAs(UnmanagedType.Bool)]
        public bool bRestartable;
    }

    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    public static extern int RmStartSession(out uint pSessionHandle, int dwSessionFlags, string strSessionKey);

    [DllImport("rstrtmgr.dll")]
    public static extern int RmEndSession(uint pSessionHandle);

    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    public static extern int RmRegisterResources(uint pSessionHandle, uint nFiles, string[] rgsFilenames, uint nApplications, RM_UNIQUE_PROCESS[] rgApplications, uint nServices, string[] rgsServiceNames);

    [DllImport("rstrtmgr.dll")]
    public static extern int RmGetList(uint dwSessionHandle, out uint pnProcInfoNeeded, ref uint pnProcInfo, [In, Out] RM_PROCESS_INFO[] rgAffectedApps, ref uint lpdwRebootReasons);

    public const int ERROR_MORE_DATA = 234;
}
"@

function Get-LockingProcessesForFiles {
    param([string[]]$FilePaths)

    $results = @()

    if ($FilePaths.Count -eq 0) {
        return $results
    }

    $sessionHandle = [uint32]0
    $sessionKey = [Guid]::NewGuid().ToString()

    # Start a Restart Manager session
    $result = [RestartManager]::RmStartSession([ref]$sessionHandle, 0, $sessionKey)

    if ($result -ne 0) {
        Write-Warning "Failed to start Restart Manager session. Error code: $result"
        return $results
    }

    try {
        # Register all resources at once (more efficient)
        $result = [RestartManager]::RmRegisterResources($sessionHandle, [uint32]$FilePaths.Count, $FilePaths, 0, $null, 0, $null)

        if ($result -ne 0) {
            Write-Warning "Failed to register resources. Error code: $result"
            return $results
        }

        # Get the list of processes
        $procInfoNeeded = [uint32]0
        $procInfo = [uint32]0
        $rebootReasons = [uint32]0

        # First call to get the number of processes
        $result = [RestartManager]::RmGetList($sessionHandle, [ref]$procInfoNeeded, [ref]$procInfo, $null, [ref]$rebootReasons)

        if ($result -eq [RestartManager]::ERROR_MORE_DATA -or $procInfoNeeded -gt 0) {
            # Allocate array and get the process info
            $processInfoArray = New-Object RestartManager+RM_PROCESS_INFO[] $procInfoNeeded
            $procInfo = $procInfoNeeded

            $result = [RestartManager]::RmGetList($sessionHandle, [ref]$procInfoNeeded, [ref]$procInfo, $processInfoArray, [ref]$rebootReasons)

            if ($result -eq 0) {
                foreach ($pi in $processInfoArray) {
                    if ($pi.Process.dwProcessId -gt 0) {
                        try {
                            $proc = Get-Process -Id $pi.Process.dwProcessId -ErrorAction SilentlyContinue
                            $procPath = ""
                            if ($proc) {
                                try {
                                    $procPath = $proc.Path
                                } catch {
                                    $procPath = "(access denied)"
                                }
                            }

                            $results += [PSCustomObject]@{
                                ProcessId   = $pi.Process.dwProcessId
                                ProcessName = $pi.strAppName
                                ExePath     = $procPath
                            }
                        } catch {
                            $results += [PSCustomObject]@{
                                ProcessId   = $pi.Process.dwProcessId
                                ProcessName = $pi.strAppName
                                ExePath     = "(unable to retrieve)"
                            }
                        }
                    }
                }
            }
        }
    }
    finally {
        # End the session
        [void][RestartManager]::RmEndSession($sessionHandle)
    }

    return $results
}

# Main script execution
$ErrorActionPreference = "Stop"

# Resolve the path to absolute
try {
    $resolvedPath = Resolve-Path -Path $Path -ErrorAction Stop
    $fullPath = $resolvedPath.Path
} catch {
    Write-Host "`nError: Path not found: $Path" -ForegroundColor Red
    exit 1
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host " File/Folder Lock Finder" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "`nChecking: $fullPath" -ForegroundColor Yellow

$filesToCheck = @()

# Check if it's a directory
if (Test-Path -Path $fullPath -PathType Container) {
    Write-Host "Scanning folder for files..." -ForegroundColor Gray

    # Get all files recursively
    $allFiles = Get-ChildItem -Path $fullPath -Recurse -File -ErrorAction SilentlyContinue
    $fileCount = $allFiles.Count

    Write-Host "Found $fileCount files to check.`n" -ForegroundColor Gray

    if ($fileCount -eq 0) {
        Write-Host "No files found in this folder." -ForegroundColor Yellow
        exit 0
    }

    $filesToCheck = $allFiles.FullName

    # Process in batches of 100 files for efficiency
    $batchSize = 100
    $allLockingProcesses = @()
    $processedFiles = 0

    for ($i = 0; $i -lt $filesToCheck.Count; $i += $batchSize) {
        $batch = $filesToCheck[$i..[Math]::Min($i + $batchSize - 1, $filesToCheck.Count - 1)]
        $processedFiles += $batch.Count

        # Show progress
        $percent = [math]::Round(($processedFiles / $filesToCheck.Count) * 100)
        Write-Host "`rScanning for locks: $percent% ($processedFiles / $fileCount files)    " -NoNewline -ForegroundColor Gray

        $lockingProcs = Get-LockingProcessesForFiles -FilePaths $batch
        if ($lockingProcs.Count -gt 0) {
            $allLockingProcesses += $lockingProcs
        }
    }

    Write-Host "`r                                                                    " -NoNewline
    Write-Host "`r" -NoNewline

    # Deduplicate by ProcessId
    $lockingProcesses = $allLockingProcesses | Sort-Object -Property ProcessId -Unique

} else {
    # Single file
    Write-Host ""
    $filesToCheck = @($fullPath)
    $lockingProcesses = Get-LockingProcessesForFiles -FilePaths $filesToCheck
}

if ($lockingProcesses.Count -eq 0) {
    Write-Host "No processes are currently locking this path." -ForegroundColor Green
    Write-Host "`nNote: The lock might be held by:" -ForegroundColor Yellow
    Write-Host "  - A system process (antivirus, indexing service)" -ForegroundColor Gray
    Write-Host "  - A process with an open working directory" -ForegroundColor Gray
    Write-Host "  - Windows itself (for certain system files)" -ForegroundColor Gray
    Write-Host "`nTry closing any programs that might be using this folder," -ForegroundColor Yellow
    Write-Host "or use Process Explorer from Sysinternals for deeper analysis." -ForegroundColor Yellow
    exit 0
}

Write-Host "Found $($lockingProcesses.Count) process(es) locking files in this path:`n" -ForegroundColor Yellow

# Display in a table
$lockingProcesses | Format-Table -Property @(
    @{Label="PID"; Expression={$_.ProcessId}; Width=8},
    @{Label="Process Name"; Expression={$_.ProcessName}; Width=25},
    @{Label="Executable Path"; Expression={$_.ExePath}}
) -Wrap

# Prompt for each process
foreach ($proc in $lockingProcesses) {
    Write-Host "`n----------------------------------------" -ForegroundColor DarkGray
    Write-Host "Process: $($proc.ProcessName) (PID: $($proc.ProcessId))" -ForegroundColor White

    $response = Read-Host "Kill this process? (Y/n)"

    if ($response -eq '' -or $response -eq 'Y' -or $response -eq 'y') {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
            Write-Host "Process $($proc.ProcessId) terminated successfully." -ForegroundColor Green
        } catch {
            Write-Host "Failed to kill process: $_" -ForegroundColor Red
            Write-Host "Try running this script as Administrator." -ForegroundColor Yellow
        }
    } else {
        Write-Host "Skipped." -ForegroundColor Gray
    }
}

Write-Host "`nDone.`n" -ForegroundColor Cyan
