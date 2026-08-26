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

# The Agent Hub is the canonical channel for the terminal hub binary:
# release_components.yml publishes all six platform builds there.
GAIA_HUB_BASE_URL="${GAIA_HUB_BASE_URL:-https://hub.amd-gaia.ai}"
GAIA_HUB_BASE_URL="${GAIA_HUB_BASE_URL%/}"
TERMINAL_HUB_ID="terminal-hub"

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
        if ! uv pip install --python "$GAIA_VENV/bin/python" --upgrade "amd-gaia[api]" --extra-index-url https://download.pytorch.org/whl/cpu --quiet; then
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
    print_warning "  (Using CPU-only PyTorch to avoid large CUDA packages)"

    # Target the venv python rather than sourcing bin/activate: that script is
    # not `set -u` clean (it reads $OSTYPE, unset in dash) and noisily half-fails
    # under the documented `curl … | sh`.
    if ! uv pip install --python "$GAIA_VENV/bin/python" "amd-gaia[api]" --extra-index-url https://download.pytorch.org/whl/cpu; then
        print_error "Failed to install GAIA package"
        exit 1
    fi

    print_success "GAIA package installed successfully"
}

# ---------------------------------------------------------------------------
# Terminal hub (Go binary)
# ---------------------------------------------------------------------------

# Print the Agent Hub platform key for this machine, e.g. "linux-x64".
# These keys are what release_components.yml publishes under, so they are the
# authority — not GOARCH ("amd64") and not `uname -m` ("x86_64").
terminal_hub_platform() {
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

# Resolve the Python interpreter used to read the hub manifest. install_gaia has
# already created the venv by the time this runs.
terminal_hub_python() {
    if [ -x "$GAIA_VENV/bin/python" ]; then
        printf '%s\n' "$GAIA_VENV/bin/python"
    elif command -v python3 > /dev/null 2>&1; then
        command -v python3
    else
        return 1
    fi
}

# A missing terminal hub fails the install — it is the advertised entry point.
install_tui() {
    print_step "Installing the GAIA terminal hub"

    platform=""
    if ! platform="$(terminal_hub_platform)"; then
        print_error "No terminal hub build for $(uname -s)/$(uname -m)."
        echo "Published targets: linux-x64, linux-arm64, darwin-x64, darwin-arm64,"
        echo "win-x64, win-arm64. See $GAIA_HUB_BASE_URL/index.json"
        exit 1
    fi
    filename="gaia-${platform}"

    py=""
    if ! py="$(terminal_hub_python)"; then
        print_error "No Python interpreter available to read the Agent Hub manifest."
        echo "Expected $GAIA_VENV/bin/python (created earlier in this install)."
        exit 1
    fi

    scratch_dir tui
    tmp="$SCRATCH"

    manifest_url="$GAIA_HUB_BASE_URL/agents/$TERMINAL_HUB_ID/manifest.json"
    if ! fetch_file "$manifest_url" "$tmp/manifest.json"; then
        print_error "Could not fetch the terminal hub manifest."
        echo "  URL:  $manifest_url"
        echo "  Fix:  check your network, then retry. If the component is not yet"
        echo "        published for this release, build from source:"
        echo "          git clone https://github.com/amd/gaia && cd gaia/tui && make build"
        echo "  Look: $GAIA_HUB_BASE_URL/index.json lists what the hub serves."
        exit 1
    fi

    # The manifest is the only source for the version, the per-platform
    # filename, and the Worker-computed SHA-256.
    resolved=""
    if ! resolved="$("$py" - "$tmp/manifest.json" "$filename" <<'PYEOF'
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
PYEOF
)"; then
        print_error "Could not resolve the terminal hub build for $platform."
        echo "  Reported above by the manifest reader."
        echo "  Fix:  if your platform is genuinely unpublished, build from source:"
        echo "          git clone https://github.com/amd/gaia && cd gaia/tui && make build"
        echo "  Look: $manifest_url"
        exit 1
    fi

    version="${resolved%% *}"
    want="${resolved##* }"

    binary_url="$GAIA_HUB_BASE_URL/agents/$TERMINAL_HUB_ID/$version/$filename"
    print_step "Downloading terminal hub $version for $platform"
    if ! fetch_file "$binary_url" "$tmp/$filename"; then
        print_error "Could not download the terminal hub binary."
        echo "  URL:  $binary_url"
        echo "  Fix:  check your network and retry."
        echo "  Look: $manifest_url lists what is published for $version."
        exit 1
    fi

    # No checksum, no install.
    if command -v sha256sum > /dev/null 2>&1; then
        got="$(sha256sum "$tmp/$filename" | awk '{print $1}')"
    elif command -v shasum > /dev/null 2>&1; then
        got="$(shasum -a 256 "$tmp/$filename" | awk '{print $1}')"
    else
        print_error "No sha256sum or shasum available to verify the download."
        echo "  Fix:  install coreutils (Linux) or perl (macOS ships shasum), then retry."
        exit 1
    fi

    if [ "$want" != "$got" ]; then
        print_error "Checksum mismatch for $filename — refusing to install."
        echo "  expected $want"
        echo "  got      $got"
        echo "  Fix:  retry; if it persists report it at https://github.com/amd/gaia/issues"
        echo "  Look: $manifest_url"
        exit 1
    fi

    # Never `gaia`: tui/internal/daemon/client.go resolves `gaia` on PATH to
    # start the Python-owned daemon, so a Go binary by that name finds itself.
    mkdir -p "$GAIA_BIN"
    install -m 0755 "$tmp/$filename" "$GAIA_BIN/gaia-tui"
    print_success "Terminal hub $version installed to $GAIA_BIN/gaia-tui"
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
    printf '  3. Open the terminal hub: %sgaia-tui%s\n' "$COLOR_GREEN" "$COLOR_RESET"
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

    # Show next steps
    show_next_steps
}

# Run installer
main "$@"
