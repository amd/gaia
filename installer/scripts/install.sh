#!/bin/sh
# GAIA Installer for Linux and macOS
# One-command installation: curl -fsSL https://amd-gaia.ai/install.sh | sh
#
# POSIX sh only: the documented one-liner pipes this into `sh`, which is dash on
# Debian/Ubuntu. Bash-only syntax here means nothing installs at all there.

set -eu

# Configuration
GAIA_HOME="$HOME/.gaia"
GAIA_VENV="$GAIA_HOME/venv"
GAIA_BIN="$GAIA_HOME/bin"
PYTHON_VERSION="3.12"

# The GAIA Terminal UI binary comes from the GitHub release: publish.yml
# attaches all six builds and gaia-tui-SHA256SUMS.txt to every release.
GAIA_RELEASES_URL="${GAIA_RELEASES_URL:-https://github.com/amd/gaia/releases}"
GAIA_RELEASES_URL="${GAIA_RELEASES_URL%/}"
# Empty means the latest stable release. A tag pins the whole install to that
# release (resolve_pin): v0.25.0 or a candidate such as v0.25.1-rc2.
GAIA_RELEASE_TAG="${GAIA_RELEASE_TAG:-}"
# Set by resolve_pin: the core package spec, and "", "final" or "rc".
GAIA_PACKAGE_SPEC="amd-gaia[api]"
PIN_KIND=""
TUI_SUMS_FILE="gaia-tui-SHA256SUMS.txt"

# The flagship agent ships on its own cadence through the Agent Hub, the same
# place `gaia init` and `gaia hub install` take it from.
GAIA_HUB_BASE_URL="${GAIA_HUB_BASE_URL:-https://hub.amd-gaia.ai}"
GAIA_HUB_BASE_URL="${GAIA_HUB_BASE_URL%/}"
FLAGSHIP_AGENT_ID="gaia"

# Network limits: a black-holed connection must fail, not hang silently.
CONNECT_TIMEOUT=15
MAX_TIME=900

# Colors
if [ -t 1 ]; then
    COLOR_GREEN=$(printf '\033[0;32m')
    COLOR_YELLOW=$(printf '\033[1;33m')
    COLOR_RED=$(printf '\033[0;31m')
    COLOR_CYAN=$(printf '\033[0;36m')
    COLOR_RESET=$(printf '\033[0m')
else
    COLOR_GREEN=""
    COLOR_YELLOW=""
    COLOR_RED=""
    COLOR_CYAN=""
    COLOR_RESET=""
fi

# One scratch dir and one EXIT trap: a second `trap … EXIT` replaces the first,
# silently leaking whatever the first was meant to remove.
GAIA_TMP=""
cleanup() {
    if [ -n "$GAIA_TMP" ]; then
        rm -rf "$GAIA_TMP"
    fi
}
trap cleanup EXIT

# Sets $SCRATCH. Not a command substitution — that subshell would lose the
# $GAIA_TMP assignment and the trap would have nothing to remove.
scratch_dir() {
    if [ -z "$GAIA_TMP" ]; then
        GAIA_TMP="$(mktemp -d)"
    fi
    SCRATCH="$GAIA_TMP/$1"
    mkdir -p "$SCRATCH"
}

# Output functions
print_step() {
    printf '%s[*]%s %s\n' "$COLOR_CYAN" "$COLOR_RESET" "$1"
}

print_success() {
    printf '%s[OK]%s %s\n' "$COLOR_GREEN" "$COLOR_RESET" "$1"
}

print_error() {
    printf '%s[X]%s %s\n' "$COLOR_RED" "$COLOR_RESET" "$1"
}

print_warning() {
    printf '%s[!]%s %s\n' "$COLOR_YELLOW" "$COLOR_RESET" "$1"
}

# Detect environment
detect_environment() {
    print_step "Detecting environment..."

    OS_NAME=$(uname -s)
    case "$OS_NAME" in
        Linux)  OS_LABEL="Linux" ;;
        Darwin) OS_LABEL="macOS" ;;
        MINGW*|MSYS*|CYGWIN*)
            print_error "This installer is for Linux and macOS. Detected: $OS_NAME"
            echo "On Windows, run instead:"
            echo "  irm https://amd-gaia.ai/install.ps1 | iex"
            exit 1
            ;;
        *)
            print_error "Unsupported operating system: $OS_NAME"
            echo "GAIA supports Linux, macOS, and Windows."
            echo "Report an unexpected result at https://github.com/amd/gaia/issues"
            exit 1
            ;;
    esac

    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64|amd64|arm64|aarch64) ;;
        *)
            print_error "Unsupported architecture: $ARCH"
            echo "GAIA ships x86_64 and arm64 builds only."
            echo "Report an unexpected result at https://github.com/amd/gaia/issues"
            exit 1
            ;;
    esac

    print_success "Environment: $OS_LABEL ($ARCH)"
}

# Check for curl or wget
check_download_tool() {
    if command -v curl > /dev/null 2>&1; then
        DOWNLOAD_CMD="curl"
        print_success "curl is available"
    elif command -v wget > /dev/null 2>&1; then
        DOWNLOAD_CMD="wget"
        print_success "wget is available"
    else
        print_error "Neither curl nor wget is installed"
        echo ""
        echo "Please install curl or wget:"
        echo "  Ubuntu/Debian: sudo apt install curl"
        echo "  Fedora: sudo dnf install curl"
        echo "  macOS: curl ships with the OS; check your PATH"
        exit 1
    fi
}

# Non-zero on any HTTP error, so a 404 is never mistaken for content.
fetch_file() {
    if [ "${DOWNLOAD_CMD:-curl}" = "wget" ]; then
        wget -q --timeout="$CONNECT_TIMEOUT" -O "$2" "$1"
    else
        curl -fsSL --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" "$1" -o "$2"
    fi
}

# Warn about elevation before anything prompts for it.
announce_elevation() {
    echo ""
    print_warning "One step later on needs your password:"
    echo "  'gaia init' installs Lemonade Server (the local model runtime), which"
    if [ "${OS_NAME:-}" = "Darwin" ]; then
        echo "  installs a .pkg via sudo. This installer itself never asks for sudo."
    else
        echo "  installs a .deb via sudo. This installer itself never asks for sudo."
    fi
    echo ""
}

# Install uv package manager
install_uv() {
    print_step "Checking for uv package manager..."

    if command -v uv > /dev/null 2>&1; then
        print_success "uv is already installed"
        return 0
    fi

    print_step "Installing uv package manager..."

    # Staged, not piped: POSIX sh has no pipefail, so `fetch | sh` would report
    # the shell's status and execute a truncated download.
    scratch_dir uv
    uv_tmp="$SCRATCH"

    if ! fetch_file https://astral.sh/uv/install.sh "$uv_tmp/uv-install.sh"; then
        print_error "Could not download the uv installer."
        echo "  URL:  https://astral.sh/uv/install.sh"
        echo "  Fix:  check your network, or install uv manually from"
        echo "        https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi

    if ! sh "$uv_tmp/uv-install.sh"; then
        print_error "The uv installer failed."
        echo "  Fix:  install uv manually from"
        echo "        https://docs.astral.sh/uv/getting-started/installation/"
        echo "        then re-run this installer."
        exit 1
    fi

    # uv installs to ~/.local/bin (older releases used ~/.cargo/bin).
    PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    export PATH

    if ! command -v uv > /dev/null 2>&1; then
        print_error "uv installed but is not on PATH"
        echo "Looked in \$HOME/.local/bin and \$HOME/.cargo/bin."
        echo "Add uv's install directory to PATH and re-run this installer."
        exit 1
    fi

    print_success "uv installed successfully"
}

# Create virtual environment and install GAIA
install_gaia() {
    # Check if GAIA is already installed
    if [ -f "$GAIA_VENV/bin/gaia" ]; then
        print_warning "GAIA is already installed at $GAIA_HOME"
        print_step "Checking for updates..."

        # --upgrade exits 0 when there is nothing to do, so non-zero is a real
        # failure, not "already current".
        if ! uv pip install --compile-bytecode --python "$GAIA_VENV/bin/python" --upgrade "$GAIA_PACKAGE_SPEC" --quiet; then
            print_error "Failed to update the GAIA package in $GAIA_VENV."
            echo "  Fix:  re-run this installer, or delete $GAIA_HOME to start clean."
            exit 1
        fi
        print_success "GAIA is up to date"
        return 0
    fi

    print_step "Creating GAIA environment at $GAIA_HOME..."

    # Create GAIA home directory
    if [ ! -d "$GAIA_HOME" ]; then
        mkdir -p "$GAIA_HOME"
        print_success "Created directory: $GAIA_HOME"
    else
        print_warning "Directory already exists: $GAIA_HOME"
    fi

    # Create virtual environment with Python 3.12 (uv will download if needed)
    print_step "Creating virtual environment with Python $PYTHON_VERSION..."
    print_warning "  (uv will automatically download Python $PYTHON_VERSION if not installed)"
    if ! uv venv "$GAIA_VENV" --python "$PYTHON_VERSION"; then
        print_error "Failed to create virtual environment"
        exit 1
    fi
    print_success "Virtual environment created"

    print_step "Installing GAIA package..."

    # Target the venv python rather than sourcing bin/activate: that script is
    # not `set -u` clean (it reads $OSTYPE, unset in dash) and noisily half-fails
    # under the documented `curl … | sh`. --compile-bytecode: uv skips it by
    # default, which leaves the first daemon start compiling all of GAIA.
    if ! uv pip install --compile-bytecode --python "$GAIA_VENV/bin/python" "$GAIA_PACKAGE_SPEC"; then
        print_error "Failed to install GAIA package"
        exit 1
    fi

    print_success "GAIA package installed successfully"
}

# ---------------------------------------------------------------------------
# Terminal hub (Go binary)
# ---------------------------------------------------------------------------

# Print the Agent Hub platform key for this machine, e.g. "linux-x64" -- the
# key the flagship agent publishes under, not GOARCH ("amd64").
hub_platform() {
    _os=""
    _arch=""
    case "$(uname -s)" in
        Linux)  _os="linux" ;;
        Darwin) _os="darwin" ;;
    esac
    case "$(uname -m)" in
        x86_64|amd64)  _arch="x64" ;;
        arm64|aarch64) _arch="arm64" ;;
    esac
    if [ -z "$_os" ] || [ -z "$_arch" ]; then
        return 1
    fi
    printf '%s-%s\n' "$_os" "$_arch"
}

# Print this machine's terminal binary as publish.yml names it on the release:
# gaia-<GOOS>-<GOARCH>, e.g. "gaia-linux-amd64".
tui_asset_name() {
    _os=""
    _arch=""
    case "$(uname -s)" in
        Linux)  _os="linux" ;;
        Darwin) _os="darwin" ;;
    esac
    case "$(uname -m)" in
        x86_64|amd64)  _arch="amd64" ;;
        arm64|aarch64) _arch="arm64" ;;
    esac
    if [ -z "$_os" ] || [ -z "$_arch" ]; then
        return 1
    fi
    printf 'gaia-%s-%s\n' "$_os" "$_arch"
}

# Turn GAIA_RELEASE_TAG into one coherent release: the core package, the
# terminal binary and the agent all from it. Sets GAIA_PACKAGE_SPEC and
# PIN_KIND; refuses anything but vX.Y.Z or vX.Y.Z-rcN before anything installs.
#   (unset)       latest everything
#   v0.25.0       amd-gaia==0.25.0; the agent from the Agent Hub, as that
#                 release's `gaia init` takes it
#   v0.25.1-rc2   amd-gaia==0.25.1rc2; the agent frozen on that pre-release,
#                 as an RC core takes it (gaia-agent-release.json)
resolve_pin() {
    GAIA_PACKAGE_SPEC="amd-gaia[api]"
    PIN_KIND=""
    _tag="$GAIA_RELEASE_TAG"
    [ -n "$_tag" ] || return 0
    _v="${_tag#v}"
    # Character check first: grep matches line by line, so a newline in the
    # value would otherwise let a valid first line through.
    case "$_tag" in
        *[!A-Za-z0-9.-]*) _v="" ;;
        v*) ;;
        *) _v="" ;;
    esac
    if [ -n "$_v" ] && printf '%s\n' "$_v" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
        PIN_KIND="final"
        GAIA_PACKAGE_SPEC="amd-gaia[api]==$_v"
    elif [ -n "$_v" ] && printf '%s\n' "$_v" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+-rc[1-9][0-9]*$'; then
        PIN_KIND="rc"
        GAIA_PACKAGE_SPEC="amd-gaia[api]==${_v%-rc*}rc${_v##*-rc}"
    else
        print_error "GAIA_RELEASE_TAG='$_tag' is not a GAIA release tag."
        echo "  Expected vX.Y.Z (e.g. v0.25.0) or a candidate vX.Y.Z-rcN (e.g. v0.25.1-rc2)."
        echo "  Fix:  unset it to install the latest release, or name one from"
        echo "        $GAIA_RELEASES_URL"
        exit 1
    fi
    print_step "Installing GAIA release $_tag ($GAIA_PACKAGE_SPEC)"
    return 0
}

# Sets RELEASE_TAG. Asked once, so the checksums and the binary always come
# from the same release even if a new one is published mid-install.
resolve_release_tag() {
    RELEASE_TAG="$GAIA_RELEASE_TAG"
    if [ -z "$RELEASE_TAG" ]; then
        # /releases/latest redirects to /releases/tag/<newest stable>; reading
        # the redirect needs no API call, so no rate limit applies.
        _loc=""
        if [ "${DOWNLOAD_CMD:-curl}" = "wget" ]; then
            _loc="$(wget --max-redirect=0 -S -O /dev/null --timeout="$CONNECT_TIMEOUT" \
                "$GAIA_RELEASES_URL/latest" 2>&1 \
                | sed -n 's/^ *[Ll]ocation: *\([^ ]*\).*/\1/p' | tail -n 1 | tr -d '\r')"
        else
            _loc="$(curl -fsS -o /dev/null --connect-timeout "$CONNECT_TIMEOUT" --max-time 60 \
                -w '%{redirect_url}' "$GAIA_RELEASES_URL/latest")" || _loc=""
        fi
        case "$_loc" in
            */releases/tag/*) RELEASE_TAG="${_loc##*/releases/tag/}" ;;
        esac
    fi
    case "$RELEASE_TAG" in
        v[0-9]*) ;;
        *) return 1 ;;
    esac
    case "$RELEASE_TAG" in
        *[!A-Za-z0-9.-]*) return 1 ;;
    esac
    return 0
}

# Resolve the Python interpreter used to read the hub manifest. install_gaia has
# already created the venv by the time this runs.
hub_python() {
    if [ -x "$GAIA_VENV/bin/python" ]; then
        printf '%s\n' "$GAIA_VENV/bin/python"
    elif command -v python3 > /dev/null 2>&1; then
        command -v python3
    else
        return 1
    fi
}

# Read one artifact's version and SHA-256 out of a hub manifest. The manifest is
# the only source for the version, the per-platform filename, and the
# Worker-computed digest, so both installers read it the same way.
#
# Args: python, manifest path, filename. Echoes "<version> <sha256>".
read_hub_manifest() {
    "$1" - "$2" "$3" <<'MANIFEST_PY'
import json
import sys

manifest_path, filename = sys.argv[1], sys.argv[2]
try:
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
except (OSError, ValueError) as exc:
    sys.exit("the hub manifest is not readable JSON: %s" % exc)

version = manifest.get("latest_version")
if not version:
    sys.exit("the hub manifest declares no latest_version")

entry = (manifest.get("versions") or {}).get(version)
if not isinstance(entry, dict):
    sys.exit("the hub manifest names latest_version %s but publishes no such version" % version)

artifacts = entry.get("artifacts")
if not artifacts:
    primary = entry.get("artifact")
    artifacts = [primary] if primary else []

match = next((a for a in artifacts if a.get("filename") == filename), None)
if match is None:
    listed = ", ".join(sorted(a.get("filename", "?") for a in artifacts)) or "none"
    sys.exit("version %s publishes no %s (it publishes: %s)" % (version, filename, listed))

digest = match.get("sha256")
if not digest:
    sys.exit("version %s publishes %s with no SHA-256" % (version, filename))

print("%s %s" % (version, digest))
MANIFEST_PY
}

# Read one platform's agent version and SHA-256 out of a release candidate's
# gaia-agent-release.json (util/flagship_rc_assets.py writes it), the file an RC
# core installs its flagship from.
#
# Args: python, manifest path, platform key, filename. Echoes "<version> <sha256>".
read_release_manifest() {
    "$1" - "$2" "$3" "$4" <<'RELEASE_PY'
import json
import re
import sys

manifest_path, platform, filename = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
except (OSError, ValueError) as exc:
    sys.exit("gaia-agent-release.json is not readable JSON: %s" % exc)

version = manifest.get("agentVersion")
if not version:
    sys.exit("gaia-agent-release.json declares no agentVersion")

entry = (manifest.get("platforms") or {}).get(platform)
if not isinstance(entry, dict):
    listed = ", ".join(sorted(manifest.get("platforms") or {})) or "none"
    sys.exit("this release candidate carries no agent for %s (it carries: %s)" % (platform, listed))
if entry.get("filename") != filename:
    sys.exit("gaia-agent-release.json names %r for %s, expected %r" % (entry.get("filename"), platform, filename))

digest = entry.get("sha256") or ""
if not re.fullmatch(r"[0-9a-f]{64}", digest):
    sys.exit("gaia-agent-release.json publishes %s with no valid SHA-256" % filename)

print("%s %s" % (version, digest))
RELEASE_PY
}

# Download one hub artifact and refuse to install it unless its SHA-256 matches
# what the manifest published. Shared by the terminal hub and the flagship agent
# so there is exactly one place the verification rule lives.
#
# Args: url, temp path, expected digest, destination path, label.
# Returns: 0 installed, 2 checksum mismatch, 1 anything else. Callers that treat
# the artifact as optional must still abort on 2 — only integrity is fatal there.
download_and_verify() {
    _url="$1"; _tmp="$2"; _want="$3"; _dest="$4"; _label="$5"

    if ! fetch_file "$_url" "$_tmp"; then
        print_error "Could not download the $_label binary."
        echo "  URL:  $_url"
        echo "  Fix:  check your network and retry."
        return 1
    fi

    # No checksum, no install.
    if command -v sha256sum > /dev/null 2>&1; then
        _got="$(sha256sum "$_tmp" | awk '{print $1}')"
    elif command -v shasum > /dev/null 2>&1; then
        _got="$(shasum -a 256 "$_tmp" | awk '{print $1}')"
    else
        print_error "No sha256sum or shasum available to verify the download."
        echo "  Fix:  install coreutils (Linux) or perl (macOS ships shasum), then retry."
        return 1
    fi

    if [ "$_want" != "$_got" ]; then
        print_error "Checksum mismatch for $_label — refusing to install."
        echo "  expected $_want"
        echo "  got      $_got"
        echo "  Fix:  retry; if it persists report it at https://github.com/amd/gaia/issues"
        return 2
    fi

    # Checked, because this is where a verified download still fails: an
    # unwritable directory, a full disk, a read-only $HOME. Both callers invoke
    # this as `if ! download_and_verify ...`, which switches `set -e` off inside
    # the body, so an unchecked failure here reached the caller as success and
    # printed "installed" over a binary that was never written.
    # (A running gaia-tui is not one of the cases: `install` unlinks the target
    # first, so the replace succeeds and the live process keeps the old inode.)
    if ! mkdir -p "$(dirname "$_dest")"; then
        print_error "Could not create $(dirname "$_dest") for $_label."
        echo "  Fix:  check you own that directory and that the disk is not full."
        return 1
    fi
    if ! install -m 0755 "$_tmp" "$_dest"; then
        print_error "Downloaded and verified $_label, but could not write $_dest."
        echo "  Fix:  check you own $(dirname "$_dest") and that it is writable, then retry."
        echo "  Look: free disk space, and whether $_dest is held by another tool."
        return 1
    fi
    return 0
}

# A missing terminal hub fails the install — it is the advertised entry point.
install_tui() {
    print_step "Installing the GAIA Terminal UI"

    filename=""
    if ! filename="$(tui_asset_name)"; then
        print_error "No GAIA Terminal UI build for $(uname -s)/$(uname -m)."
        echo "Published builds: Linux and macOS on x86_64 and arm64, and Windows."
        echo "See $GAIA_RELEASES_URL/latest"
        exit 1
    fi

    RELEASE_TAG=""
    if ! resolve_release_tag; then
        print_error "Could not find the GAIA release to install the Terminal UI from."
        if [ -n "$GAIA_RELEASE_TAG" ]; then
            echo "  GAIA_RELEASE_TAG='$GAIA_RELEASE_TAG' is not a release tag (expected e.g. v0.25.0)."
            echo "  Fix:  unset it to install the latest release, or name one from"
            echo "        $GAIA_RELEASES_URL"
        else
            echo "  URL:  $GAIA_RELEASES_URL/latest"
            echo "  Fix:  check your network (github.com must be reachable), then retry."
        fi
        exit 1
    fi

    scratch_dir tui
    tmp="$SCRATCH"

    release_url="$GAIA_RELEASES_URL/download/$RELEASE_TAG"
    if ! fetch_file "$release_url/$TUI_SUMS_FILE" "$tmp/$TUI_SUMS_FILE"; then
        print_error "Could not fetch the GAIA Terminal UI checksums for $RELEASE_TAG."
        echo "  URL:  $release_url/$TUI_SUMS_FILE"
        echo "  Fix:  check your network and retry. A release published in the last"
        echo "        few minutes may still be uploading its files."
        exit 1
    fi

    # The checksum file is the only source for the digest: exactly one entry of
    # lowercase hex, or no install. A second line for the file is a newline in
    # $want, which the character check below refuses.
    want="$(awk -v f="$filename" '$2 == f || $2 == "*" f { print $1 }' "$tmp/$TUI_SUMS_FILE")"
    case "$want" in
        *[!0-9a-f]*|"") want="" ;;
    esac
    if [ "${#want}" -ne 64 ]; then
        print_error "$RELEASE_TAG publishes no single valid SHA-256 for $filename — refusing to install unverified."
        echo "  Look: $release_url/$TUI_SUMS_FILE"
        echo "  Fix:  report it at https://github.com/amd/gaia/issues"
        exit 1
    fi

    print_step "Downloading GAIA Terminal UI $RELEASE_TAG ($filename)"
    # Never `gaia`: tui/internal/daemon/client.go resolves `gaia` on PATH to
    # start the Python-owned daemon, so a Go binary by that name finds itself.
    if ! download_and_verify "$release_url/$filename" "$tmp/$filename" "$want" \
            "$GAIA_BIN/gaia-tui" "GAIA Terminal UI"; then
        echo "  Look: $GAIA_RELEASES_URL/tag/$RELEASE_TAG lists the release's files."
        exit 1
    fi
    print_success "GAIA Terminal UI $RELEASE_TAG installed to $GAIA_BIN/gaia-tui"
}

# The flagship agent's frozen sidecar. The terminal hub spawns `gaia-agent` as a
# child process, so without this binary the flagship is listed but cannot run —
# which is what `curl | sh` left behind before: a UI with no agent under it.
#
# Installed into $GAIA_BIN because that is the directory this installer owns and
# has already put on PATH, so the hub's exec.LookPath finds gaia-agent there.
#
# Only a checksum mismatch is fatal. A missing build or a failed download leaves
# a working hub and a working Python CLI, so those warn and continue rather than
# failing an otherwise complete install. Never silent — an unrunnable flagship is
# exactly the thing a user must be told about.
install_flagship_agent() {
    print_step "Installing the GAIA flagship agent"

    platform=""
    if ! platform="$(hub_platform)"; then
        print_warning "No flagship agent build for $(uname -s)/$(uname -m) — skipping."
        echo "  The GAIA Terminal UI still works; the GAIA agent will show as unavailable."
        return 0
    fi
    filename="gaia-agent-${platform}"

    py=""
    if ! py="$(hub_python)"; then
        print_warning "No Python interpreter available to read the agent manifest — skipping."
        return 0
    fi

    scratch_dir agent
    tmp="$SCRATCH"

    # A pinned candidate carries its own frozen agent; everything else takes
    # the Agent Hub's, the same source as `gaia init`.
    if [ "${PIN_KIND:-}" = "rc" ]; then
        base_url="$GAIA_RELEASES_URL/download/$GAIA_RELEASE_TAG"
        manifest_url="$base_url/gaia-agent-release.json"
    else
        manifest_url="$GAIA_HUB_BASE_URL/agents/$FLAGSHIP_AGENT_ID/manifest.json"
    fi
    if ! fetch_file "$manifest_url" "$tmp/manifest.json"; then
        print_warning "Could not fetch the flagship agent manifest — skipping."
        echo "  URL:  $manifest_url"
        echo "  The GAIA agent will show as unavailable until you re-run this installer."
        return 0
    fi

    resolved=""
    if [ "${PIN_KIND:-}" = "rc" ]; then
        resolved="$(read_release_manifest "$py" "$tmp/manifest.json" "$platform" "$filename")" || resolved=""
    else
        resolved="$(read_hub_manifest "$py" "$tmp/manifest.json" "$filename")" || resolved=""
    fi
    if [ -z "$resolved" ]; then
        print_warning "Could not resolve the flagship agent for $platform — skipping."
        echo "  Reported above by the manifest reader."
        echo "  Look: $manifest_url"
        return 0
    fi

    version="${resolved%% *}"
    want="${resolved##* }"

    if [ "${PIN_KIND:-}" = "rc" ]; then
        binary_url="$base_url/$filename"
    else
        binary_url="$GAIA_HUB_BASE_URL/agents/$FLAGSHIP_AGENT_ID/$version/$filename"
    fi
    print_step "Downloading GAIA agent $version for $platform"
    # `|| _rc=$?` rather than `if !`: `set -e` must stay armed, and the two
    # failure modes are not the same failure.
    _rc=0
    download_and_verify "$binary_url" "$tmp/$filename" "$want" \
            "$GAIA_BIN/gaia-agent" "GAIA agent" || _rc=$?
    if [ "$_rc" -eq 2 ]; then
        # A checksum mismatch is never downgraded to a warning.
        echo "  Look: $manifest_url"
        exit 1
    elif [ "$_rc" -ne 0 ]; then
        print_warning "The GAIA agent did not install — skipping."
        echo "  The GAIA Terminal UI still works; re-run this installer to retry."
        return 0
    fi
    # Read by show_next_steps: every path above this line leaves the machine
    # without an agent, and the closing banner must not promise one.
    FLAGSHIP_INSTALLED=1
    print_success "GAIA agent $version installed to $GAIA_BIN/gaia-agent"
}

# Add GAIA to PATH
add_to_path() {
    print_step "Adding GAIA to PATH..."

    # Both bins: the venv holds the Python CLI, $GAIA_BIN holds the terminal hub.
    path_export="export PATH=\"\$PATH:$GAIA_VENV/bin:$GAIA_BIN\""

    # Export for the current session regardless of what we can write.
    PATH="$PATH:$GAIA_VENV/bin:$GAIA_BIN"
    export PATH

    primary_rc=""
    user_shell="${SHELL:-}"
    case "${user_shell##*/}" in
        zsh)
            primary_rc="$HOME/.zshrc"
            ;;
        bash)
            # macOS Terminal starts login shells, which read ~/.bash_profile and
            # never ~/.bashrc. Creating ~/.bash_profile where only ~/.profile
            # exists would shadow it, so prefer whichever the user already has.
            if [ "${OS_NAME:-}" = "Darwin" ]; then
                if [ -f "$HOME/.bash_profile" ]; then
                    primary_rc="$HOME/.bash_profile"
                elif [ -f "$HOME/.profile" ]; then
                    primary_rc="$HOME/.profile"
                else
                    primary_rc="$HOME/.bash_profile"
                fi
            else
                primary_rc="$HOME/.bashrc"
            fi
            ;;
        sh|ksh|dash)
            primary_rc="$HOME/.profile"
            ;;
    esac

    if [ -z "$primary_rc" ]; then
        print_warning "Unrecognized shell (${SHELL:-unset}); not editing any startup file."
        echo "  Add these two directories to your PATH by hand:"
        echo "    $GAIA_VENV/bin"
        echo "    $GAIA_BIN"
        RELOAD_FILE=""
        return 0
    fi

    RELOAD_FILE="$primary_rc"
    added=""

    for rc in "$primary_rc" "$HOME/.bashrc" "$HOME/.zshrc"; do
        # Create only the primary file; the others are updated if they exist.
        if [ ! -f "$rc" ] && [ "$rc" != "$primary_rc" ]; then
            continue
        fi
        # Match on $GAIA_BIN, not the whole line: pre-0.23 installs wrote a
        # venv-only export that a line-exact check would duplicate.
        if [ -f "$rc" ] && grep -Fq "$GAIA_BIN" "$rc"; then
            continue
        fi
        {
            echo ""
            echo "# Added by GAIA installer"
            echo "$path_export"
        } >> "$rc"
        print_success "Added to $rc"
        added="yes"
    done

    if [ -n "$added" ]; then
        print_success "GAIA added to PATH"
    else
        print_success "GAIA is already on PATH in your shell config"
    fi
}

# Show next steps
show_next_steps() {
    echo ""
    printf '%s================================%s\n' "$COLOR_GREEN" "$COLOR_RESET"
    printf '%s  GAIA Installed Successfully!%s\n' "$COLOR_GREEN" "$COLOR_RESET"
    printf '%s================================%s\n' "$COLOR_GREEN" "$COLOR_RESET"
    echo ""

    printf '%sNext steps:%s\n' "$COLOR_CYAN" "$COLOR_RESET"
    if [ -n "${RELOAD_FILE:-}" ]; then
        echo "  1. Reload your shell config:"
        printf '     %ssource %s%s\n' "$COLOR_GREEN" "$RELOAD_FILE" "$COLOR_RESET"
    else
        echo "  1. Open a new terminal (PATH was not written to a startup file)"
    fi
    printf '  2. Set up the local model runtime: %sgaia init%s\n' "$COLOR_GREEN" "$COLOR_RESET"
    echo "     (installs Lemonade Server — asks for your password)"
    if [ "${FLAGSHIP_INSTALLED:-0}" = "1" ]; then
        printf '  3. Talk to the agent: %sgaia-tui%s\n' "$COLOR_GREEN" "$COLOR_RESET"
        printf '     (it opens the GAIA agent; type %s/agents%s to list installed agents)\n' "$COLOR_GREEN" "$COLOR_RESET"
    else
        printf '  3. Open the GAIA Terminal UI: %sgaia-tui%s\n' "$COLOR_GREEN" "$COLOR_RESET"
        printf '     %sThe GAIA agent was skipped — see the warning above — so this\n' "$COLOR_YELLOW"
        printf '     opens the agent list, not a chat. Re-run this installer to retry.%s\n' "$COLOR_RESET"
    fi
    echo ""

    printf '%sDocumentation:%s https://amd-gaia.ai\n' "$COLOR_CYAN" "$COLOR_RESET"
    printf '%sIssues:%s https://github.com/amd/gaia/issues\n' "$COLOR_CYAN" "$COLOR_RESET"
    echo ""
}

# Main installation flow
main() {
    echo ""
    printf '%s========================================%s\n' "$COLOR_CYAN" "$COLOR_RESET"
    printf '%s  GAIA Installer for Linux and macOS%s\n' "$COLOR_CYAN" "$COLOR_RESET"
    printf '%s========================================%s\n' "$COLOR_CYAN" "$COLOR_RESET"
    echo ""

    # Check prerequisites
    detect_environment
    check_download_tool

    # Before anything installs: a bad tag must not leave a half-pinned machine.
    resolve_pin

    announce_elevation

    # Install uv
    install_uv

    # Install GAIA
    install_gaia

    # Before the terminal hub, so a hard failure there still leaves the Python
    # CLI reachable.
    add_to_path

    # Install the terminal hub binary
    install_tui

    # The agent the hub spawns. After install_tui so it lands in the same
    # directory, which is where the hub looks for it first.
    install_flagship_agent

    # Show next steps
    show_next_steps
}

# Run installer
main "$@"
