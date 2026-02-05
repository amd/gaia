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
        echo "  To reinstall, delete the directory first: rm -rf '$GAIA_HOME'"
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

    local bin_path="$GAIA_VENV/bin"
    local path_export="export PATH=\"\$PATH:$bin_path\""
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

    # Add to PATH
    add_to_path

    # Show next steps
    show_next_steps
}

# Run installer
main "$@"
