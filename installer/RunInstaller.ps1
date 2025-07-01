param(
    [string]$INSTALL_PATH = "$env:LOCALAPPDATA"
)

# Create a log file path
$installPath = Join-Path $INSTALL_PATH "GAIA"
$logFile = Join-Path $installPath "gaia_install.log"

# Start the installer process with error redirection
$process = Start-Process -FilePath ".\gaia-windows-setup.exe" -ArgumentList "/S /D=$installPath" -NoNewWindow -PassThru -RedirectStandardError "installer_error.log"
$processId = $process.Id
Write-Host "Installer process started with PID: $processId"
Write-Host "Installation path: $installPath"

# Wait for 30 seconds before checking for log file
Write-Host "Waiting for installer to create log file..."
Start-Sleep -Seconds 30
$startTime = Get-Date
$timeout = New-TimeSpan -Minutes 30  # 30 minute timeout

while ($true) {
    # Check if process is still running
    $runningProcess = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if (-not $runningProcess) {
        Write-Host "Process has exited"
        # Check for error log
        if (Test-Path "installer_error.log") {
            Write-Host "Error log contents:"
            Get-Content "installer_error.log"
        }
        break
    }

    if (Test-Path $logFile) {
        Write-Host "Log file found at: $logFile"
        # Create a job to monitor the log file
        $logJob = Start-Job -ScriptBlock {
            param($logFile)
            Get-Content $logFile -Wait | ForEach-Object {
                Write-Output $_
            }
        } -ArgumentList $logFile

        while ($true) {
            # Check if process is still running
            $runningProcess = Get-Process -Id $processId -ErrorAction SilentlyContinue
            if (-not $runningProcess) {
                Write-Host "Process has exited"
                Stop-Job -Job $logJob
                Remove-Job -Job $logJob
                break
            }

            # Display any new log content
            $logOutput = Receive-Job -Job $logJob
            if ($logOutput) {
                $logOutput | ForEach-Object {
                    Write-Host $_
                }
            }

            # Check if we've exceeded timeout
            if ((Get-Date) - $startTime -gt $timeout) {
                Write-Host "ERROR: Installation timed out after 30 minutes"
                Stop-Process -Id $processId -Force
                Stop-Job -Job $logJob
                Remove-Job -Job $logJob
                exit 1
            }

            Start-Sleep -Seconds 1
        }
    } else {
        Write-Host "Waiting for log file to be created..."
        Start-Sleep -Seconds 10
    }

    # Check if we've exceeded timeout
    if ((Get-Date) - $startTime -gt $timeout) {
        Write-Host "ERROR: Installation timed out after 30 minutes"
        Stop-Process -Id $processId -Force
        exit 1
    }
    Start-Sleep -Seconds 1
}

# Get final exit code
$exitCode = $process.ExitCode
Write-Host "Installer exited with code: $exitCode"

# Show final log contents and check for success
if (Test-Path $logFile) {
    $logContent = Get-Content $logFile -Raw
    # This message is created in install.bat
    if ($logContent -match "GAIA package installation successful") {
        Write-Host "Installation completed successfully!"
        exit 0
    } else {
        Write-Host "ERROR: Installation did not complete successfully"
        Write-Host "Log contents:"
        Write-Host $logContent
        exit 1
    }
} else {
    Write-Host "ERROR: No log file found at: $logFile"
    if (Test-Path "installer_error.log") {
        Write-Host "Error log contents:"
        Get-Content "installer_error.log"
    }
    exit 1
}
