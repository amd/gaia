# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# find_files.ps1
# Script to find files of specific types that DO NOT contain a specific string
# Usage: .\find_files.ps1 -FileTypes "*.py,*.md" -SearchString "(C) 2024-2025" -Path "C:\YourProjectPath" -IgnoreTypes "*.pyc,*.pyo" -IncludeInit -ExcludeMatches

param (
    [Parameter(Mandatory=$false)]
    [string]$FileTypes = "*.py,*.md,*.ps1,*.yml",
    
    [Parameter(Mandatory=$false)]
    [string]$SearchString = "(C) 2024-2025",
    
    [Parameter(Mandatory=$false)]
    [string]$Path = ".",
    
    [Parameter(Mandatory=$false)]
    [switch]$Recursive = $true,
    
    [Parameter(Mandatory=$false)]
    [string]$IgnoreTypes = "*.pyc,*.pyo",
    
    [Parameter(Mandatory=$false)]
    [switch]$IncludeInit = $false,
    
    [Parameter(Mandatory=$false)]
    [switch]$ExcludeMatches = $false
)

# Display script parameters
Write-Host "Starting search with the following parameters:" -ForegroundColor Cyan
Write-Host "  File Types: $FileTypes" -ForegroundColor Cyan
Write-Host "  Search String: $SearchString" -ForegroundColor Cyan
Write-Host "  Path: $Path" -ForegroundColor Cyan
Write-Host "  Recursive: $Recursive" -ForegroundColor Cyan
Write-Host "  Ignore Types: $IgnoreTypes" -ForegroundColor Cyan
Write-Host "  Include __init__.py: $IncludeInit" -ForegroundColor Cyan
Write-Host "  Mode: $(if ($ExcludeMatches) {"Files WITHOUT search string"} else {"Files WITH search string"})" -ForegroundColor Cyan
Write-Host ""

# Convert comma-separated file types to array
$fileTypeArray = $FileTypes.Split(",")

# Convert comma-separated ignore types to array
$ignoreTypeArray = $IgnoreTypes.Split(",")

# Prepare parameters for Get-ChildItem
$getChildItemParams = @{
    Path = $Path
    Include = $fileTypeArray
}

if ($Recursive) {
    $getChildItemParams.Add("Recurse", $true)
}

# Count variables for statistics
$totalFilesScanned = 0
$filesWithoutString = 0

# Find files with or without the string
$searchMode = if ($ExcludeMatches) {"WITHOUT"} else {"WITH"}
Write-Host "Searching for files $searchMode the string '$SearchString'..." -ForegroundColor Yellow
Write-Host ""

$results = Get-ChildItem @getChildItemParams | ForEach-Object {
    $file = $_
    $fileName = $file.Name
    $fileExtension = $file.Extension
    
    # Check if file should be ignored
    $shouldIgnore = $false
    foreach ($ignorePattern in $ignoreTypeArray) {
        if ($fileName -like $ignorePattern) {
            $shouldIgnore = $true
            break
        }
    }
    
    # Special handling for __init__.py files
    $isInitFile = $fileName -eq "__init__.py"
    if ($isInitFile -and -not $IncludeInit) {
        $shouldIgnore = $true
    } elseif ($isInitFile -and $IncludeInit) {
        # Force include __init__.py if the switch is enabled
        $shouldIgnore = $false
    }
    
    if (-not $shouldIgnore) {
        $totalFilesScanned++
        
        # Show progress for large searches
        if ($totalFilesScanned % 100 -eq 0) {
            Write-Host "Scanned $totalFilesScanned files so far..." -ForegroundColor DarkGray
        }
        
        # Check if file contains the search string
        $containsString = Select-String -Path $file.FullName -Pattern ([regex]::Escape($SearchString)) -Quiet
        
        # Determine if file should be included based on search mode
        $shouldInclude = if ($ExcludeMatches) {-not $containsString} else {$containsString}
        
        if ($shouldInclude) {
            $filesWithoutString++
            $file
        }
    }
}

# Display results
Write-Host ""
Write-Host "Results:" -ForegroundColor Green
Write-Host "--------" -ForegroundColor Green

if ($results) {
    if ($ExcludeMatches) {
        Write-Host "Files that DON'T contain '$SearchString':" -ForegroundColor Green
    } else {
        Write-Host "Files that contain '$SearchString':" -ForegroundColor Green
    }
    $results | ForEach-Object {
        Write-Host "  $_" -ForegroundColor White
    }
} else {
    Write-Host "No matching files found." -ForegroundColor Yellow
}

# Display statistics
Write-Host ""
Write-Host "Summary:" -ForegroundColor Blue
Write-Host "  Total files scanned: $totalFilesScanned" -ForegroundColor Blue
Write-Host "  Files without the string: $filesWithoutString" -ForegroundColor Blue