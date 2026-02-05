# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Compress and convert video recordings for documentation
# Usage: .\compress-video.ps1 -InputPath recording.mp4 [-Output output.mp4] [-Quality 20] [-Format mp4]

param(
    [Parameter(Mandatory=$true)]
    [string]$InputPath,

    [string]$Output,

    [ValidateRange(18, 35)]
    [int]$Quality = 20,

    [ValidateSet("mp4", "webm")]
    [string]$Format = "mp4",

    [switch]$Crop,

    [int]$CropPixels = 5,

    [switch]$Preview,

    [switch]$Help
)

# Helper functions with ASCII symbols for compatibility
function Write-Step { param($msg) Write-Host "  -> $msg" -ForegroundColor Cyan }
function Write-Success { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Err { param($msg) Write-Host "  [X] $msg" -ForegroundColor Red }

if ($Help) {
    Write-Host ''
    Write-Host '  Video Compression Script for GAIA Documentation' -ForegroundColor Cyan
    Write-Host '  ================================================' -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  Usage:' -ForegroundColor White
    Write-Host '    .\compress-video.ps1 -InputPath <file> [options]'
    Write-Host ''
    Write-Host '  Options:' -ForegroundColor White
    Write-Host '    -InputPath <file>   Input video file (required)'
    Write-Host '    -Output <file>      Output filename (default: input-compressed.ext)'
    Write-Host '    -Quality <18-35>    CRF value, lower=better (default: 20)'
    Write-Host '    -Format <mp4|webm>  Output format (default: mp4)'
    Write-Host '    -Crop               Enable cropping of edges'
    Write-Host '    -CropPixels <int>   Pixels to crop from each edge (default: 5)'
    Write-Host '    -Preview            Generate a 10-second preview instead'
    Write-Host '    -Help               Show this help'
    Write-Host ''
    Write-Host '  Quality Guide:' -ForegroundColor White
    Write-Host '    18-20  High quality (recommended for text/terminal)'
    Write-Host '    23-25  Medium quality'
    Write-Host '    28-35  Lower quality, smaller file'
    Write-Host ''
    Write-Host '  Examples:' -ForegroundColor White
    Write-Host '    .\compress-video.ps1 -InputPath demo.mp4'
    Write-Host '    .\compress-video.ps1 -InputPath demo.mp4 -Format webm'
    Write-Host '    .\compress-video.ps1 -InputPath demo.mp4 -Crop'
    Write-Host '    .\compress-video.ps1 -InputPath demo.mp4 -Crop -CropPixels 10'
    Write-Host '    .\compress-video.ps1 -InputPath demo.mp4 -Preview'
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
    Write-Host '  Or download from: https://ffmpeg.org/download.html' -ForegroundColor Gray
    exit 1
}
Write-Success 'ffmpeg found'

# Check input file exists
if (-not (Test-Path -LiteralPath $InputPath)) {
    Write-Err "Input file not found: $InputPath"
    exit 1
}

$inputFile = Get-Item -LiteralPath $InputPath
$inputSizeMB = [math]::Round($inputFile.Length / 1MB, 2)
Write-Step "Input: $($inputFile.Name) ($inputSizeMB MB)"

# Generate output filename if not provided
if (-not $Output) {
    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($InputPath)
    if ($Format -eq 'webm') {
        # WebM: just change extension (no -compressed suffix)
        $Output = "${baseName}.${Format}"
    } else {
        # MP4: add -compressed to avoid overwriting original
        $Output = "${baseName}-compressed.${Format}"
    }
}

# Modify output for preview mode
if ($Preview) {
    $outputBase = [System.IO.Path]::GetFileNameWithoutExtension($Output)
    $Output = "${outputBase}-preview.${Format}"
}

# Handle cropping if enabled
$compressSource = $InputPath
$croppedPath = $null

if ($Crop) {
    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($InputPath)
    $inputDir = [System.IO.Path]::GetDirectoryName($InputPath)
    if (-not $inputDir) { $inputDir = "." }
    $croppedPath = Join-Path $inputDir "${baseName}-cropped.mp4"

    Write-Step "Cropping ($CropPixels px from each edge)..."
    $cropFilter = "crop=iw-$($CropPixels*2):ih-$($CropPixels*2):${CropPixels}:${CropPixels}"
    & ffmpeg -i $InputPath -vf $cropFilter -c:a copy -y $croppedPath 2>$null

    if ($LASTEXITCODE -ne 0) {
        Write-Err "Failed to crop video"
        exit 1
    }
    $compressSource = $croppedPath
}

# Build ffmpeg arguments as a simple array
$ffmpegArgs = @(
    '-i', $compressSource,
    '-y'  # Overwrite output
)

# Add preview seek/duration if requested
if ($Preview) {
    $ffmpegArgs += @('-ss', '5', '-t', '10')
}

# Add codec-specific arguments
if ($Format -eq 'mp4') {
    # H.264 encoding for MP4 (web-optimized)
    # Reference: https://slhck.info/video/2017/02/24/crf-guide.html
    $ffmpegArgs += @(
        '-c:v', 'libx264',
        '-preset', 'slow',
        '-profile:v', 'high',
        '-crf', [string]$Quality,
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-movflags', '+faststart'
    )
} else {
    # VP9 encoding for WebM
    # Reference: https://developers.google.com/media/vp9/the-basics
    # Note: VP9 CRF scale is 0-63, but 18-35 range works well
    $ffmpegArgs += @(
        '-c:v', 'libvpx-vp9',
        '-crf', [string]$Quality,
        '-b:v', '0',
        '-quality', 'good',
        '-speed', '2',
        '-row-mt', '1',
        '-c:a', 'libopus',
        '-b:a', '128k'
    )
}

# Add output file
$ffmpegArgs += $Output

Write-Step "Compressing (CRF: $Quality, Format: $Format)..."
Write-Host "  Output: $Output" -ForegroundColor Gray
Write-Host ''

# Run ffmpeg using call operator (more reliable than Start-Process)
& ffmpeg @ffmpegArgs
$exitCode = $LASTEXITCODE

# Cleanup cropped intermediate file
if ($Crop -and $croppedPath -and (Test-Path -LiteralPath $croppedPath)) {
    Remove-Item -LiteralPath $croppedPath -Force
}

if ($exitCode -eq 0 -and (Test-Path -LiteralPath $Output)) {
    $outputFile = Get-Item -LiteralPath $Output
    $outputSizeMB = [math]::Round($outputFile.Length / 1MB, 2)
    $savings = [math]::Round((1 - $outputFile.Length / $inputFile.Length) * 100, 1)

    Write-Host ''
    Write-Success 'Compression complete!'
    Write-Host ''
    Write-Host "  Output: $Output" -ForegroundColor White
    Write-Host "  Size:   $outputSizeMB MB ($savings% smaller)" -ForegroundColor Gray
    Write-Host ''
    Write-Host '  Upload to R2:' -ForegroundColor White
    Write-Host "    rclone copy `"$Output`" gaia:amd-gaia/videos/ --s3-no-check-bucket" -ForegroundColor Cyan
    Write-Host ''
} else {
    Write-Host ''
    Write-Err "Compression failed (exit code: $exitCode)"
    exit 1
}
