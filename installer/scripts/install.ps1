# GAIA Installer for Windows
# One-command installation: irm https://amd-gaia.ai/install.ps1 | iex

# One scope, and throw rather than exit: under iex this runs in the user's own session.
& {

$ErrorActionPreference = "Stop"

# Configuration
$GAIA_HOME = "$env:USERPROFILE\.gaia"
$GAIA_VENV = "$GAIA_HOME\venv"
$GAIA_BIN = "$GAIA_HOME\bin"
$PYTHON_VERSION = "3.12"

# The GAIA Terminal UI binary comes from the GitHub release: publish.yml
# attaches all six builds and gaia-tui-SHA256SUMS.txt to every release.
$GAIA_RELEASES_URL = if ($env:GAIA_RELEASES_URL) { $env:GAIA_RELEASES_URL } else { "https://github.com/amd/gaia/releases" }
$GAIA_RELEASES_URL = $GAIA_RELEASES_URL.TrimEnd('/')
# Empty means the latest stable release; a tag such as v0.25.1-rc2 pins one.
$GAIA_RELEASE_TAG = $env:GAIA_RELEASE_TAG
$TUI_SUMS_FILE = "gaia-tui-SHA256SUMS.txt"

# The flagship agent ships on its own cadence through the Agent Hub, the same
# place `gaia init` and `gaia hub install` take it from.
$GAIA_HUB_BASE_URL = if ($env:GAIA_HUB_BASE_URL) { $env:GAIA_HUB_BASE_URL } else { "https://hub.amd-gaia.ai" }
$GAIA_HUB_BASE_URL = $GAIA_HUB_BASE_URL.TrimEnd('/')
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

function Write-GaiaError {
    param([string]$Message)
    Write-Host "[X] $Message" -ForegroundColor $COLOR_RED
}

function Write-GaiaWarning {
    param([string]$Message)
    Write-Host "[!] $Message" -ForegroundColor $COLOR_YELLOW
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
        Write-GaiaError "Failed to install uv: $_"
        Write-Host "  Fix:  install uv manually from" -ForegroundColor $COLOR_YELLOW
        Write-Host "        https://docs.astral.sh/uv/getting-started/installation/" -ForegroundColor $COLOR_YELLOW
        throw "GAIA install failed - see the error above."
    }

    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-GaiaError "uv installed but is not on PATH."
        Write-Host "  Fix:  close and reopen your terminal, then re-run this installer." -ForegroundColor $COLOR_YELLOW
        throw "GAIA install failed - see the error above."
    }

    Write-Success "uv installed successfully"
}

function Install-Gaia {
    # Check if GAIA is already installed
    $gaiaExe = "$GAIA_VENV\Scripts\gaia.exe"
    if (Test-Path $gaiaExe) {
        Write-GaiaWarning "GAIA is already installed at $GAIA_HOME"
        Write-Step "Checking for updates..."

        # uv, not `python -m pip`: `uv venv` creates the environment without pip,
        # so the pip form fails on every re-run.
        # --upgrade exits 0 when there is nothing to do, so non-zero is a real
        # failure, not "already current".
        & uv pip install --native-tls --compile-bytecode --python "$GAIA_VENV\Scripts\python.exe" --upgrade "amd-gaia[api]" --quiet
        if ($LASTEXITCODE -ne 0) {
            Write-GaiaError "Failed to update the GAIA package in $GAIA_VENV (exit $LASTEXITCODE)."
            Write-Host "  Fix:  re-run this installer, or delete $GAIA_HOME to start clean." -ForegroundColor $COLOR_YELLOW
            throw "GAIA install failed - see the error above."
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
        Write-GaiaWarning "Directory already exists: $GAIA_HOME"
    }

    # Create virtual environment with Python 3.12 (uv will download if needed)
    Write-Step "Creating virtual environment with Python $PYTHON_VERSION..."
    Write-Host "  (uv will automatically download Python $PYTHON_VERSION if not installed)" -ForegroundColor $COLOR_YELLOW
    # A non-zero native exit does not throw, so $LASTEXITCODE is the only signal.
    # --native-tls on every uv call: the Windows certificate store is where IT
    # installs a TLS-inspecting proxy's CA, and uv's bundled roots lack it.
    & uv venv $GAIA_VENV --python $PYTHON_VERSION --native-tls
    if ($LASTEXITCODE -ne 0) {
        Write-GaiaError "Failed to create the virtual environment at $GAIA_VENV (uv exit $LASTEXITCODE)."
        throw "GAIA install failed - see the error above."
    }
    Write-Success "Virtual environment created"

    Write-Step "Installing GAIA package (about a minute)..."

    # Target the venv python rather than running Activate.ps1: that is a script
    # on disk, so a Restricted execution policy blocks it even though the
    # piped-in installer itself is exempt. --compile-bytecode: uv skips it by
    # default, which leaves the first daemon start compiling all of GAIA.
    & uv pip install --native-tls --compile-bytecode --python "$GAIA_VENV\Scripts\python.exe" "amd-gaia[api]" --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-GaiaError "Failed to install the GAIA package (uv exit $LASTEXITCODE)."
        Write-Host "  Fix:  re-run this installer; if it persists report it at" -ForegroundColor $COLOR_YELLOW
        Write-Host "        https://github.com/amd/gaia/issues" -ForegroundColor $COLOR_YELLOW
        throw "GAIA install failed - see the error above."
    }
    Write-Success "GAIA package installed successfully"
}

function Get-HubPlatform {
    # The Agent Hub platform key - what the flagship agent publishes under,
    # not GOARCH ("amd64").
    $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($arch) {
        "AMD64" { return "win-x64" }
        "ARM64" { return "win-arm64" }
        default { return $null }
    }
}

function Get-TuiAssetName {
    # The terminal binary as publish.yml names it on the release:
    # gaia-<GOOS>-<GOARCH>.exe.
    $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($arch) {
        "AMD64" { return "gaia-windows-amd64.exe" }
        "ARM64" { return "gaia-windows-arm64.exe" }
        default { return $null }
    }
}

# The release to install from: GAIA_RELEASE_TAG, else the tag GitHub's
# /releases/latest redirects to. Asked once, so the checksums and the binary
# always come from the same release. Reading the redirect needs no API call,
# so no rate limit applies. Returns $null after saying why.
function Resolve-ReleaseTag {
    $tag = $GAIA_RELEASE_TAG
    if (-not $tag) {
        $latestUrl = "$GAIA_RELEASES_URL/latest"
        try {
            $req = [System.Net.HttpWebRequest]::Create($latestUrl)
            $req.Method = "HEAD"
            $req.AllowAutoRedirect = $false
            $req.Timeout = $HTTP_TIMEOUT_SEC * 1000
            $req.UserAgent = "gaia-installer"
            $resp = $req.GetResponse()
            try { $location = $resp.Headers["Location"] } finally { $resp.Close() }
        }
        catch {
            Write-GaiaError "Could not reach the GAIA releases: $_"
            Write-Host "  URL:  $latestUrl" -ForegroundColor $COLOR_YELLOW
            Write-Host "  Fix:  check your network (github.com must be reachable), then retry." -ForegroundColor $COLOR_YELLOW
            return $null
        }
        if ($location -match '/releases/tag/([^/?#]+)$') { $tag = $Matches[1] }
        if (-not $tag) {
            Write-GaiaError "Could not find the latest GAIA release."
            Write-Host "  URL:  $latestUrl (redirected to '$location')" -ForegroundColor $COLOR_YELLOW
            Write-Host "  Fix:  retry; if it persists report it at https://github.com/amd/gaia/issues" -ForegroundColor $COLOR_YELLOW
            return $null
        }
    }
    if ($tag -notmatch '^v[0-9][A-Za-z0-9.-]*$') {
        Write-GaiaError "'$tag' is not a GAIA release tag (expected e.g. v0.25.0)."
        Write-Host "  Fix:  unset GAIA_RELEASE_TAG to install the latest release, or name one from" -ForegroundColor $COLOR_YELLOW
        Write-Host "        $GAIA_RELEASES_URL" -ForegroundColor $COLOR_YELLOW
        return $null
    }
    return $tag
}

# Resolve the terminal binary's URL and SHA-256 from the release's checksum
# file, the only source for the digest. Returns @{Version; Sha256; Url}, or
# $null after saying why.
function Resolve-TuiAsset {
    param([Parameter(Mandatory)][string]$Filename)

    $tag = Resolve-ReleaseTag
    if (-not $tag) { return $null }

    $releaseUrl = "$GAIA_RELEASES_URL/download/$tag"
    $sumsUrl = "$releaseUrl/$TUI_SUMS_FILE"
    $sumsFile = Join-Path $env:TEMP ("gaia-sums-" + [guid]::NewGuid().ToString("N") + ".txt")
    try {
        try {
            Invoke-WebRequest -Uri $sumsUrl -OutFile $sumsFile -UseBasicParsing -TimeoutSec $HTTP_TIMEOUT_SEC
        }
        catch {
            Write-GaiaError "Could not fetch the GAIA Terminal UI checksums for ${tag}: $_"
            Write-Host "  URL:  $sumsUrl" -ForegroundColor $COLOR_YELLOW
            Write-Host "  Fix:  check your network and retry. A release published in the last" -ForegroundColor $COLOR_YELLOW
            Write-Host "        few minutes may still be uploading its files." -ForegroundColor $COLOR_YELLOW
            return $null
        }
        $sha = $null
        foreach ($line in Get-Content -LiteralPath $sumsFile) {
            $parts = $line.Trim() -split '\s+', 2
            if ($parts.Count -eq 2 -and $parts[1].TrimStart('*') -eq $Filename) { $sha = $parts[0] }
        }
    }
    finally {
        if (Test-Path $sumsFile) { Remove-Item -LiteralPath $sumsFile -Force }
    }

    if ($sha -notmatch '^[0-9a-fA-F]{64}$') {
        Write-GaiaError "$tag publishes no SHA-256 for $Filename - refusing to install unverified."
        Write-Host "  Look: $sumsUrl" -ForegroundColor $COLOR_YELLOW
        Write-Host "  Fix:  report it at https://github.com/amd/gaia/issues" -ForegroundColor $COLOR_YELLOW
        return $null
    }

    return @{
        Version = $tag
        Sha256  = $sha
        Url     = "$releaseUrl/$Filename"
    }
}

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
        Write-GaiaError "Could not fetch the $Label manifest: $_"
        Write-Host "  URL:  $manifestUrl" -ForegroundColor $COLOR_YELLOW
        Write-Host "  Fix:  check your network, then retry." -ForegroundColor $COLOR_YELLOW
        return $null
    }

    $version = $manifest.latest_version
    if (-not $version) {
        Write-GaiaError "The hub manifest at $manifestUrl declares no latest_version."
        return $null
    }

    $entry = $manifest.versions.$version
    if (-not $entry) {
        Write-GaiaError "The hub manifest names latest_version $version but publishes no such version."
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
        Write-GaiaError "$Label $version publishes no $Filename (it publishes: $listed)."
        Write-Host "  Look: $manifestUrl" -ForegroundColor $COLOR_YELLOW
        return $null
    }

    if (-not $match.sha256) {
        Write-GaiaError "$Label $version publishes $Filename with no SHA-256 - refusing to install unverified."
        Write-Host "  Look: $manifestUrl" -ForegroundColor $COLOR_YELLOW
        return $null
    }

    return @{
        Version = $version
        Sha256  = $match.sha256
        Url     = "$GAIA_HUB_BASE_URL/agents/$AgentId/$version/$Filename"
    }
}

# Download one resolved binary into $GAIA_BIN, refusing anything whose SHA-256
# does not match the published digest. Shared by the terminal hub and the
# flagship agent so there is exactly one place the verification rule lives.
#
# -Optional turns a failed resolve or download into a warning instead of a
# failure: the hub is still usable without a given agent. A CHECKSUM MISMATCH
# is never softened that way -- it always fails the install.
function Install-VerifiedBinary {
    param(
        [hashtable]$Resolved,
        [Parameter(Mandatory)][string]$Filename,
        [Parameter(Mandatory)][string]$DestName,
        [Parameter(Mandatory)][string]$Label,
        [switch]$Optional
    )

    if (-not $Resolved) {
        if ($Optional) {
            Write-GaiaWarning "Skipping $Label - see above. The GAIA Terminal UI still works."
            return $false
        }
        throw "GAIA install failed - see the error above."
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
            Write-GaiaError "Could not download the $Label binary: $_"
            Write-Host "  URL:  $($resolved.Url)" -ForegroundColor $COLOR_YELLOW
            Write-Host "  Fix:  check your network and retry." -ForegroundColor $COLOR_YELLOW
            if ($Optional) { return $false }
            throw "GAIA install failed - see the error above."
        }

        # No checksum, no install.
        $got = (Get-FileHash -Path $tmpFile -Algorithm SHA256).Hash
        if ($got -ne $resolved.Sha256.ToUpper()) {
            Write-GaiaError "Checksum mismatch for $Filename - refusing to install."
            Write-Host "  expected $($resolved.Sha256.ToUpper())" -ForegroundColor $COLOR_YELLOW
            Write-Host "  got      $got" -ForegroundColor $COLOR_YELLOW
            Write-Host "  Fix:  retry; if it persists report it at https://github.com/amd/gaia/issues" -ForegroundColor $COLOR_YELLOW
            throw "GAIA install failed - see the error above."
        }

        if (-not (Test-Path $GAIA_BIN)) {
            New-Item -ItemType Directory -Path $GAIA_BIN -Force | Out-Null
        }
        try {
            Move-Item -Path $tmpFile -Destination "$GAIA_BIN\$DestName" -Force
        }
        catch {
            Write-GaiaError "Downloaded and verified, but could not write $GAIA_BIN\$DestName`: $_"
            Write-Host "  Fix:  close any running GAIA (and any tool scanning that" -ForegroundColor $COLOR_YELLOW
            Write-Host "        folder), then re-run this installer." -ForegroundColor $COLOR_YELLOW
            if ($Optional) { return $false }
            throw "GAIA install failed - see the error above."
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
    Write-Step "Installing the GAIA Terminal UI"

    $filename = Get-TuiAssetName
    if (-not $filename) {
        $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
        Write-GaiaError "No GAIA Terminal UI build for processor architecture '$arch'."
        Write-Host "  Published builds: Windows on x64 and ARM64, Linux, and macOS." -ForegroundColor $COLOR_YELLOW
        Write-Host "  See $GAIA_RELEASES_URL/latest" -ForegroundColor $COLOR_YELLOW
        throw "GAIA install failed - see the error above."
    }

    # Never `gaia.exe`: tui/internal/daemon/client.go resolves `gaia` on PATH to
    # start the Python-owned daemon, so a Go binary by that name finds itself.
    $resolved = Resolve-TuiAsset -Filename $filename
    [void](Install-VerifiedBinary -Resolved $resolved -Filename $filename -DestName "gaia-tui.exe" -Label "GAIA Terminal UI")
}

# The flagship agent's frozen sidecar. The terminal hub spawns `gaia-agent` as a
# child process, so without this binary the flagship cannot run -- which is what
# this one-liner left behind before: a UI with no agent under it.
#
# Installed into $GAIA_BIN because that is the directory this installer owns and
# has already put on PATH, so the hub's exec.LookPath finds gaia-agent there.
#
# Returns $true only when the agent binary is on disk. The caller uses that to
# decide what to promise: the hub spawns gaia-agent as a child process, so
# without it `gaia-tui` opens an agent list rather than a chat. ARM64 Windows
# hits this every time — the sidecar publishes no build for it.
function Install-FlagshipAgent {
    Write-Step "Installing the GAIA flagship agent"

    $platform = Get-HubPlatform
    if (-not $platform) {
        Write-GaiaWarning "No flagship agent build for this architecture - skipping."
        return $false
    }
    # The sidecar is published under win32-x64, not win-x64.
    $lockPlatform = $platform -replace '^win-', 'win32-'
    $filename = "gaia-agent-$lockPlatform.exe"

    $resolved = Resolve-HubArtifact -AgentId $FLAGSHIP_AGENT_ID -Filename $filename -Label "GAIA agent"
    $ok = Install-VerifiedBinary -Resolved $resolved -Filename $filename -DestName "gaia-agent.exe" -Label "GAIA agent" -Optional
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
        Write-GaiaWarning "Failed to add GAIA to PATH automatically"
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
    Write-Host "  1. Open a new terminal window, so the gaia command is on your PATH" -ForegroundColor White
    Write-Host "  2. Run: " -ForegroundColor White -NoNewline
    Write-Host "gaia init" -ForegroundColor $COLOR_GREEN -NoNewline
    Write-Host " to set up the local model runtime and download the models" -ForegroundColor White
    Write-Host "     (~6 GB; ~28 GB on PCs whose GPU can run the larger model)" -ForegroundColor White
    if ($FlagshipInstalled) {
        Write-Host "  3. Talk to the agent: " -ForegroundColor White -NoNewline
        Write-Host "gaia-tui" -ForegroundColor $COLOR_GREEN
        Write-Host "     (it opens the GAIA agent; type " -ForegroundColor White -NoNewline
        Write-Host "/agents" -ForegroundColor $COLOR_GREEN -NoNewline
        Write-Host " to list installed agents)" -ForegroundColor White
    } else {
        Write-Host "  3. Open the GAIA Terminal UI: " -ForegroundColor White -NoNewline
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
        Write-GaiaError "This installer is for Windows. Detected: $([System.Environment]::OSVersion.Platform)"
        Write-Host "On Linux and macOS, run instead:" -ForegroundColor $COLOR_YELLOW
        Write-Host "  curl -fsSL https://amd-gaia.ai/install.sh | sh" -ForegroundColor $COLOR_YELLOW
        throw "GAIA install failed - see the error above."
    }

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
}
