# GAIA Installer for Windows
# One-command installation: irm https://amd-gaia.ai/install.ps1 | iex

$ErrorActionPreference = "Stop"

# Configuration
$GAIA_HOME = "$env:USERPROFILE\.gaia"
$GAIA_VENV = "$GAIA_HOME\venv"
$PYTHON_VERSION = "3.12"

# Colors for output
$COLOR_GREEN = "Green"
$COLOR_YELLOW = "Yellow"
$COLOR_RED = "Red"
$COLOR_CYAN = "Cyan"

function Write-Step {
    param([string]$Message)
    Write-Host "[*] $Message" -ForegroundColor $COLOR_CYAN
}

function Write-Success {
    param([string]$Message)
    Write-Host "[✓] $Message" -ForegroundColor $COLOR_GREEN
}

function Write-Error {
    param([string]$Message)
    Write-Host "[✗] $Message" -ForegroundColor $COLOR_RED
}

function Write-Warning {
    param([string]$Message)
    Write-Host "[!] $Message" -ForegroundColor $COLOR_YELLOW
}

function Install-Uv {
    Write-Step "Checking for uv package manager..."

    try {
        $uvCmd = Get-Command uv -ErrorAction Stop
        Write-Success "uv is already installed"
        return $true
    }
    catch {
        Write-Step "Installing uv package manager..."
        try {
            irm https://astral.sh/uv/install.ps1 | iex

            # Refresh PATH to include uv
            $env:PATH = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")

            Write-Success "uv installed successfully"
            return $true
        }
        catch {
            Write-Error "Failed to install uv: $_"
            exit 1
        }
    }
}

function Install-Gaia {
    # Check if GAIA is already installed
    $gaiaExe = "$GAIA_VENV\Scripts\gaia.exe"
    if (Test-Path $gaiaExe) {
        Write-Warning "GAIA is already installed at $GAIA_HOME"
        Write-Host "  To reinstall, delete the directory first: Remove-Item -Recurse -Force '$GAIA_HOME'" -ForegroundColor $COLOR_YELLOW
        return
    }

    Write-Step "Creating GAIA environment at $GAIA_HOME..."

    # Create GAIA home directory
    if (-not (Test-Path $GAIA_HOME)) {
        New-Item -ItemType Directory -Path $GAIA_HOME -Force | Out-Null
        Write-Success "Created directory: $GAIA_HOME"
    }
    else {
        Write-Warning "Directory already exists: $GAIA_HOME"
    }

    # Create virtual environment with Python 3.12 (uv will download if needed)
    Write-Step "Creating virtual environment with Python $PYTHON_VERSION..."
    Write-Host "  (uv will automatically download Python $PYTHON_VERSION if not installed)" -ForegroundColor $COLOR_YELLOW
    try {
        & uv venv $GAIA_VENV --python $PYTHON_VERSION
        Write-Success "Virtual environment created"
    }
    catch {
        Write-Error "Failed to create virtual environment: $_"
        exit 1
    }

    # Activate virtual environment
    Write-Step "Installing GAIA package..."
    try {
        $activateScript = "$GAIA_VENV\Scripts\Activate.ps1"
        & $activateScript

        # Install GAIA
        & uv pip install amd-gaia

        Write-Success "GAIA package installed successfully"
    }
    catch {
        Write-Error "Failed to install GAIA package: $_"
        exit 1
    }
}

function Add-ToPath {
    Write-Step "Adding GAIA to PATH..."

    $scriptsPath = "$GAIA_VENV\Scripts"
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")

    # Check if already in PATH
    if ($currentPath -like "*$scriptsPath*") {
        Write-Success "GAIA is already in PATH"
        return
    }

    # Add to PATH
    try {
        $newPath = "$currentPath;$scriptsPath"
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")

        # Update current session PATH
        $env:PATH = "$env:PATH;$scriptsPath"

        Write-Success "Added GAIA to PATH"
    }
    catch {
        Write-Warning "Failed to add GAIA to PATH automatically"
        Write-Host "Please add the following directory to your PATH manually:" -ForegroundColor $COLOR_YELLOW
        Write-Host "  $scriptsPath" -ForegroundColor $COLOR_YELLOW
    }
}

function Show-NextSteps {
    Write-Host "`n" -NoNewline
    Write-Host "================================" -ForegroundColor $COLOR_GREEN
    Write-Host "  GAIA Installed Successfully!" -ForegroundColor $COLOR_GREEN
    Write-Host "================================" -ForegroundColor $COLOR_GREEN
    Write-Host "`n"

    Write-Host "Next steps:" -ForegroundColor $COLOR_CYAN
    Write-Host "  1. Close and reopen your terminal (or run: refreshenv)" -ForegroundColor White
    Write-Host "  2. Run: " -ForegroundColor White -NoNewline
    Write-Host "gaia init" -ForegroundColor $COLOR_GREEN -NoNewline
    Write-Host " to set up Lemonade Server and download models" -ForegroundColor White
    Write-Host "  3. Start chatting: " -ForegroundColor White -NoNewline
    Write-Host "gaia chat" -ForegroundColor $COLOR_GREEN
    Write-Host "`n"

    Write-Host "Documentation: https://amd-gaia.ai" -ForegroundColor $COLOR_CYAN
    Write-Host "Issues: https://github.com/amd/gaia/issues" -ForegroundColor $COLOR_CYAN
    Write-Host "`n"
}

function Main {
    Write-Host "`n"
    Write-Host "========================================" -ForegroundColor $COLOR_CYAN
    Write-Host "  GAIA Installer for Windows" -ForegroundColor $COLOR_CYAN
    Write-Host "========================================" -ForegroundColor $COLOR_CYAN
    Write-Host "`n"

    # Install uv if needed
    Install-Uv

    # Install GAIA
    Install-Gaia

    # Add to PATH
    Add-ToPath

    # Show next steps
    Show-NextSteps
}

# Run installer
Main

