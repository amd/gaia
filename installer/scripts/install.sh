#!/bin/bash
# GAIA Installer for Linux
# One-command installation: curl -fsSL https://amd-gaia.ai/install.sh | sh

set -euo pipefail

# Configuration
GAIA_HOME="$HOME/.gaia"
GAIA_VENV="$GAIA_HOME/venv"
PYTHON_VERSION="3.12"

# Colors
COLOR_GREEN='\033[0;32m'
COLOR_YELLOW='\033[1;33m'
COLOR_RED='\033[0;31m'
COLOR_CYAN='\033[0;36m'
COLOR_RESET='\033[0m'

# Output functions
print_step() {
    echo -e "${COLOR_CYAN}[*]${COLOR_RESET} $1"
}

print_success() {
    echo -e "${COLOR_GREEN}[✓]${COLOR_RESET} $1"
}

print_error() {
    echo -e "${COLOR_RED}[✗]${COLOR_RESET} $1"
}

print_warning() {
    echo -e "${COLOR_YELLOW}[!]${COLOR_RESET} $1"
}

# Detect environment
detect_environment() {
    print_step "Detecting environment..."

    # Check OS
    if [[ "$OSTYPE" != "linux-gnu"* ]]; then
        print_error "This installer is for Linux only. Detected OS: $OSTYPE"
        if [[ "$OSTYPE" == "darwin"* ]]; then
            echo "For macOS, please use: pip install amd-gaia"
        fi
        exit 1
    fi

    # Check architecture
    ARCH=$(uname -m)
    if [[ "$ARCH" != "x86_64" && "$ARCH" != "amd64" ]]; then
        print_warning "Architecture $ARCH detected. GAIA is optimized for x86_64/amd64."
    fi

    print_success "Environment: Linux ($ARCH)"
}

# Check for curl or wget
check_download_tool() {
    if command -v curl &> /dev/null; then
        DOWNLOAD_CMD="curl"
        print_success "curl is available"
    elif command -v wget &> /dev/null; then
        DOWNLOAD_CMD="wget"
        print_success "wget is available"
    else
        print_error "Neither curl nor wget is installed"
        echo ""
        echo "Please install curl or wget:"
        echo "  Ubuntu/Debian: sudo apt install curl"
        echo "  Fedora: sudo dnf install curl"
        exit 1
    fi
}

# Install uv package manager
install_uv() {
    print_step "Checking for uv package manager..."

    if command -v uv &> /dev/null; then
        print_success "uv is already installed"
        return 0
    fi

    print_step "Installing uv package manager..."
    if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
        print_error "Failed to install uv"
        exit 1
    fi

    # Source uv environment
    export PATH="$HOME/.cargo/bin:$PATH"

    print_success "uv installed successfully"
}

# Create virtual environment and install GAIA
install_gaia() {
    # Check if GAIA is already installed
    if [[ -f "$GAIA_VENV/bin/gaia" ]]; then
        print_warning "GAIA is already installed at $GAIA_HOME"
        print_step "Checking for updates..."

        # Activate venv and upgrade
        source "$GAIA_VENV/bin/activate"
        if uv pip install --upgrade amd-gaia --extra-index-url https://download.pytorch.org/whl/cpu --quiet; then
            print_success "GAIA updated to latest version"
        else
            print_success "GAIA is already up to date"
        fi
        return 0
    fi

    print_step "Creating GAIA environment at $GAIA_HOME..."

    # Create GAIA home directory
    if [[ ! -d "$GAIA_HOME" ]]; then
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

    # Activate and install GAIA
    print_step "Installing GAIA package..."
    print_warning "  (Using CPU-only PyTorch to avoid large CUDA packages)"

    # shellcheck disable=SC1091
    source "$GAIA_VENV/bin/activate"

    if ! uv pip install amd-gaia --extra-index-url https://download.pytorch.org/whl/cpu; then
        print_error "Failed to install GAIA package"
        exit 1
    fi

    print_success "GAIA package installed successfully"
}

# Add GAIA to PATH
add_to_path() {
    print_step "Adding GAIA to PATH..."

    # Both bins: the venv holds the Python CLI, $GAIA_HOME/bin holds the
    # terminal hub. Adding only the venv left gaia-tui installed but unreachable.
    local bin_path="$GAIA_VENV/bin"
    local path_export="export PATH=\"\$PATH:$bin_path:$GAIA_HOME/bin\""
    local added=false

    # Add to .bashrc if it exists
    if [[ -f "$HOME/.bashrc" ]]; then
        if ! grep -q "$bin_path" "$HOME/.bashrc"; then
            echo "" >> "$HOME/.bashrc"
            echo "# Added by GAIA installer" >> "$HOME/.bashrc"
            echo "$path_export" >> "$HOME/.bashrc"
            print_success "Added to ~/.bashrc"
            added=true
        fi
    fi

    # Add to .zshrc if it exists
    if [[ -f "$HOME/.zshrc" ]]; then
        if ! grep -q "$bin_path" "$HOME/.zshrc"; then
            echo "" >> "$HOME/.zshrc"
            echo "# Added by GAIA installer" >> "$HOME/.zshrc"
            echo "$path_export" >> "$HOME/.zshrc"
            print_success "Added to ~/.zshrc"
            added=true
        fi
    fi

    # Export for current session
    export PATH="$PATH:$bin_path"

    if [[ "$added" == true ]]; then
        print_success "GAIA added to PATH"
    else
        print_warning "GAIA may already be in PATH or shell config not found"
    fi
}

# Show next steps
show_next_steps() {
    echo ""
    echo -e "${COLOR_GREEN}================================${COLOR_RESET}"
    echo -e "${COLOR_GREEN}  GAIA Installed Successfully!${COLOR_RESET}"
    echo -e "${COLOR_GREEN}================================${COLOR_RESET}"
    echo ""

    echo -e "${COLOR_CYAN}Next steps:${COLOR_RESET}"
    echo "  1. Reload your shell config:"

    if [[ -f "$HOME/.bashrc" ]]; then
        echo -e "     ${COLOR_GREEN}source ~/.bashrc${COLOR_RESET}"
    elif [[ -f "$HOME/.zshrc" ]]; then
        echo -e "     ${COLOR_GREEN}source ~/.zshrc${COLOR_RESET}"
    fi

    echo -e "  2. Initialize GAIA: ${COLOR_GREEN}gaia init${COLOR_RESET}"
    echo -e "  3. Start chatting: ${COLOR_GREEN}gaia chat${COLOR_RESET}"
    echo ""

    echo -e "${COLOR_CYAN}Documentation:${COLOR_RESET} https://amd-gaia.ai"
    echo -e "${COLOR_CYAN}Issues:${COLOR_RESET} https://github.com/amd/gaia/issues"
    echo ""
}

# Main installation flow

# ---------------------------------------------------------------------------
# Terminal hub (Go binary)
# ---------------------------------------------------------------------------

# Install the `gaia` terminal hub from the GitHub release.
#
# Separate from the Python package on purpose: this is a static binary with no
# interpreter, and it is what a user actually runs. Failure here is reported and
# survivable — the Python CLI is already installed by the time we get here, so a
# release without a matching asset must not abort the whole install.
install_tui() {
    print_step "Installing the GAIA terminal hub"

    local os arch target
    case "$(uname -s)" in
        Linux)  os="linux" ;;
        Darwin) os="darwin" ;;
        *)
            print_warning "No terminal hub build for $(uname -s); skipping."
            return 0
            ;;
    esac
    case "$(uname -m)" in
        x86_64|amd64)  arch="amd64" ;;
        arm64|aarch64) arch="arm64" ;;
        *)
            print_warning "No terminal hub build for $(uname -m); skipping."
            return 0
            ;;
    esac
    target="gaia-${os}-${arch}"

    local base="https://github.com/amd/gaia/releases/latest/download"
    local tmp
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' RETURN

    if ! curl -fsSL "$base/$target" -o "$tmp/gaia"; then
        print_warning "Could not download $target from the latest release."
        print_warning "The Python CLI is installed; the terminal hub is not."
        return 0
    fi

    # Verify before trusting. A checksum file that cannot be fetched is not a
    # reason to install an unverified binary — it is a reason to stop.
    if curl -fsSL "$base/gaia-tui-SHA256SUMS.txt" -o "$tmp/SHA256SUMS"; then
        local want got
        want="$(grep " ${target}\$" "$tmp/SHA256SUMS" | awk '{print $1}')"
        if [ -z "$want" ]; then
            print_warning "No checksum listed for $target; not installing it."
            return 0
        fi
        if command -v sha256sum >/dev/null 2>&1; then
            got="$(sha256sum "$tmp/gaia" | awk '{print $1}')"
        else
            got="$(shasum -a 256 "$tmp/gaia" | awk '{print $1}')"
        fi
        if [ "$want" != "$got" ]; then
            print_error "Checksum mismatch for $target — refusing to install."
            print_error "  expected $want"
            print_error "  got      $got"
            return 0
        fi
    else
        print_warning "Checksum file unavailable; not installing an unverified binary."
        return 0
    fi

    # Installed as `gaia-tui`, NOT `gaia`. The Python CLI already owns `gaia`
    # in the venv, and this binary has no `init`, `daemon` or `connectors`
    # subcommands — whichever won the PATH, something a user needs would stop
    # working. The planned rename (Go takes `gaia`, Python moves to `gaia-cli`)
    # flips this deliberately; until then the two names coexist.
    mkdir -p "$GAIA_HOME/bin"
    install -m 0755 "$tmp/gaia" "$GAIA_HOME/bin/gaia-tui"
    print_success "Terminal hub installed to $GAIA_HOME/bin/gaia-tui"
}

main() {
    echo ""
    echo -e "${COLOR_CYAN}========================================${COLOR_RESET}"
    echo -e "${COLOR_CYAN}  GAIA Installer for Linux${COLOR_RESET}"
    echo -e "${COLOR_CYAN}========================================${COLOR_RESET}"
    echo ""

    # Check prerequisites
    detect_environment
    check_download_tool

    # Install uv
    install_uv

    # Install GAIA
    install_gaia

    # Install the terminal hub binary
    install_tui

    # Add to PATH
    add_to_path

    # Show next steps
    show_next_steps
}

# Run installer
main "$@"
