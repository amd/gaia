# GAIA Installer for Windows
# One-command installation: irm https://amd-gaia.ai/install.ps1 | iex

$ErrorActionPreference = "Stop"

# Configuration
$GAIA_HOME = "$env:USERPROFILE\.gaia"
$GAIA_VENV = "$GAIA_HOME\venv"
$GAIA_BIN = "$GAIA_HOME\bin"
$PYTHON_VERSION = "3.12"

# The Agent Hub is the canonical channel for the terminal hub binary:
# release_components.yml publishes all six platform builds there.
$GAIA_HUB_BASE_URL = if ($env:GAIA_HUB_BASE_URL) { $env:GAIA_HUB_BASE_URL } else { "https://hub.amd-gaia.ai" }
$GAIA_HUB_BASE_URL = $GAIA_HUB_BASE_URL.TrimEnd('/')
$TERMINAL_HUB_ID = "terminal-hub"

# Network limit: a black-holed connection must fail, not hang silently.
$HTTP_TIMEOUT_SEC = 900

# Windows PowerShell 5.1 defaults to TLS 1.0 in a fresh session.
if ($PSVersionTable.PSVersion.Major -lt 6) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
}

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

# Warn about elevation before anything prompts for it.
function Show-ElevationNotice {
    Write-Warning "One step later on needs administrator approval:"
    Write-Host "  'gaia init' installs Lemonade Server (the local model runtime), whose" -ForegroundColor White
    Write-Host "  MSI raises a UAC prompt. This installer itself never needs elevation." -ForegroundColor White
    Write-Host "`n"
}

function Install-Uv {
    Write-Step "Checking for uv package manager..."

    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Write-Success "uv is already installed"
        return
    }

    Write-Step "Installing uv package manager..."
    try {
        irm https://astral.sh/uv/install.ps1 | iex

        # Refresh PATH to include uv
        $env:PATH = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    }
    catch {
        Write-Error "Failed to install uv: $_"
        Write-Host "  Fix:  install uv manually from" -ForegroundColor $COLOR_YELLOW
        Write-Host "        https://docs.astral.sh/uv/getting-started/installation/" -ForegroundColor $COLOR_YELLOW
        exit 1
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Error "uv installed but is not on PATH."
        Write-Host "  Fix:  close and reopen your terminal, then re-run this installer." -ForegroundColor $COLOR_YELLOW
        exit 1
    }

    Write-Success "uv installed successfully"
}

function Install-Gaia {
    # Check if GAIA is already installed
    $gaiaExe = "$GAIA_VENV\Scripts\gaia.exe"
    if (Test-Path $gaiaExe) {
        Write-Warning "GAIA is already installed at $GAIA_HOME"
        Write-Step "Checking for updates..."

        # --upgrade exits 0 when there is nothing to do, so non-zero is a real
        # failure, not "already current".
        & "$GAIA_VENV\Scripts\python.exe" -m pip install --upgrade amd-gaia --quiet
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to update the GAIA package in $GAIA_VENV (exit $LASTEXITCODE)."
            Write-Host "  Fix:  re-run this installer, or delete $GAIA_HOME to start clean." -ForegroundColor $COLOR_YELLOW
            exit 1
        }
        Write-Success "GAIA is up to date"
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
    # A non-zero native exit does not throw, so $LASTEXITCODE is the only signal.
    & uv venv $GAIA_VENV --python $PYTHON_VERSION
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create the virtual environment at $GAIA_VENV (uv exit $LASTEXITCODE)."
        exit 1
    }
    Write-Success "Virtual environment created"

    # Activate virtual environment
    Write-Step "Installing GAIA package..."
    try {
        $activateScript = "$GAIA_VENV\Scripts\Activate.ps1"
        & $activateScript
    }
    catch {
        Write-Error "Failed to activate the virtual environment at $GAIA_VENV`: $_"
        exit 1
    }

    & uv pip install amd-gaia
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to install the GAIA package (uv exit $LASTEXITCODE)."
        Write-Host "  Fix:  re-run this installer; if it persists report it at" -ForegroundColor $COLOR_YELLOW
        Write-Host "        https://github.com/amd/gaia/issues" -ForegroundColor $COLOR_YELLOW
        exit 1
    }
    Write-Success "GAIA package installed successfully"
}

function Get-TerminalHubPlatform {
    # The Agent Hub platform keys are what release_components.yml publishes
    # under, so they are the authority - not GOARCH ("amd64").
    $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($arch) {
        "AMD64" { return "win-x64" }
        "ARM64" { return "win-arm64" }
        default { return $null }
    }
}

# A missing terminal hub fails the install - it is the advertised entry point.
function Install-Tui {
    Write-Step "Installing the GAIA terminal hub"

    $platform = Get-TerminalHubPlatform
    if (-not $platform) {
        $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
        Write-Error "No terminal hub build for processor architecture '$arch'."
        Write-Host "  Published targets: win-x64, win-arm64, linux-x64, linux-arm64," -ForegroundColor $COLOR_YELLOW
        Write-Host "  darwin-x64, darwin-arm64. See $GAIA_HUB_BASE_URL/index.json" -ForegroundColor $COLOR_YELLOW
        exit 1
    }
    $filename = "gaia-$platform.exe"

    # The manifest is the only source for the version, the per-platform
    # filename, and the Worker-computed SHA-256.
    $manifestUrl = "$GAIA_HUB_BASE_URL/agents/$TERMINAL_HUB_ID/manifest.json"
    try {
        $manifest = Invoke-RestMethod -Uri $manifestUrl -UseBasicParsing -TimeoutSec $HTTP_TIMEOUT_SEC
    }
    catch {
        Write-Error "Could not fetch the terminal hub manifest: $_"
        Write-Host "  URL:  $manifestUrl" -ForegroundColor $COLOR_YELLOW
        Write-Host "  Fix:  check your network, then retry. If the component is not yet" -ForegroundColor $COLOR_YELLOW
        Write-Host "        published for this release, build from source:" -ForegroundColor $COLOR_YELLOW
        Write-Host "          git clone https://github.com/amd/gaia; cd gaia\tui; make build" -ForegroundColor $COLOR_YELLOW
        Write-Host "  Look: $GAIA_HUB_BASE_URL/index.json lists what the hub serves." -ForegroundColor $COLOR_YELLOW
        exit 1
    }

    $version = $manifest.latest_version
    if (-not $version) {
        Write-Error "The hub manifest at $manifestUrl declares no latest_version."
        exit 1
    }

    $entry = $manifest.versions.$version
    if (-not $entry) {
        Write-Error "The hub manifest names latest_version $version but publishes no such version."
        Write-Host "  Look: $manifestUrl" -ForegroundColor $COLOR_YELLOW
        exit 1
    }

    $artifacts = @($entry.artifacts)
    if (-not $artifacts -or $artifacts.Count -eq 0) {
        $artifacts = if ($entry.artifact) { @($entry.artifact) } else { @() }
    }

    $match = $artifacts | Where-Object { $_.filename -eq $filename } | Select-Object -First 1
    if (-not $match) {
        $listed = ($artifacts | ForEach-Object { $_.filename } | Sort-Object) -join ", "
        if (-not $listed) { $listed = "none" }
        Write-Error "Terminal hub $version publishes no $filename (it publishes: $listed)."
        Write-Host "  Fix:  if your platform is genuinely unpublished, build from source:" -ForegroundColor $COLOR_YELLOW
        Write-Host "          git clone https://github.com/amd/gaia; cd gaia\tui; make build" -ForegroundColor $COLOR_YELLOW
        Write-Host "  Look: $manifestUrl" -ForegroundColor $COLOR_YELLOW
        exit 1
    }

    $want = $match.sha256
    if (-not $want) {
        Write-Error "Terminal hub $version publishes $filename with no SHA-256 - refusing to install unverified."
        Write-Host "  Look: $manifestUrl" -ForegroundColor $COLOR_YELLOW
        exit 1
    }

    $binaryUrl = "$GAIA_HUB_BASE_URL/agents/$TERMINAL_HUB_ID/$version/$filename"
    $tmpDir = Join-Path $env:TEMP ("gaia-tui-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
    $tmpFile = Join-Path $tmpDir $filename

    try {
        Write-Step "Downloading terminal hub $version for $platform"
        try {
            $prevProgress = $ProgressPreference
            $ProgressPreference = "SilentlyContinue"
            try {
                Invoke-WebRequest -Uri $binaryUrl -OutFile $tmpFile -UseBasicParsing -TimeoutSec $HTTP_TIMEOUT_SEC
            }
            finally {
                $ProgressPreference = $prevProgress
            }
        }
        catch {
            Write-Error "Could not download the terminal hub binary: $_"
            Write-Host "  URL:  $binaryUrl" -ForegroundColor $COLOR_YELLOW
            Write-Host "  Fix:  check your network and retry." -ForegroundColor $COLOR_YELLOW
            Write-Host "  Look: $manifestUrl lists what is published for $version." -ForegroundColor $COLOR_YELLOW
            exit 1
        }

        # No checksum, no install.
        $got = (Get-FileHash -Path $tmpFile -Algorithm SHA256).Hash
        if ($got -ne $want.ToUpper()) {
            Write-Error "Checksum mismatch for $filename - refusing to install."
            Write-Host "  expected $($want.ToUpper())" -ForegroundColor $COLOR_YELLOW
            Write-Host "  got      $got" -ForegroundColor $COLOR_YELLOW
            Write-Host "  Fix:  retry; if it persists report it at https://github.com/amd/gaia/issues" -ForegroundColor $COLOR_YELLOW
            Write-Host "  Look: $manifestUrl" -ForegroundColor $COLOR_YELLOW
            exit 1
        }

        # Never `gaia.exe`: tui/internal/daemon/client.go resolves `gaia` on PATH
        # to start the Python-owned daemon, so a Go binary by that name finds
        # itself.
        if (-not (Test-Path $GAIA_BIN)) {
            New-Item -ItemType Directory -Path $GAIA_BIN -Force | Out-Null
        }
        try {
            Move-Item -Path $tmpFile -Destination "$GAIA_BIN\gaia-tui.exe" -Force
        }
        catch {
            Write-Error "Downloaded and verified, but could not write $GAIA_BIN\gaia-tui.exe`: $_"
            Write-Host "  Fix:  close any running gaia-tui (and any tool scanning that" -ForegroundColor $COLOR_YELLOW
            Write-Host "        folder), then re-run this installer." -ForegroundColor $COLOR_YELLOW
            exit 1
        }
        Write-Success "Terminal hub $version installed to $GAIA_BIN\gaia-tui.exe"
    }
    finally {
        if (Test-Path $tmpDir) { Remove-Item -Path $tmpDir -Recurse -Force }
    }
}

function Add-ToPath {
    Write-Step "Adding GAIA to PATH..."

    # Both dirs: the venv holds the Python CLI, $GAIA_BIN holds the terminal hub.
    $wanted = @("$GAIA_VENV\Scripts", $GAIA_BIN)
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $missing = @($wanted | Where-Object { $currentPath -notlike "*$_*" })

    if ($missing.Count -eq 0) {
        Write-Success "GAIA is already in PATH"
        return
    }

    try {
        # Split/filter first: an empty element (from a trailing ";") is resolved
        # as the current directory by every process that reads this PATH.
        $existing = @($currentPath -split ";" | Where-Object { $_ })
        $newPath = ($existing + $missing) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")

        # Update current session PATH
        $session = @($env:PATH -split ";" | Where-Object { $_ })
        $env:PATH = ($session + $missing) -join ";"

        Write-Success "Added GAIA to PATH"
    }
    catch {
        Write-Warning "Failed to add GAIA to PATH automatically"
        Write-Host "Please add the following directories to your PATH manually:" -ForegroundColor $COLOR_YELLOW
        foreach ($dir in $wanted) {
            Write-Host "  $dir" -ForegroundColor $COLOR_YELLOW
        }
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
    Write-Host "     (the Lemonade installer asks for administrator approval)" -ForegroundColor White
    Write-Host "  3. Open the terminal hub: " -ForegroundColor White -NoNewline
    Write-Host "gaia-tui" -ForegroundColor $COLOR_GREEN
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

    # $env:USERPROFILE is empty off Windows, which would target the filesystem
    # root. PS 5.1 is Windows-only, so $IsWindows only exists to be checked here.
    if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) {
        Write-Error "This installer is for Windows. Detected: $([System.Environment]::OSVersion.Platform)"
        Write-Host "On Linux and macOS, run instead:" -ForegroundColor $COLOR_YELLOW
        Write-Host "  curl -fsSL https://amd-gaia.ai/install.sh | sh" -ForegroundColor $COLOR_YELLOW
        exit 1
    }

    Show-ElevationNotice

    # Install uv if needed
    Install-Uv

    # Install GAIA
    Install-Gaia

    # Before the terminal hub, so a hard failure there still leaves the Python
    # CLI reachable.
    Add-ToPath

    # Install the terminal hub binary
    Install-Tui

    # Show next steps
    Show-NextSteps
}

# Run installer
Main

