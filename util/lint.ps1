# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

param(
    [switch]$RunPylint,
    [switch]$RunBlack,
    [switch]$RunIsort,
    [switch]$RunFlake8,
    [switch]$RunMyPy,
    [switch]$RunBandit,
    [switch]$RunImportTests,
    [switch]$All,
    [switch]$Fix
)

# Set console to UTF-8 for Unicode support
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
chcp 65001 | Out-Null  # Set console code page to UTF-8

# Configuration
$PYTHON_PATH = "python"
$PYLINT_PATH = "pylint"
$SRC_DIR = "src\gaia"
$TEST_DIR = "tests"
$INSTALLER_DIR = "installer"
$PYLINT_CONFIG = ".pylintrc"
$DISABLED_CHECKS = "C0103,C0301,W0246,W0221,E1102,R0401,E0401,W0718"
$EXCLUDE_DIRS = ".git,__pycache__,venv,.venv,.mypy_cache,.tox,.eggs,_build,buck-out,node_modules"

$ErrorCount = 0
$WarningCount = 0

# Track individual check results
$script:BlackPassed = $true
$script:IsortPassed = $true
$script:PylintPassed = $true
$script:Flake8Passed = $true
$script:MyPyPassed = $true
$script:ImportsPassed = $true
$script:BanditPassed = $true

# Track issue counts per check
$script:BlackIssues = 0
$script:IsortIssues = 0
$script:PylintIssues = 0
$script:Flake8Issues = 0
$script:MyPyIssues = 0
$script:ImportsIssues = 0
$script:BanditIssues = 0

# Function to run Black
function Invoke-Black {
    Write-Host "`n[1/7] Checking code formatting with Black..." -ForegroundColor Cyan
    Write-Host "----------------------------------------"

    if ($Fix) {
        $blackOutput = & $PYTHON_PATH -m black $INSTALLER_DIR $SRC_DIR $TEST_DIR --config pyproject.toml 2>&1 | Out-String -Width 4096
    } else {
        $blackOutput = & $PYTHON_PATH -m black --check --diff $INSTALLER_DIR $SRC_DIR $TEST_DIR --config pyproject.toml 2>&1 | Out-String -Width 4096
    }

    if ($LASTEXITCODE -ne 0) {
        # Count files that would be reformatted
        $script:BlackIssues = ($blackOutput | Select-String "would reformat" | Measure-Object).Count
        if ($script:BlackIssues -eq 0) { $script:BlackIssues = 1 }

        Write-Host "`n[!] Code formatting issues found." -ForegroundColor Red
        if (-not $Fix) {
            Write-Host "Fix with: powershell util\lint.ps1 -RunBlack -Fix" -ForegroundColor Yellow
        }
        $script:ErrorCount++
        $script:BlackPassed = $false
        return $false
    }
    Write-Host "[OK] Code formatting looks good!" -ForegroundColor Green
    $script:BlackPassed = $true
    return $true
}

# Function to run isort
function Invoke-Isort {
    Write-Host "`n[2/7] Checking import sorting with isort..." -ForegroundColor Cyan
    Write-Host "----------------------------------------"

    if ($Fix) {
        $isortOutput = & $PYTHON_PATH -m isort $INSTALLER_DIR $SRC_DIR $TEST_DIR 2>&1 | Out-String -Width 4096
    } else {
        $isortOutput = & $PYTHON_PATH -m isort --check-only --diff $INSTALLER_DIR $SRC_DIR $TEST_DIR 2>&1 | Out-String -Width 4096
    }

    if ($LASTEXITCODE -ne 0) {
        # Count files with import issues
        $script:IsortIssues = ($isortOutput | Select-String "would reformat|ERROR" | Measure-Object).Count
        if ($script:IsortIssues -eq 0) { $script:IsortIssues = 1 }

        Write-Host "`n[!] Import sorting issues found." -ForegroundColor Red
        if (-not $Fix) {
            Write-Host "Fix with: powershell util\lint.ps1 -RunIsort -Fix" -ForegroundColor Yellow
        }
        $script:ErrorCount++
        $script:IsortPassed = $false
        return $false
    }
    Write-Host "[OK] Import sorting looks good!" -ForegroundColor Green
    $script:IsortPassed = $true
    return $true
}

# Function to run Pylint
function Invoke-Pylint {
    Write-Host "`n[3/7] Running Pylint (errors only)..." -ForegroundColor Cyan
    Write-Host "----------------------------------------"

    $pylintOutput = & $PYTHON_PATH -m $PYLINT_PATH $SRC_DIR --rcfile $PYLINT_CONFIG --disable $DISABLED_CHECKS 2>&1 | Out-String -Width 4096

    if ($LASTEXITCODE -ne 0) {
        # Count error lines (lines starting with file path and containing error indicators)
        $script:PylintIssues = ($pylintOutput | Select-String ":\d+:\d+: [EF]\d+" | Measure-Object).Count
        if ($script:PylintIssues -eq 0) { $script:PylintIssues = 1 }

        Write-Host "`n[!] Pylint found critical errors:" -ForegroundColor Red
        Write-Host $pylintOutput -ForegroundColor Yellow
        $script:ErrorCount++
        $script:PylintPassed = $false
        return $false
    }
    Write-Host "[OK] No critical Pylint errors!" -ForegroundColor Green
    $script:PylintPassed = $true
    return $true
}

# Function to run Flake8
function Invoke-Flake8 {
    Write-Host "`n[4/7] Running Flake8..." -ForegroundColor Cyan
    Write-Host "----------------------------------------"

    $flake8Output = & $PYTHON_PATH -m flake8 $INSTALLER_DIR $SRC_DIR $TEST_DIR --exclude=$EXCLUDE_DIRS --count --statistics --max-line-length=88 --extend-ignore=E203,W503,E501,F541,W291,W293,E402,F841,E722 2>&1 | Out-String -Width 4096

    if ($LASTEXITCODE -ne 0) {
        # Count actual violation lines (format: filepath:line:col: error_code message)
        $script:Flake8Issues = ($flake8Output | Select-String "\.py:\d+:\d+: [A-Z]\d+" | Measure-Object).Count
        if ($script:Flake8Issues -eq 0) { $script:Flake8Issues = 1 }

        Write-Host "`n[!] Flake8 found style issues:" -ForegroundColor Red
        # Split output into lines and display each one
        $flake8Output -split "`n" | Where-Object { $_.Trim() -ne "" } | ForEach-Object {
            Write-Host "   $_" -ForegroundColor Yellow
        }
        $script:ErrorCount++
        $script:Flake8Passed = $false
        return $false
    }
    Write-Host "[OK] Flake8 checks passed!" -ForegroundColor Green
    $script:Flake8Passed = $true
    return $true
}

# Function to run MyPy
function Invoke-MyPy {
    Write-Host "`n[5/7] Running MyPy type checking (warning only)..." -ForegroundColor Cyan
    Write-Host "----------------------------------------"

    $mypyOutput = & $PYTHON_PATH -m mypy $SRC_DIR --ignore-missing-imports 2>&1 | Out-String -Width 4096

    if ($LASTEXITCODE -ne 0) {
        # Count error lines (format: filepath:line: error: message)
        $script:MyPyIssues = ($mypyOutput | Select-String "\.py:\d+: error:" | Measure-Object).Count
        if ($script:MyPyIssues -eq 0) { $script:MyPyIssues = 1 }

        Write-Host "`n[WARNING] MyPy found type issues (non-blocking):" -ForegroundColor Yellow
        # Show first 20 lines of output to avoid overwhelming the terminal
        $lines = ($mypyOutput -split "`n") | Select-Object -First 20
        $lines | ForEach-Object { Write-Host $_ -ForegroundColor DarkYellow }
        if (($mypyOutput -split "`n").Count -gt 20) {
            Write-Host "... (output truncated, showing first 20 lines)" -ForegroundColor DarkGray
        }
        $script:WarningCount++
        $script:MyPyPassed = $false
        return $true
    }
    Write-Host "[OK] Type checking passed!" -ForegroundColor Green
    $script:MyPyPassed = $true
    return $true
}

# Function to run Bandit
function Invoke-Bandit {
    Write-Host "`n[6/7] Running security check with Bandit (warning only)..." -ForegroundColor Cyan
    Write-Host "----------------------------------------"

    $banditOutput = & $PYTHON_PATH -m bandit -r $SRC_DIR -ll --exclude $EXCLUDE_DIRS 2>&1 | Out-String -Width 4096

    if ($LASTEXITCODE -ne 0) {
        # Count issue lines (look for >> Issue: patterns)
        $script:BanditIssues = ($banditOutput | Select-String ">> Issue:" | Measure-Object).Count
        if ($script:BanditIssues -eq 0) { $script:BanditIssues = 1 }

        Write-Host "`n[WARNING] Bandit found security issues (non-blocking):" -ForegroundColor Yellow
        # Show first 30 lines of output
        $lines = ($banditOutput -split "`n") | Select-Object -First 30
        $lines | ForEach-Object { Write-Host $_ -ForegroundColor DarkYellow }
        if (($banditOutput -split "`n").Count -gt 30) {
            Write-Host "... (output truncated, showing first 30 lines)" -ForegroundColor DarkGray
        }
        Write-Host "`nNote: Many are false positives for ML applications." -ForegroundColor Yellow
        $script:WarningCount++
        $script:BanditPassed = $false
        return $true
    }
    Write-Host "[OK] No security issues found!" -ForegroundColor Green
    $script:BanditPassed = $true
    return $true
}

# Function to test imports
function Invoke-ImportTests {
    Write-Host "`n[7/7] Testing critical imports..." -ForegroundColor Cyan
    Write-Host "----------------------------------------"

    $imports = @(
        @{Module="gaia.cli"; Desc="CLI module"},
        @{Module="gaia.chat.sdk"; Desc="Chat SDK"},
        @{Module="gaia.llm.llm_client"; Desc="LLM client"},
        @{Module="gaia.agents.base.agent"; Desc="Base agent"}
    )

    $failed = $false
    $script:ImportsIssues = 0
    foreach ($import in $imports) {
        & $PYTHON_PATH -c "import $($import.Module); print('OK: $($import.Desc) imports')" 2>&1 | Out-String -Width 4096
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[!] Failed to import $($import.Module)" -ForegroundColor Red
            $failed = $true
            $script:ImportsIssues++
        }
    }

    if ($failed) {
        $script:ErrorCount++
        $script:ImportsPassed = $false
        return $false
    }
    Write-Host "[OK] All imports working!" -ForegroundColor Green
    $script:ImportsPassed = $true
    return $true
}

# Print header
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Running Code Quality Checks" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Run based on arguments
# If no specific options are provided, run all by default
if (-not ($RunPylint -or $RunBlack -or $RunIsort -or $RunFlake8 -or $RunMyPy -or $RunBandit -or $RunImportTests -or $All)) {
    $All = $true
}

if ($RunBlack -or $All) {
    Invoke-Black | Out-Null
}

if ($RunIsort -or $All) {
    Invoke-Isort | Out-Null
}

if ($RunPylint -or $All) {
    Invoke-Pylint | Out-Null
}

if ($RunFlake8 -or $All) {
    Invoke-Flake8 | Out-Null
}

if ($RunMyPy -or $All) {
    Invoke-MyPy | Out-Null
}

if ($RunImportTests -or $All) {
    Invoke-ImportTests | Out-Null
}

if ($RunBandit -or $All) {
    Invoke-Bandit | Out-Null
}

# Collect file statistics
Write-Host "`nCollecting statistics..." -ForegroundColor DarkGray
$pyFiles = @(Get-ChildItem -Path $SRC_DIR,$TEST_DIR,$INSTALLER_DIR -Recurse -Filter "*.py" -File)
$totalPyFiles = $pyFiles.Count
$totalLines = 0
foreach ($file in $pyFiles) {
    $totalLines += (Get-Content $file.FullName -Encoding UTF8 -ErrorAction SilentlyContinue).Count
}

# Print summary table
Write-Host "`n"
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "                    LINT SUMMARY REPORT                        " -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan

# Print statistics
Write-Host ""
Write-Host "[STATS] Project Statistics:" -ForegroundColor White
Write-Host "   - Python Files: $totalPyFiles" -ForegroundColor Gray
Write-Host "   - Lines of Code: $($totalLines.ToString('N0'))" -ForegroundColor Gray
Write-Host "   - Directories: src/gaia, tests, installer" -ForegroundColor Gray

# Build results table
$results = @()
$passCount = 0
$failCount = 0
$warnCount = 0

# Track which checks were run
if ($RunBlack -or $All) {
    $isPassed = $script:BlackPassed
    $status = if ($isPassed) { "[OK] PASS" } else { "[X] FAIL" }
    $color = if ($isPassed) { "Green" } else { "Red" }
    $results += @{Check="Code Formatting (Black)"; Status=$status; Color=$color; Type=if($isPassed){"pass"}else{"fail"}; Issues=$script:BlackIssues}
    if ($isPassed) { $passCount++ } else { $failCount++ }
}

if ($RunIsort -or $All) {
    $isPassed = $script:IsortPassed
    $status = if ($isPassed) { "[OK] PASS" } else { "[X] FAIL" }
    $color = if ($isPassed) { "Green" } else { "Red" }
    $results += @{Check="Import Sorting (isort)"; Status=$status; Color=$color; Type=if($isPassed){"pass"}else{"fail"}; Issues=$script:IsortIssues}
    if ($isPassed) { $passCount++ } else { $failCount++ }
}

if ($RunPylint -or $All) {
    $isPassed = $script:PylintPassed
    $status = if ($isPassed) { "[OK] PASS" } else { "[X] FAIL" }
    $color = if ($isPassed) { "Green" } else { "Red" }
    $results += @{Check="Critical Errors (Pylint)"; Status=$status; Color=$color; Type=if($isPassed){"pass"}else{"fail"}; Issues=$script:PylintIssues}
    if ($isPassed) { $passCount++ } else { $failCount++ }
}

if ($RunFlake8 -or $All) {
    $isPassed = $script:Flake8Passed
    $status = if ($isPassed) { "[OK] PASS" } else { "[X] FAIL" }
    $color = if ($isPassed) { "Green" } else { "Red" }
    $results += @{Check="Style Compliance (Flake8)"; Status=$status; Color=$color; Type=if($isPassed){"pass"}else{"fail"}; Issues=$script:Flake8Issues}
    if ($isPassed) { $passCount++ } else { $failCount++ }
}

if ($RunMyPy -or $All) {
    $isPassed = $script:MyPyPassed
    $status = if (-not $isPassed) { "[!] WARN" } else { "[OK] PASS" }
    $color = if (-not $isPassed) { "Yellow" } else { "Green" }
    $results += @{Check="Type Checking (MyPy)"; Status=$status; Color=$color; Type=if($isPassed){"pass"}else{"warn"}; Issues=$script:MyPyIssues}
    if ($isPassed) { $passCount++ } else { $warnCount++ }
}

if ($RunImportTests -or $All) {
    $isPassed = $script:ImportsPassed
    $status = if ($isPassed) { "[OK] PASS" } else { "[X] FAIL" }
    $color = if ($isPassed) { "Green" } else { "Red" }
    $results += @{Check="Import Validation"; Status=$status; Color=$color; Type=if($isPassed){"pass"}else{"fail"}; Issues=$script:ImportsIssues}
    if ($isPassed) { $passCount++ } else { $failCount++ }
}

if ($RunBandit -or $All) {
    $isPassed = $script:BanditPassed
    $status = if (-not $isPassed) { "[!] WARN" } else { "[OK] PASS" }
    $color = if (-not $isPassed) { "Yellow" } else { "Green" }
    $results += @{Check="Security Check (Bandit)"; Status=$status; Color=$color; Type=if($isPassed){"pass"}else{"warn"}; Issues=$script:BanditIssues}
    if ($isPassed) { $passCount++ } else { $warnCount++ }
}

# Print table
Write-Host ""
Write-Host "[RESULTS] Quality Check Results:" -ForegroundColor White
Write-Host ""
Write-Host "+--------------------------------+------------+-----------+" -ForegroundColor Cyan
Write-Host "| Check                          | Status     | Issues    |" -ForegroundColor Cyan
Write-Host "+--------------------------------+------------+-----------+" -ForegroundColor Cyan

foreach ($result in $results) {
    $checkPadded = $result.Check.PadRight(30)
    $statusPadded = $result.Status.PadRight(10)

    # Determine issue count display
    $issueCount = ""
    if ($result.Type -eq "fail") {
        $count = $result.Issues
        $issueCount = if ($count -eq 1) { "1 error" } else { "$count errors" }
        $issueCount = $issueCount.PadRight(9)
        $issueColor = "Red"
    } elseif ($result.Type -eq "warn") {
        $count = $result.Issues
        $issueCount = if ($count -eq 1) { "1 warning" } else { "$count warns" }
        $issueCount = $issueCount.PadRight(9)
        $issueColor = "Yellow"
    } else {
        $issueCount = "-".PadRight(9)
        $issueColor = "Gray"
    }

    Write-Host "| $checkPadded | " -ForegroundColor Cyan -NoNewline
    Write-Host $statusPadded -ForegroundColor $result.Color -NoNewline
    Write-Host " | " -ForegroundColor Cyan -NoNewline
    Write-Host $issueCount -ForegroundColor $issueColor -NoNewline
    Write-Host " |" -ForegroundColor Cyan
}

Write-Host "+--------------------------------+------------+-----------+" -ForegroundColor Cyan

# Print detailed statistics
$totalChecks = $results.Count
Write-Host ""
Write-Host "[SUMMARY] Statistics:" -ForegroundColor White
Write-Host "   - Total Checks Run: $totalChecks" -ForegroundColor Gray
Write-Host "   - Passed: " -NoNewline -ForegroundColor Gray
Write-Host $passCount -ForegroundColor Green -NoNewline
Write-Host " ($([math]::Round($passCount/$totalChecks*100,1))%)" -ForegroundColor Gray
Write-Host "   - Failed: " -NoNewline -ForegroundColor Gray
Write-Host $failCount -ForegroundColor Red -NoNewline
Write-Host " ($([math]::Round($failCount/$totalChecks*100,1))%)" -ForegroundColor Gray
Write-Host "   - Warnings: " -NoNewline -ForegroundColor Gray
Write-Host $warnCount -ForegroundColor Yellow -NoNewline
Write-Host " ($([math]::Round($warnCount/$totalChecks*100,1))%)" -ForegroundColor Gray

# Print final verdict
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
if ($ErrorCount -eq 0) {
    Write-Host "[SUCCESS] ALL QUALITY CHECKS PASSED!" -ForegroundColor Green
    if ($WarningCount -gt 0) {
        Write-Host "[WARNING] $WarningCount warning(s) found (non-blocking)" -ForegroundColor Yellow
    }
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "[OK] Your code meets quality standards!" -ForegroundColor Green
    Write-Host "[OK] Ready for PR submission" -ForegroundColor Green
    exit 0
} else {
    Write-Host "[FAILED] QUALITY CHECKS FAILED" -ForegroundColor Red
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "[ERROR] Issues Found:" -ForegroundColor Red
    Write-Host "   - $ErrorCount critical error(s) - must fix before PR" -ForegroundColor Red
    if ($WarningCount -gt 0) {
        Write-Host "   - $WarningCount warning(s) - non-blocking" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "[TIP] Review the error messages above and fix the issues." -ForegroundColor Yellow
    Write-Host "[TIP] Use -Fix flag to auto-fix formatting issues:" -ForegroundColor Yellow
    Write-Host "   powershell util\lint.ps1 -RunBlack -Fix" -ForegroundColor Gray
    Write-Host "   powershell util\lint.ps1 -RunIsort -Fix" -ForegroundColor Gray
    exit 1
}