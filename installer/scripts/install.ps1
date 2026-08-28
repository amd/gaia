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
$FLAGSHIP_AGENT_ID = "gaia"

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
    Write-Host "[OK] $Message" -ForegroundColor $COLOR_GREEN
}

function Write-Error {
    param([string]$Message)
    Write-Host "[X] $Message" -ForegroundColor $COLOR_RED
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

        # uv, not `python -m pip`: `uv venv` creates the environment without pip,
        # so the pip form fails on every re-run.
        # --upgrade exits 0 when there is nothing to do, so non-zero is a real
        # failure, not "already current".
        & uv pip install --python "$GAIA_VENV\Scripts\python.exe" --upgrade "amd-gaia[api]" --quiet
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

    Write-Step "Installing GAIA package..."

    # Target the venv python rather than running Activate.ps1: that is a script
    # on disk, so a Restricted execution policy blocks it even though the
    # piped-in installer itself is exempt.
    & uv pip install --python "$GAIA_VENV\Scripts\python.exe" "amd-gaia[api]"
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
# Resolve one artifact's version and SHA-256 from a hub manifest. The manifest
# is the only source for both, so each installer reads it the same way.
# Returns @{Version; Sha256; Url}, or $null when the platform is unpublished.
function Resolve-HubArtifact {
    param(
        [Parameter(Mandatory)][string]$AgentId,
        [Parameter(Mandatory)][string]$Filename,
        [Parameter(Mandatory)][string]$Label
    )

    $manifestUrl = "$GAIA_HUB_BASE_URL/agents/$AgentId/manifest.json"
    try {
        $manifest = Invoke-RestMethod -Uri $manifestUrl -UseBasicParsing -TimeoutSec $HTTP_TIMEOUT_SEC
    }
    catch {
        Write-Error "Could not fetch the $Label manifest: $_"
        Write-Host "  URL:  $manifestUrl" -ForegroundColor $COLOR_YELLOW
        Write-Host "  Fix:  check your network, then retry." -ForegroundColor $COLOR_YELLOW
        return $null
    }

    $version = $manifest.latest_version
    if (-not $version) {
        Write-Error "The hub manifest at $manifestUrl declares no latest_version."
        return $null
    }

    $entry = $manifest.versions.$version
    if (-not $entry) {
        Write-Error "The hub manifest names latest_version $version but publishes no such version."
        Write-Host "  Look: $manifestUrl" -ForegroundColor $COLOR_YELLOW
        return $null
    }

    $artifacts = @($entry.artifacts)
    if (-not $artifacts -or $artifacts.Count -eq 0) {
        $artifacts = if ($entry.artifact) { @($entry.artifact) } else { @() }
    }

    $match = $artifacts | Where-Object { $_.filename -eq $Filename } | Select-Object -First 1
    if (-not $match) {
        $listed = ($artifacts | ForEach-Object { $_.filename } | Sort-Object) -join ", "
        if (-not $listed) { $listed = "none" }
        Write-Error "$Label $version publishes no $Filename (it publishes: $listed)."
        Write-Host "  Look: $manifestUrl" -ForegroundColor $COLOR_YELLOW
        return $null
    }

    if (-not $match.sha256) {
        Write-Error "$Label $version publishes $Filename with no SHA-256 - refusing to install unverified."
        Write-Host "  Look: $manifestUrl" -ForegroundColor $COLOR_YELLOW
        return $null
    }

    return @{
        Version = $version
        Sha256  = $match.sha256
        Url     = "$GAIA_HUB_BASE_URL/agents/$AgentId/$version/$Filename"
    }
}

# Download one hub artifact into $GAIA_BIN, refusing anything whose SHA-256 does
# not match what the manifest published. Shared by the terminal hub and the
# flagship agent so there is exactly one place the verification rule lives.
#
# -Optional turns a missing platform build or an unreachable manifest into a
# warning instead of exiting: the hub is still usable without a given agent.
# A CHECKSUM MISMATCH is never softened that way -- it always exits.
function Install-HubBinary {
    param(
        [Parameter(Mandatory)][string]$AgentId,
        [Parameter(Mandatory)][string]$Filename,
        [Parameter(Mandatory)][string]$DestName,
        [Parameter(Mandatory)][string]$Label,
        [switch]$Optional
    )

    $resolved = Resolve-HubArtifact -AgentId $AgentId -Filename $Filename -Label $Label
    if (-not $resolved) {
        if ($Optional) {
            Write-Warning "Skipping $Label - see above. The terminal hub still works."
            return $false
        }
        exit 1
    }

    $tmpDir = Join-Path $env:TEMP ("gaia-dl-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
    $tmpFile = Join-Path $tmpDir $Filename

    try {
        Write-Step "Downloading $Label $($resolved.Version)"
        try {
            $prevProgress = $ProgressPreference
            $ProgressPreference = "SilentlyContinue"
            try {
                Invoke-WebRequest -Uri $resolved.Url -OutFile $tmpFile -UseBasicParsing -TimeoutSec $HTTP_TIMEOUT_SEC
            }
            finally {
                $ProgressPreference = $prevProgress
            }
        }
        catch {
            Write-Error "Could not download the $Label binary: $_"
            Write-Host "  URL:  $($resolved.Url)" -ForegroundColor $COLOR_YELLOW
            Write-Host "  Fix:  check your network and retry." -ForegroundColor $COLOR_YELLOW
            if ($Optional) { return $false }
            exit 1
        }

        # No checksum, no install.
        $got = (Get-FileHash -Path $tmpFile -Algorithm SHA256).Hash
        if ($got -ne $resolved.Sha256.ToUpper()) {
            Write-Error "Checksum mismatch for $Filename - refusing to install."
            Write-Host "  expected $($resolved.Sha256.ToUpper())" -ForegroundColor $COLOR_YELLOW
            Write-Host "  got      $got" -ForegroundColor $COLOR_YELLOW
            Write-Host "  Fix:  retry; if it persists report it at https://github.com/amd/gaia/issues" -ForegroundColor $COLOR_YELLOW
            exit 1
        }

        if (-not (Test-Path $GAIA_BIN)) {
            New-Item -ItemType Directory -Path $GAIA_BIN -Force | Out-Null
        }
        try {
            Move-Item -Path $tmpFile -Destination "$GAIA_BIN\$DestName" -Force
        }
        catch {
            Write-Error "Downloaded and verified, but could not write $GAIA_BIN\$DestName`: $_"
            Write-Host "  Fix:  close any running GAIA (and any tool scanning that" -ForegroundColor $COLOR_YELLOW
            Write-Host "        folder), then re-run this installer." -ForegroundColor $COLOR_YELLOW
            if ($Optional) { return $false }
            exit 1
        }
        Write-Success "$Label $($resolved.Version) installed to $GAIA_BIN\$DestName"
        return $true
    }
    finally {
        if (Test-Path $tmpDir) { Remove-Item -Path $tmpDir -Recurse -Force }
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

    # Never `gaia.exe`: tui/internal/daemon/client.go resolves `gaia` on PATH to
    # start the Python-owned daemon, so a Go binary by that name finds itself.
    [void](Install-HubBinary -AgentId $TERMINAL_HUB_ID -Filename "gaia-$platform.exe" -DestName "gaia-tui.exe" -Label "terminal hub")
}

# The flagship agent's frozen sidecar. The terminal hub spawns `gaia-agent` as a
# child process, so without this binary the flagship cannot run -- which is what
# this one-liner left behind before: a UI with no agent under it.
#
# Installed BESIDE gaia-tui.exe on purpose. The hub resolves its agent from its
# own directory before consulting PATH (catalog.resolveAgentBinary), so a stale
# gaia-agent elsewhere cannot shadow the one this script just verified.
# Returns $true only when the agent binary is on disk. The caller uses that to
# decide what to promise: the hub spawns gaia-agent as a child process, so
# without it `gaia-tui` opens an agent list rather than a chat. ARM64 Windows
# hits this every time — the sidecar publishes no build for it.
function Install-FlagshipAgent {
    Write-Step "Installing the GAIA flagship agent"

    $platform = Get-TerminalHubPlatform
    if (-not $platform) {
        Write-Warning "No flagship agent build for this architecture - skipping."
        return $false
    }
    # The sidecar is published under win32-x64; the terminal hub uses win-x64.
    $lockPlatform = $platform -replace '^win-', 'win32-'
    $suffix = if ($lockPlatform -like 'win32-*') { ".exe" } else { "" }

    $ok = Install-HubBinary -AgentId $FLAGSHIP_AGENT_ID -Filename "gaia-agent-$lockPlatform$suffix" -DestName "gaia-agent.exe" -Label "GAIA agent" -Optional
    return [bool]$ok
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
    param(
        # False when the flagship sidecar was skipped — no published build for
        # this platform (ARM64 Windows), or its download failed. The banner must
        # not promise an agent that is not on disk.
        [bool]$FlagshipInstalled = $true
    )
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
    if ($FlagshipInstalled) {
        Write-Host "  3. Talk to the agent: " -ForegroundColor White -NoNewline
        Write-Host "gaia-tui" -ForegroundColor $COLOR_GREEN
        Write-Host "     (it opens the GAIA agent; type " -ForegroundColor White -NoNewline
        Write-Host "/hub" -ForegroundColor $COLOR_GREEN -NoNewline
        Write-Host " for the agent hub)" -ForegroundColor White
    } else {
        Write-Host "  3. Open the terminal hub: " -ForegroundColor White -NoNewline
        Write-Host "gaia-tui" -ForegroundColor $COLOR_GREEN
        Write-Host "     The GAIA agent was skipped - see the warning above - so this" -ForegroundColor $COLOR_YELLOW
        Write-Host "     opens the agent list, not a chat. Re-run this installer to retry." -ForegroundColor $COLOR_YELLOW
    }
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

    # After Install-Tui so it lands in the same directory, which is where
    # the hub looks for its agent first.
    $flagshipInstalled = Install-FlagshipAgent

    # Show next steps
    Show-NextSteps -FlagshipInstalled:$flagshipInstalled
}

# Run installer
Main

