# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

param(
    [switch]$RunPylint,
    [switch]$RunBlack,
    [switch]$All
)

# Configuration
$PYTHON_PATH = "python"
$PYLINT_PATH = "pylint"
$SRC_DIR = "src\gaia"
$PYLINT_CONFIG = ".pylintrc"
$DISABLED_CHECKS = "C0103,C0301,W0246,W0221,E1102,R0401,E0401,W0718"
$EXCLUDE = "ui_form.py"

# Function to run Black
function Invoke-Black {
    Write-Host "Running black..."
    & $PYTHON_PATH -m black installer src tests --config pyproject.toml
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Black formatting failed." -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "Black formatting completed successfully." -ForegroundColor Green
}

# Function to run Pylint
function Invoke-Pylint {
    Write-Host "Running pylint..."
    & $PYTHON_PATH -m $PYLINT_PATH $SRC_DIR --rcfile $PYLINT_CONFIG --disable $DISABLED_CHECKS --ignore-paths $EXCLUDE
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Pylint check failed." -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "Pylint check completed successfully." -ForegroundColor Green
}

# Run based on arguments
# If no specific options are provided, run all by default
if (-not ($RunPylint -or $RunBlack -or $All)) {
    $All = $true
}

if ($RunBlack -or $All) {
    Invoke-Black
}

if ($RunPylint -or $All) {
    Invoke-Pylint
}
