# Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Store the original directory
$originalLocation = Get-Location

# Set the base path for AMD_OGA_HYBRID
# Use environment variable if provided, otherwise use default path
$hybridPath = if ($env:AMD_OGA_HYBRID) {
    $env:AMD_OGA_HYBRID
} else {
    $env:USERPROFILE
}

# Create/Update the AMD_OGA_HYBRID user environment variable
[System.Environment]::SetEnvironmentVariable('AMD_OGA_HYBRID', $hybridPath, [System.EnvironmentVariableTarget]::User)

# Verify the environment variable was set correctly
$envValue = [System.Environment]::GetEnvironmentVariable('AMD_OGA_HYBRID', [System.EnvironmentVariableTarget]::User)
Write-Host "AMD_OGA_HYBRID environment variable set to: $envValue" -ForegroundColor Cyan

# Create directory if it doesn't exist
if (-not (Test-Path $hybridPath)) {
    New-Item -ItemType Directory -Force -Path $hybridPath | Out-Null
    Write-Host "Created directory: $hybridPath" -ForegroundColor Yellow
}

# Download and extract artifacts if they don't exist
$ogaZipPath = Join-Path $hybridPath "oga-hybrid.zip"
$wheelPath = Join-Path $hybridPath "hybrid-llm-artifacts_1.3.0\hybrid-llm-artifacts\onnxruntime_genai\wheel"
if (-not (Test-Path $wheelPath)) {
    Write-Host "Downloading hybrid-llm-artifacts from ${wheelPath} to ${ogaZipPath}..." -ForegroundColor Yellow

    # Download artifacts using the download_lfs_file.py script
    python ./installer/download_lfs_file.py `
        "ryzen_ai_13_ga/hybrid-llm-artifacts_1.3.0.zip" `
        $hybridPath `
        $ogaZipPath `
        $env:OGA_TOKEN `
        "https://api.github.com/repos/aigdat/ryzenai-sw-ea/contents/"

    # Verify download was successful
    if (-not (Test-Path $ogaZipPath)) {
        Write-Host "Error: Failed to download artifacts to $ogaZipPath" -ForegroundColor Red
        exit 1
    }

    # Extract the archive
    Write-Host "Extracting artifacts..." -ForegroundColor Yellow
    try {
        Expand-Archive -Path $ogaZipPath -DestinationPath $hybridPath -Force
    } catch {
        Write-Host "Error: Failed to extract artifacts: $_" -ForegroundColor Red
        exit 1
    }
}

# Verify wheel directory exists
if (-not (Test-Path $wheelPath)) {
    Write-Host "Error: Wheel directory not found at: $wheelPath" -ForegroundColor Red
    Write-Host "Contents of ${hybridPath}:" -ForegroundColor Yellow
    Get-ChildItem -Path $hybridPath -Recurse | Format-Table -Property FullName
    exit 1
}

# Change directory to the wheel folder
Set-Location -Path $wheelPath

# Install the required wheel files
Write-Host "Installing wheel files..." -ForegroundColor Yellow
pip install "onnxruntime_genai_directml-0.4.0.dev0-cp310-cp310-win_amd64.whl"
pip install "onnxruntime_vitisai-1.19.0.dev20241217-cp310-cp310-win_amd64.whl"

# Return to the original directory
Set-Location -Path $originalLocation

Write-Host "Dependencies installed successfully!" -ForegroundColor Green
