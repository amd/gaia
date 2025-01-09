# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

param(
    [Parameter()]
    [ValidateSet("Hybrid", "NPU")]
    [string]$Mode = "Hybrid"  # Default to Hybrid mode if not specified
)

# Store the original directory
$originalLocation = Get-Location

# Set variables based on mode
$envVarName = if ($Mode -eq "Hybrid") { "AMD_OGA_HYBRID" } else { "AMD_OGA" }
$zipFileName = if ($Mode -eq "Hybrid") { "oga-hybrid.zip" } else { "oga-npu.zip" }
$artifactsPath = if ($Mode -eq "Hybrid") {
    "hybrid-llm-artifacts_1.3.0\hybrid-llm-artifacts\onnxruntime_genai\wheel"
} else {
    "amd_oga\wheels"
}

# Set the base path and environment variable path
$basePath = if (Test-Path "Env:$envVarName") {
    (Get-Item -Path "Env:$envVarName" | Select-Object -ExpandProperty Value)
} else {
    Join-Path $env:USERPROFILE ".amd_oga"
}

$envVarPath = if (Test-Path "Env:$envVarName") {
    (Get-Item -Path "Env:$envVarName" | Select-Object -ExpandProperty Value)
} else {
    Join-Path $basePath $(if ($Mode -eq "Hybrid") { "hybrid-llm-artifacts_1.3.0" } else { "amd_oga" })
}

# Display environment variable and path information
Write-Host "Setting $envVarName environment variable..." -ForegroundColor Cyan
Write-Host "Base Path: $basePath" -ForegroundColor Cyan
Write-Host "Environment Variable Path: $envVarPath" -ForegroundColor Cyan


# Create/Update the environment variable for both System and Process level
[System.Environment]::SetEnvironmentVariable($envVarName, $envVarPath, [System.EnvironmentVariableTarget]::User)
[System.Environment]::SetEnvironmentVariable($envVarName, $envVarPath, [System.EnvironmentVariableTarget]::Process)

# Verify the environment variable was set correctly
$envValue = [System.Environment]::GetEnvironmentVariable($envVarName, [System.EnvironmentVariableTarget]::User)
Write-Host "$envVarName environment variable set to: $envValue" -ForegroundColor Cyan

# Create directory if it doesn't exist
if (-not (Test-Path $basePath)) {
    New-Item -ItemType Directory -Force -Path $basePath | Out-Null
    Write-Host "Created directory: $basePath" -ForegroundColor Yellow
}

# Check if OGA_TOKEN is set
if (-not $env:OGA_TOKEN) {
    Write-Host "Error: OGA_TOKEN environment variable is not set" -ForegroundColor Red
    Write-Host "Please set the OGA_TOKEN environment variable before running this script" -ForegroundColor Yellow
    exit 1
}

# Download and extract artifacts if they don't exist
$ogaZipPath = Join-Path $basePath $zipFileName
$wheelPath = Join-Path $basePath $artifactsPath

if (Test-Path $ogaZipPath) {
    Write-Host "Found existing ${Mode} artifacts at ${ogaZipPath}, skipping download..." -ForegroundColor Yellow
} else {
    Write-Host "Downloading ${Mode} artifacts to ${ogaZipPath}..." -ForegroundColor Yellow

    # Download artifacts using the download_lfs_file.py script
    $artifactZip = if ($Mode -eq "Hybrid") {
        "ryzen_ai_13_ga/hybrid-llm-artifacts_1.3.0.zip"
    } else {
        "ryzen_ai_13_ga/npu-llm-artifacts_1.3.0.zip"
    }

    python ./installer/download_lfs_file.py `
        $artifactZip `
        $basePath `
        $ogaZipPath `
        $env:OGA_TOKEN

    # Verify download was successful
    if (-not (Test-Path $ogaZipPath)) {
        Write-Host "Error: Failed to download artifacts to $ogaZipPath" -ForegroundColor Red
        exit 1
    }
}

# Extract the archive
Write-Host "Extracting artifacts..." -ForegroundColor Yellow
try {
    Expand-Archive -Path $ogaZipPath -DestinationPath $basePath -Force
} catch {
    Write-Host "Error: Failed to extract artifacts: $_" -ForegroundColor Red
    exit 1
}

# Verify wheel directory exists
if (-not (Test-Path $wheelPath)) {
    Write-Host "Error: Wheel directory not found at: $wheelPath" -ForegroundColor Red
    Write-Host "Contents of ${basePath}:" -ForegroundColor Yellow
    Get-ChildItem -Path $basePath -Recurse | Format-Table -Property FullName
    exit 1
}

# Change directory to the wheel folder
Set-Location -Path $wheelPath

# Install the required wheel files using the install script
Write-Host "Installing wheel files..." -ForegroundColor Yellow
python $originalLocation/installer/install_oga.py `
    --folder_path "$basePath" `
    --zip_file_name "$zipFileName" `
    --wheels_path "$artifactsPath" `
    --keep_zip

# Return to the original directory
Set-Location -Path $originalLocation

Write-Host "Dependencies installed successfully!" -ForegroundColor Green
