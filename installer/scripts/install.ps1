# GAIA Installer for Windows
# One-command installation: irm https://amd-gaia.ai/install.ps1 | iex

# One scope, and throw rather than exit: under iex this runs in the user's own session.
& {

$ErrorActionPreference = "Stop"

# Configuration
$GAIA_HOME = "$env:USERPROFILE\.gaia"
# Where installers before the frozen core built GAIA; only its PATH entry is
# still taken out (Get-UpdatedPath).
$GAIA_VENV = "$GAIA_HOME\venv"
$GAIA_BIN = "$GAIA_HOME\bin"
# GAIA's commands in $GAIA_BIN: copies of the frozen gaia.exe, which share the
# _internal folder beside them and run as gaia-mcp when called by that name.
$GAIA_COMMANDS = @("gaia", "gaia-cli", "gaia-mcp")
# Every Windows PC gets the x64 build; Windows on Arm runs it emulated.
$CORE_ASSET = "gaia-core-win32-x64.tar.gz"

# The GAIA Terminal UI binary comes from the GitHub release: publish.yml
# attaches all six builds and gaia-tui-SHA256SUMS.txt to every release.
$GAIA_RELEASES_URL = if ($env:GAIA_RELEASES_URL) { $env:GAIA_RELEASES_URL } else { "https://github.com/amd/gaia/releases" }
$GAIA_RELEASES_URL = $GAIA_RELEASES_URL.TrimEnd('/')
# Empty means the latest stable release. A tag pins the whole install to that
# release (Resolve-Pin): v0.25.0 or a candidate such as v0.25.1-rc2.
$GAIA_RELEASE_TAG = $env:GAIA_RELEASE_TAG
$TUI_SUMS_FILE = "gaia-tui-SHA256SUMS.txt"
$CORE_SUMS_FILE = "gaia-core-SHA256SUMS.txt"
# The release this run installs from, resolved once. A hashtable so functions
# can set it without reaching the caller's scope under iex.
$RELEASE = @{ Tag = $null }

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

# Download and verify the release's frozen gaia, then put it in $GAIA_BIN, the
# one directory on PATH. Nothing here installs Python or a PyPI package.
function Install-Core {
    Write-Step "Installing GAIA"

    $resolved = Resolve-TuiAsset -Filename $CORE_ASSET -SumsName $CORE_SUMS_FILE
    $stage = Join-Path $env:TEMP ("gaia-core-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    try {
        [void](Install-VerifiedBinary -Resolved $resolved -Filename $CORE_ASSET -DestName $CORE_ASSET -DestDir $stage -Label "GAIA")
        # Windows' own tar: a Git for Windows tar earlier on PATH reads `C:` as a host.
        & "$env:SystemRoot\System32\tar.exe" -xzf "$stage\$CORE_ASSET" -C $stage
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$stage\gaia.exe")) {
            Write-GaiaError "Could not unpack $CORE_ASSET (tar exit $LASTEXITCODE)."
            Write-Host "  Fix:  check that the disk is not full, then retry." -ForegroundColor $COLOR_YELLOW
            throw "GAIA install failed - see the error above."
        }

        # The running daemon reads its files as it runs, so it stops before they change.
        if (Test-Path "$GAIA_BIN\gaia.exe") {
            & "$GAIA_BIN\gaia.exe" daemon stop *> $null
            if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 3) {
                Write-GaiaError "Could not stop the running GAIA daemon (exit $LASTEXITCODE), so its files can't be replaced."
                Write-Host "  Fix:  close the GAIA apps, then re-run this installer." -ForegroundColor $COLOR_YELLOW
                throw "GAIA install failed - see the error above."
            }
        }

        if (-not (Test-Path $GAIA_BIN)) {
            New-Item -ItemType Directory -Path $GAIA_BIN -Force | Out-Null
        }
        # What an earlier upgrade moved aside; one still in use is removed next time.
        Get-ChildItem -LiteralPath $GAIA_BIN -Filter "*.exe*.old" -File |
            Remove-Item -Force -ErrorAction SilentlyContinue
        Get-ChildItem -LiteralPath $GAIA_BIN -Filter "_internal.*.old" -Directory |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        # The old install is moved aside, never deleted, so a failed swap can put it back.
        $oldInternal = $null
        $asides = @()
        try {
            if (Test-Path "$GAIA_BIN\_internal") {
                $oldInternal = "$GAIA_BIN\_internal.$PID.old"
                Move-Item -LiteralPath "$GAIA_BIN\_internal" -Destination $oldInternal
            }
            Move-Item -LiteralPath "$stage\_internal" -Destination "$GAIA_BIN\_internal"
            foreach ($name in $GAIA_COMMANDS) {
                $dest = "$GAIA_BIN\$name.exe"
                # A running gaia.exe can't be overwritten but can be renamed.
                if (Test-Path -LiteralPath $dest) {
                    $aside = "$dest.old"
                    if (Test-Path -LiteralPath $aside) { $aside = "$dest.$PID.old" }
                    Move-Item -LiteralPath $dest -Destination $aside -Force
                    $asides += ,@($dest, $aside)
                }
                Copy-Item -LiteralPath "$stage\gaia.exe" -Destination $dest -Force
            }
        }
        catch {
            $swapError = $_
            try {
                foreach ($pair in $asides) {
                    if (Test-Path -LiteralPath $pair[0]) { Remove-Item -LiteralPath $pair[0] -Force }
                    Move-Item -LiteralPath $pair[1] -Destination $pair[0]
                }
                if ($oldInternal) {
                    if (Test-Path "$GAIA_BIN\_internal") { Remove-Item -LiteralPath "$GAIA_BIN\_internal" -Recurse -Force }
                    Move-Item -LiteralPath $oldInternal -Destination "$GAIA_BIN\_internal"
                }
            }
            catch {
                Write-GaiaError "Could not restore the previous GAIA in $GAIA_BIN`: $_"
            }
            Write-GaiaError "Could not put GAIA in $GAIA_BIN`: $swapError"
            Write-Host "  Fix:  close any running GAIA (and any tool scanning that" -ForegroundColor $COLOR_YELLOW
            Write-Host "        folder), then re-run this installer." -ForegroundColor $COLOR_YELLOW
            throw "GAIA install failed - see the error above."
        }
        if ($oldInternal) {
            Remove-Item -LiteralPath $oldInternal -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    finally {
        if (Test-Path $stage) { Remove-Item -Path $stage -Recurse -Force }
    }
    Write-Success "GAIA $($resolved.Version) installed to $GAIA_BIN ($($GAIA_COMMANDS -join ', '))"
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
        if ($location -match '/releases/tag/([^/?#]+)\z') { $tag = $Matches[1] }
        if (-not $tag) {
            Write-GaiaError "Could not find the latest GAIA release."
            Write-Host "  URL:  $latestUrl (redirected to '$location')" -ForegroundColor $COLOR_YELLOW
            Write-Host "  Fix:  retry; if it persists report it at https://github.com/amd/gaia/issues" -ForegroundColor $COLOR_YELLOW
            return $null
        }
    }
    if ($tag -cnotmatch '^v[0-9][A-Za-z0-9.-]*\z') {
        Write-GaiaError "'$tag' is not a GAIA release tag (expected e.g. v0.25.0)."
        Write-Host "  Fix:  unset GAIA_RELEASE_TAG to install the latest release, or name one from" -ForegroundColor $COLOR_YELLOW
        Write-Host "        $GAIA_RELEASES_URL" -ForegroundColor $COLOR_YELLOW
        return $null
    }
    return $tag
}

# Turn GAIA_RELEASE_TAG into one coherent release: gaia, the terminal binary
# and the agent all from it. Throws on anything but vX.Y.Z or vX.Y.Z-rcN,
# before anything installs.
#   (unset)       latest everything
#   v0.25.0       that release; the agent from the Agent Hub, as that release's
#                 `gaia init` takes it
#   v0.25.1-rc2   that pre-release; the agent frozen on it, as an RC core takes
#                 it (gaia-agent-release.json)
function Resolve-Pin {
    $tag = $GAIA_RELEASE_TAG
    if (-not $tag) { return @{ Kind = ""; Tag = "" } }
    if ($tag -cmatch '^v([0-9]+\.[0-9]+\.[0-9]+)\z') {
        $pin = @{ Kind = "final"; Tag = $tag }
    }
    elseif ($tag -cmatch '^v([0-9]+\.[0-9]+\.[0-9]+)-rc([1-9][0-9]*)\z') {
        $pin = @{ Kind = "rc"; Tag = $tag }
    }
    else {
        Write-GaiaError "GAIA_RELEASE_TAG='$tag' is not a GAIA release tag."
        Write-Host "  Expected vX.Y.Z (e.g. v0.25.0) or a candidate vX.Y.Z-rcN (e.g. v0.25.1-rc2)." -ForegroundColor $COLOR_YELLOW
        Write-Host "  Fix:  unset it to install the latest release, or name one from" -ForegroundColor $COLOR_YELLOW
        Write-Host "        $GAIA_RELEASES_URL" -ForegroundColor $COLOR_YELLOW
        throw "GAIA install failed - see the error above."
    }
    Write-Step "Installing GAIA release $tag"
    return $pin
}

# Resolve one platform's agent from a release candidate's gaia-agent-release.json
# (util/flagship_rc_assets.py writes it), the file an RC core installs its
# flagship from. Returns @{Version; Sha256; Url}, or $null after saying why.
function Resolve-ReleaseAgent {
    param(
        [Parameter(Mandatory)][string]$Tag,
        [Parameter(Mandatory)][string]$Platform,
        [Parameter(Mandatory)][string]$Filename
    )
    $baseUrl = "$GAIA_RELEASES_URL/download/$Tag"
    $manifestUrl = "$baseUrl/gaia-agent-release.json"
    try {
        $manifest = Invoke-RestMethod -Uri $manifestUrl -UseBasicParsing -TimeoutSec $HTTP_TIMEOUT_SEC
    }
    catch {
        Write-GaiaError "Could not fetch the GAIA agent manifest for ${Tag}: $_"
        Write-Host "  URL:  $manifestUrl" -ForegroundColor $COLOR_YELLOW
        return $null
    }
    if ($manifest -is [string]) { $manifest = $manifest | ConvertFrom-Json }
    $entry = $manifest.platforms.$Platform
    if (-not $manifest.agentVersion -or -not $entry) {
        Write-GaiaError "$Tag carries no GAIA agent for $Platform."
        Write-Host "  Look: $manifestUrl" -ForegroundColor $COLOR_YELLOW
        return $null
    }
    if ($entry.filename -cne $Filename -or $entry.sha256 -cnotmatch '^[0-9a-f]{64}\z') {
        Write-GaiaError "$Tag lists '$($entry.filename)' for $Platform with no valid SHA-256 (expected $Filename) - refusing to install unverified."
        Write-Host "  Look: $manifestUrl" -ForegroundColor $COLOR_YELLOW
        return $null
    }
    return @{
        Version = $manifest.agentVersion
        Sha256  = $entry.sha256
        Url     = "$baseUrl/$Filename"
    }
}

# Resolve a release file's URL and SHA-256 from the release's checksum file,
# the only source for the digest: the terminal binary, or gaia itself. Returns
# @{Version; Sha256; Url}, or $null after saying why.
function Resolve-TuiAsset {
    param(
        [Parameter(Mandatory)][string]$Filename,
        [string]$SumsName = $TUI_SUMS_FILE
    )

    # Asked once, so gaia, the terminal binary and their checksums all come
    # from the same release even if a new one is published mid-install.
    if (-not $RELEASE.Tag) { $RELEASE.Tag = Resolve-ReleaseTag }
    $tag = $RELEASE.Tag
    if (-not $tag) { return $null }

    $releaseUrl = "$GAIA_RELEASES_URL/download/$tag"
    $sumsUrl = "$releaseUrl/$SumsName"
    $sumsFile = Join-Path $env:TEMP ("gaia-sums-" + [guid]::NewGuid().ToString("N") + ".txt")
    try {
        try {
            Invoke-WebRequest -Uri $sumsUrl -OutFile $sumsFile -UseBasicParsing -TimeoutSec $HTTP_TIMEOUT_SEC
        }
        catch {
            Write-GaiaError "Could not fetch $SumsName for ${tag}: $_"
            Write-Host "  URL:  $sumsUrl" -ForegroundColor $COLOR_YELLOW
            Write-Host "  Fix:  check your network and retry. A release published in the last" -ForegroundColor $COLOR_YELLOW
            Write-Host "        few minutes may still be uploading its files." -ForegroundColor $COLOR_YELLOW
            return $null
        }
        # Same rule as install.sh: exactly one line for the file, lowercase hex.
        $found = @()
        foreach ($line in Get-Content -LiteralPath $sumsFile) {
            $parts = $line.Trim() -split '\s+', 2
            if ($parts.Count -eq 2 -and $parts[1].TrimStart('*') -ceq $Filename) { $found += $parts[0] }
        }
        $sha = if ($found.Count -eq 1) { $found[0] } else { $null }
    }
    finally {
        if (Test-Path $sumsFile) { Remove-Item -LiteralPath $sumsFile -Force }
    }

    if ($sha -cnotmatch '^[0-9a-f]{64}\z') {
        Write-GaiaError "$tag publishes no single valid SHA-256 for $Filename - refusing to install unverified."
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
        [string]$DestDir = $GAIA_BIN,
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

        if (-not (Test-Path $DestDir)) {
            New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
        }
        try {
            Move-Item -Path $tmpFile -Destination "$DestDir\$DestName" -Force
        }
        catch {
            Write-GaiaError "Downloaded and verified, but could not write $DestDir\$DestName`: $_"
            Write-Host "  Fix:  close any running GAIA (and any tool scanning that" -ForegroundColor $COLOR_YELLOW
            Write-Host "        folder), then re-run this installer." -ForegroundColor $COLOR_YELLOW
            if ($Optional) { return $false }
            throw "GAIA install failed - see the error above."
        }
        Write-Success "$Label $($resolved.Version) downloaded and verified"
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
    param([hashtable]$Pin = @{ Kind = "" })
    Write-Step "Installing the GAIA flagship agent"

    $platform = Get-HubPlatform
    if (-not $platform) {
        Write-GaiaWarning "No flagship agent build for this architecture - skipping."
        return $false
    }
    # The sidecar is published under win32-x64, not win-x64.
    $lockPlatform = $platform -replace '^win-', 'win32-'
    $filename = "gaia-agent-$lockPlatform.exe"

    # A pinned candidate carries its own frozen agent; everything else takes
    # the Agent Hub's, the same source as `gaia init`.
    if ($Pin.Kind -eq "rc") {
        $resolved = Resolve-ReleaseAgent -Tag $Pin.Tag -Platform $lockPlatform -Filename $filename
    }
    else {
        $resolved = Resolve-HubArtifact -AgentId $FLAGSHIP_AGENT_ID -Filename $filename -Label "GAIA agent"
    }
    $ok = Install-VerifiedBinary -Resolved $resolved -Filename $filename -DestName "gaia-agent.exe" -Label "GAIA agent" -Optional
    if (-not $ok) { return $false }
    return [bool](Write-AgentInstallRecord -Resolved $resolved)
}

# Record the verified agent's version and SHA-256 beside it. GAIA's background
# service starts this copy only when it still matches this record.
function Write-AgentInstallRecord {
    param([Parameter(Mandatory)][hashtable]$Resolved)

    $record = "$GAIA_BIN\gaia-agent.install.json"
    $json = [ordered]@{
        id      = $FLAGSHIP_AGENT_ID
        version = [string]$Resolved.Version
        sha256  = $Resolved.Sha256.ToLower()
    } | ConvertTo-Json
    try {
        # No BOM: Windows PowerShell's UTF8 encoding writes one.
        [IO.File]::WriteAllText($record, $json, (New-Object System.Text.UTF8Encoding $false))
    }
    catch {
        Write-GaiaError "Installed the GAIA agent, but could not write its install record $record`: $_"
        Write-Host "  Fix:  check you can write to $GAIA_BIN, then re-run this installer." -ForegroundColor $COLOR_YELLOW
        return $false
    }
    return $true
}

# The user environment key Add-ToPath edits. Tests point it at a scratch key.
$USER_ENV_KEY = "Environment"

function Get-NormalizedPathEntry {
    param([string]$Entry)
    return [Environment]::ExpandEnvironmentVariables($Entry.Trim().Trim('"')).TrimEnd('\', '/')
}

function Get-RawUserPath {
    # Unexpanded: writing back an expanded %USERPROFILE% would freeze it to this machine.
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($USER_ENV_KEY, $false)
    if ($null -eq $key) { return "" }
    try {
        return [string]$key.GetValue(
            "Path", "",
            [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    }
    finally {
        $key.Close()
    }
}

function Set-RawUserPath {
    param([string]$Value)
    $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($USER_ENV_KEY)
    try {
        # REG_EXPAND_SZ, as Windows creates it: REG_SZ leaves %VARS% unexpanded.
        $key.SetValue("Path", $Value, [Microsoft.Win32.RegistryValueKind]::ExpandString)
    }
    finally {
        $key.Close()
    }
}

function Send-EnvironmentChange {
    # A raw registry write, unlike SetEnvironmentVariable, tells no one; new
    # terminals launched from Explorer would keep the old PATH until sign-out.
    if (-not ("GaiaInstaller.EnvBroadcast" -as [type])) {
        Add-Type -Namespace GaiaInstaller -Name EnvBroadcast -MemberDefinition @'
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam,
    string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@
    }
    $result = [UIntPtr]::Zero
    # HWND_BROADCAST, WM_SETTINGCHANGE, SMTO_ABORTIFHUNG, 5 s.
    $null = [GaiaInstaller.EnvBroadcast]::SendMessageTimeout(
        [IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, "Environment", 2, 5000, [ref]$result)
}

# $Current with $GAIA_BIN added once and the venv's Scripts folder, which earlier
# installers added, taken out. Every other entry keeps its text and its place.
function Get-UpdatedPath {
    param([string]$Current)
    $legacy = Get-NormalizedPathEntry "$GAIA_VENV\Scripts"
    $wanted = Get-NormalizedPathEntry $GAIA_BIN
    # Split/filter first: an empty element (from a trailing ";") is resolved
    # as the current directory by every process that reads this PATH.
    $existing = @($Current -split ";" | Where-Object { $_.Trim() })
    $kept = @($existing | Where-Object { (Get-NormalizedPathEntry $_) -ne $legacy })
    $added = @()
    if (@($kept | ForEach-Object { Get-NormalizedPathEntry $_ }) -notcontains $wanted) {
        $added = @($GAIA_BIN)
    }
    return [pscustomobject]@{
        Value   = (($kept + $added) -join ";")
        Added   = ($added.Count -gt 0)
        Removed = ($existing.Count - $kept.Count)
    }
}

function Add-ToPath {
    Write-Step "Adding GAIA to PATH..."

    try {
        $update = Get-UpdatedPath -Current (Get-RawUserPath)
        if (-not $update.Added -and $update.Removed -eq 0) {
            Write-Success "GAIA is already in PATH"
            return
        }
        Set-RawUserPath -Value $update.Value
    }
    catch {
        Write-GaiaWarning "Failed to add GAIA to PATH automatically: $_"
        Write-Host "Add this directory to your user PATH by hand:" -ForegroundColor $COLOR_YELLOW
        Write-Host "  $GAIA_BIN" -ForegroundColor $COLOR_YELLOW
        Write-Host "Do not add $GAIA_VENV\Scripts: its python.exe would replace yours." -ForegroundColor $COLOR_YELLOW
        Write-Host "If an earlier GAIA install added it, remove it from your PATH." -ForegroundColor $COLOR_YELLOW
        return
    }

    $env:PATH = (Get-UpdatedPath -Current $env:PATH).Value
    if ($update.Removed -gt 0) {
        Write-Success "Removed $GAIA_VENV\Scripts from PATH, so your own python and pip come first again"
    }
    if ($update.Added) {
        Write-Success "Added GAIA to PATH"
    }

    # Separate: the PATH is already saved, so a failure here must not say otherwise.
    try {
        Send-EnvironmentChange
    }
    catch {
        Write-GaiaWarning "PATH saved, but Windows could not be notified: $_"
        Write-Host "  Sign out and back in before opening a new terminal." -ForegroundColor $COLOR_YELLOW
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
    if ($FlagshipInstalled) {
        Write-Host "  2. Open GAIA: " -ForegroundColor White -NoNewline
        Write-Host "gaia-tui" -ForegroundColor $COLOR_GREEN
        Write-Host "     On first launch it sets up the local model server and lets you" -ForegroundColor White
        Write-Host "     choose which models to download before anything downloads." -ForegroundColor White
        Write-Host "     (type " -ForegroundColor White -NoNewline
        Write-Host "/agents" -ForegroundColor $COLOR_GREEN -NoNewline
        Write-Host " there to list installed agents)" -ForegroundColor White
        Write-Host "  For a scripted or headless install, run " -ForegroundColor White -NoNewline
        Write-Host "gaia init --yes" -ForegroundColor $COLOR_GREEN -NoNewline
        Write-Host " instead." -ForegroundColor White
    } else {
        Write-Host "  2. Run: " -ForegroundColor White -NoNewline
        Write-Host "gaia init" -ForegroundColor $COLOR_GREEN -NoNewline
        Write-Host " to set up the local model runtime and download the models" -ForegroundColor White
        Write-Host "     (~6 GB; ~28 GB on PCs whose GPU can run the larger model)" -ForegroundColor White
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

    # Before anything installs: a bad tag must not leave a half-pinned machine.
    $pin = Resolve-Pin

    Install-Core

    # Before the terminal hub, so a hard failure there still leaves the gaia
    # command reachable.
    Add-ToPath

    # Install the terminal hub binary
    Install-Tui

    # After Install-Tui so it lands in the same directory, which is where
    # the hub looks for its agent first.
    $flagshipInstalled = Install-FlagshipAgent -Pin $pin

    # Show next steps
    Show-NextSteps -FlagshipInstalled:$flagshipInstalled
}

# Run installer
Main
}
