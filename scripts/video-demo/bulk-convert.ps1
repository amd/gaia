# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Bulk convert video recordings: compress to WebM (optionally crop edges)
# Usage: .\bulk-convert.ps1 -InputFolder recordings -OutputFolder output

param(
    [Parameter(Mandatory=$true)]
    [string]$InputFolder,

    [string]$OutputFolder = "output",

    [switch]$Crop,

    [int]$CropPixels = 5,

    [ValidateRange(18, 35)]
    [int]$Quality = 20,

    [switch]$KeepIntermediate,

    [switch]$Help
)

# Helper functions
function Write-Step { param($msg) Write-Host "  -> $msg" -ForegroundColor Cyan }
function Write-Success { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Err { param($msg) Write-Host "  [X] $msg" -ForegroundColor Red }

if ($Help) {
    Write-Host ''
    Write-Host '  Bulk Video Conversion Script' -ForegroundColor Cyan
    Write-Host '  =============================' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  Converts all MP4 files in a folder to compressed WebM'
    Write-Host ''
    Write-Host '  Usage:' -ForegroundColor White
    Write-Host '    .\bulk-convert.ps1 -InputFolder <folder> [options]'
    Write-Host ''
    Write-Host '  Options:' -ForegroundColor White
    Write-Host '    -InputFolder <folder>   Input folder with MP4 files (required)'
    Write-Host '    -OutputFolder <folder>  Output folder for WebM files (default: output)'
    Write-Host '    -Crop                   Enable cropping of edges'
    Write-Host '    -CropPixels <int>       Pixels to crop from each edge (default: 5)'
    Write-Host '    -Quality <18-35>        CRF value, lower=better (default: 20)'
    Write-Host '    -KeepIntermediate       Keep intermediate files (cropped)'
    Write-Host '    -Help                   Show this help'
    Write-Host ''
    Write-Host '  Examples:' -ForegroundColor White
    Write-Host '    .\bulk-convert.ps1 -InputFolder ./recordings'
    Write-Host '    .\bulk-convert.ps1 -InputFolder ./recordings -OutputFolder ./webm'
    Write-Host '    .\bulk-convert.ps1 -InputFolder ./recordings -Crop'
    Write-Host '    .\bulk-convert.ps1 -InputFolder ./recordings -Crop -CropPixels 10'
    Write-Host ''
    exit 0
}

# Check ffmpeg
Write-Host ''
Write-Step 'Checking ffmpeg...'
$ffmpegCmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpegCmd) {
    Write-Err 'ffmpeg not found'
    Write-Host '  Install with: winget install ffmpeg' -ForegroundColor Gray
    exit 1
}
Write-Success 'ffmpeg found'

# Check input folder
if (-not (Test-Path -LiteralPath $InputFolder)) {
    Write-Err "Input folder not found: $InputFolder"
    exit 1
}

# Get MP4 files
$mp4Files = Get-ChildItem -LiteralPath $InputFolder -Filter "*.mp4"
if ($mp4Files.Count -eq 0) {
    Write-Err "No MP4 files found in: $InputFolder"
    exit 1
}

Write-Step "Found $($mp4Files.Count) MP4 file(s)"
if ($Crop) {
    Write-Step "Cropping enabled: $CropPixels px from each edge"
}

# Create output folder
if (-not (Test-Path -LiteralPath $OutputFolder)) {
    New-Item -ItemType Directory -Path $OutputFolder -Force | Out-Null
    Write-Step "Created output folder: $OutputFolder"
}

# Process each file
$successCount = 0
$failCount = 0

foreach ($file in $mp4Files) {
    Write-Host ''
    Write-Host "Processing: $($file.Name)" -ForegroundColor White

    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($file.Name)
    $inputPath = $file.FullName
    $croppedPath = Join-Path $InputFolder "${baseName}-cropped.mp4"
    $outputPath = Join-Path $OutputFolder "${baseName}.webm"

    # Determine source for compression
    $compressSource = $inputPath

    # Step 1: Crop edges (if enabled)
    if ($Crop) {
        Write-Step "Cropping ($CropPixels px from each edge)..."
        $cropFilter = "crop=iw-$($CropPixels*2):ih-$($CropPixels*2):${CropPixels}:${CropPixels}"
        & ffmpeg -i $inputPath -vf $cropFilter -c:a copy -y $croppedPath 2>$null

        if ($LASTEXITCODE -ne 0) {
            Write-Err "Failed to crop: $($file.Name)"
            $failCount++
            continue
        }
        $compressSource = $croppedPath
    }

    # Step 2: Compress to WebM (VP9)
    Write-Step "Compressing to WebM (CRF: $Quality)..."
    & ffmpeg -i $compressSource `
        -c:v libvpx-vp9 `
        -crf $Quality `
        -b:v 0 `
        -quality good `
        -speed 2 `
        -row-mt 1 `
        -c:a libopus `
        -b:a 128k `
        -y $outputPath 2>$null

    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to compress: $($file.Name)"
        $failCount++
        # Cleanup cropped file on failure
        if ($Crop -and (Test-Path -LiteralPath $croppedPath)) {
            Remove-Item -LiteralPath $croppedPath -Force
        }
        continue
    }

    # Step 3: Cleanup intermediate files
    if (-not $KeepIntermediate) {
        if ($Crop -and (Test-Path -LiteralPath $croppedPath)) {
            Remove-Item -LiteralPath $croppedPath -Force
        }
    }

    # Report sizes
    $inputSize = [math]::Round($file.Length / 1MB, 2)
    $outputFile = Get-Item -LiteralPath $outputPath
    $outputSize = [math]::Round($outputFile.Length / 1MB, 2)
    $savings = [math]::Round((1 - $outputFile.Length / $file.Length) * 100, 1)

    Write-Success "$($file.Name) -> $baseName.webm ($inputSize MB -> $outputSize MB, $savings% smaller)"
    $successCount++
}

# Summary
Write-Host ''
Write-Host '==========================================' -ForegroundColor Cyan
Write-Host "  Conversion Complete" -ForegroundColor Cyan
Write-Host '==========================================' -ForegroundColor Cyan
Write-Host "  Succeeded: $successCount" -ForegroundColor Green
if ($failCount -gt 0) {
    Write-Host "  Failed:    $failCount" -ForegroundColor Red
}
Write-Host "  Output:    $OutputFolder" -ForegroundColor White
Write-Host ''
