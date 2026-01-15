# GAIA Lightweight Installer - Implementation Plan

**Date:** 2025-01-11
**Status:** Draft
**Author:** Claude (with Kalin)
**Priority:** High

---

## Executive Summary

Create a lightweight, cross-platform installer for GAIA core components that's as easy as installing `uv`:

```powershell
# Windows
irm https://amd-gaia.ai/install.ps1 | iex

# Linux/Mac
curl -fsSL https://amd-gaia.ai/install.sh | sh
```

**Goal:** Zero to chatting in under 2 minutes.

---

## Scope

### In Scope (Core)
| Component | Command | Description |
|-----------|---------|-------------|
| CLI Base | `gaia` | Core framework and CLI |
| Chat | `gaia chat` | Chat SDK + RAG |
| Code Agent | `gaia-code` | AI coding assistant |
| MCP | `gaia mcp` | Model Context Protocol servers |

### Out of Scope (Extended - Install Separately)
| Component | Install Command | Why Excluded |
|-----------|-----------------|--------------|
| EMR Agent | `gaia install emr` | Domain-specific, VLM deps |
| Blender Agent | `gaia install blender` | Requires Blender |
| Jira Agent | `gaia install jira` | Enterprise-specific |
| Talk Agent | `gaia install talk` | Heavy audio deps (500MB+) |
| Eval Framework | `gaia install eval` | Developer tool |

---

## Installation Channels

### 1. One-Liner Install (Primary)

**Windows PowerShell:**
```powershell
irm https://amd-gaia.ai/install.ps1 | iex
```

**Linux/macOS:**
```bash
curl -fsSL https://amd-gaia.ai/install.sh | sh
```

**Characteristics:**
- Zero prerequisites (installs uv if needed)
- Isolated environment (~/.gaia/)
- Automatic PATH setup
- ~30 second install

### 2. winget (Windows Native)

```powershell
winget install AMD.GAIA
```

**Characteristics:**
- Native Windows package manager
- Auto-updates via winget
- Requires manifest in microsoft/winget-pkgs

### 3. pip/uv (Developers)

```bash
# Using uv (recommended)
uv pip install amd-gaia

# Using pip
pip install amd-gaia

# With specific extras
uv pip install "amd-gaia[chat,code,mcp]"
```

**Characteristics:**
- Requires Python 3.10+
- Integrates with existing environments
- Most flexible

### 4. Standalone Executable (Future)

```powershell
# Download and run
.\gaia-setup.exe
```

**Characteristics:**
- No Python required
- Larger download (~100MB)
- Built with PyInstaller or Nuitka

---

## Installation Flow

### One-Liner Flow

```
User runs: irm https://amd-gaia.ai/install.ps1 | iex
                              │
                              ▼
                    ┌─────────────────┐
                    │ Check if uv     │
                    │ is installed    │
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │ No                          │ Yes
              ▼                             ▼
    ┌─────────────────┐           ┌─────────────────┐
    │ Install uv via  │           │ Continue        │
    │ official script │           │                 │
    └────────┬────────┘           └────────┬────────┘
             │                             │
             └──────────────┬──────────────┘
                            ▼
                  ┌─────────────────┐
                  │ Create venv at  │
                  │ ~/.gaia/venv    │
                  └────────┬────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │ Install         │
                  │ amd-gaia[core]  │
                  └────────┬────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │ Add to PATH     │
                  │ (user scope)    │
                  └────────┬────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │ Verify install  │
                  │ gaia --version  │
                  └────────┬────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │ Print success   │
                  │ + quick start   │
                  └─────────────────┘
```

---

## Install Scripts

### Windows: install.ps1

```powershell
#!/usr/bin/env pwsh
# GAIA Installer for Windows
# Usage: irm https://amd-gaia.ai/install.ps1 | iex

$ErrorActionPreference = "Stop"

# Configuration
$GAIA_HOME = "$env:USERPROFILE\.gaia"
$GAIA_VENV = "$GAIA_HOME\venv"
$GAIA_BIN = "$GAIA_VENV\Scripts"
$GAIA_VERSION = "latest"  # or specific version like "0.16.0"
$UV_INSTALL_URL = "https://astral.sh/uv/install.ps1"
$PYTHON_VERSION = "3.11"

# Colors and formatting
function Write-Step { param($msg) Write-Host "  → $msg" -ForegroundColor Cyan }
function Write-Success { param($msg) Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "  ⚠ $msg" -ForegroundColor Yellow }
function Write-Err { param($msg) Write-Host "  ✗ $msg" -ForegroundColor Red }

# Header
Write-Host ""
Write-Host "  ╔════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║            GAIA Installer              ║" -ForegroundColor Cyan
Write-Host "  ║    Local AI for AMD Ryzen AI PCs      ║" -ForegroundColor Cyan
Write-Host "  ╚════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Check Windows version
$winVer = [System.Environment]::OSVersion.Version
if ($winVer.Major -lt 10) {
    Write-Err "Windows 10 or later required"
    exit 1
}

# Step 1: Install uv if needed
Write-Step "Checking for uv package manager..."
$uvPath = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvPath) {
    Write-Warn "uv not found, installing..."
    try {
        Invoke-RestMethod $UV_INSTALL_URL | Invoke-Expression
        # Refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [System.Environment]::GetEnvironmentVariable("Path", "User")
        Write-Success "uv installed"
    } catch {
        Write-Err "Failed to install uv: $_"
        Write-Host "  Please install uv manually: https://docs.astral.sh/uv/"
        exit 1
    }
} else {
    Write-Success "uv found at $($uvPath.Source)"
}

# Step 2: Create GAIA home directory
Write-Step "Creating GAIA home directory..."
if (-not (Test-Path $GAIA_HOME)) {
    New-Item -ItemType Directory -Path $GAIA_HOME -Force | Out-Null
}
Write-Success "GAIA home: $GAIA_HOME"

# Step 3: Create virtual environment
Write-Step "Creating Python environment..."
if (Test-Path $GAIA_VENV) {
    Write-Warn "Existing environment found, recreating..."
    Remove-Item -Recurse -Force $GAIA_VENV
}
& uv venv $GAIA_VENV --python $PYTHON_VERSION
if ($LASTEXITCODE -ne 0) {
    Write-Err "Failed to create virtual environment"
    exit 1
}
Write-Success "Python $PYTHON_VERSION environment created"

# Step 4: Install GAIA
Write-Step "Installing GAIA core..."
$pipArgs = @("pip", "install")
if ($GAIA_VERSION -eq "latest") {
    $pipArgs += "amd-gaia"
} else {
    $pipArgs += "amd-gaia==$GAIA_VERSION"
}
& "$GAIA_BIN\uv.exe" @pipArgs
if ($LASTEXITCODE -ne 0) {
    Write-Err "Failed to install GAIA"
    exit 1
}
Write-Success "GAIA installed"

# Step 5: Add to PATH
Write-Step "Configuring PATH..."
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*$GAIA_BIN*") {
    [Environment]::SetEnvironmentVariable("Path", "$GAIA_BIN;$currentPath", "User")
    $env:Path = "$GAIA_BIN;$env:Path"
    Write-Success "Added to PATH"
} else {
    Write-Success "Already in PATH"
}

# Step 6: Verify installation
Write-Step "Verifying installation..."
$gaiaVersion = & "$GAIA_BIN\gaia.exe" --version 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Success "Verified: $gaiaVersion"
} else {
    Write-Warn "Could not verify installation"
}

# Step 7: Create config directory
$configDir = "$GAIA_HOME\config"
if (-not (Test-Path $configDir)) {
    New-Item -ItemType Directory -Path $configDir -Force | Out-Null
}

# Success message
Write-Host ""
Write-Host "  ════════════════════════════════════════" -ForegroundColor Green
Write-Host "  ✓ GAIA installed successfully!" -ForegroundColor Green
Write-Host "  ════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "  Quick start:" -ForegroundColor White
Write-Host "    gaia chat              # Chat with AI" -ForegroundColor Gray
Write-Host "    gaia-code              # Code assistant" -ForegroundColor Gray
Write-Host "    gaia mcp status        # MCP server status" -ForegroundColor Gray
Write-Host ""
Write-Host "  Manage installation:" -ForegroundColor White
Write-Host "    gaia update            # Update GAIA" -ForegroundColor Gray
Write-Host "    gaia install emr       # Add EMR agent" -ForegroundColor Gray
Write-Host "    gaia doctor            # Check system health" -ForegroundColor Gray
Write-Host ""
Write-Host "  Next step - Install Lemonade (local LLM server):" -ForegroundColor Yellow
Write-Host "    https://amd-gaia.ai/setup#lemonade" -ForegroundColor Cyan
Write-Host ""

# Remind about new terminal
Write-Host "  ⚠ Open a new terminal to use 'gaia' command" -ForegroundColor Yellow
Write-Host ""
```

### Linux/macOS: install.sh

```bash
#!/bin/bash
# GAIA Installer for Linux/macOS
# Usage: curl -fsSL https://amd-gaia.ai/install.sh | sh

set -e

# Configuration
GAIA_HOME="${HOME}/.gaia"
GAIA_VENV="${GAIA_HOME}/venv"
GAIA_BIN="${GAIA_VENV}/bin"
GAIA_VERSION="latest"
PYTHON_VERSION="3.11"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

step() { echo -e "  ${CYAN}→${NC} $1"; }
success() { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
err() { echo -e "  ${RED}✗${NC} $1"; }

# Header
echo ""
echo -e "  ${CYAN}╔════════════════════════════════════════╗${NC}"
echo -e "  ${CYAN}║            GAIA Installer              ║${NC}"
echo -e "  ${CYAN}║    Local AI for AMD Ryzen AI PCs      ║${NC}"
echo -e "  ${CYAN}╚════════════════════════════════════════╝${NC}"
echo ""

# Detect OS
OS="$(uname -s)"
case "$OS" in
    Linux*)  OS_TYPE="linux";;
    Darwin*) OS_TYPE="macos";;
    *)       err "Unsupported OS: $OS"; exit 1;;
esac

# Step 1: Install uv if needed
step "Checking for uv package manager..."
if ! command -v uv &> /dev/null; then
    warn "uv not found, installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.cargo/bin:$PATH"
    success "uv installed"
else
    success "uv found"
fi

# Step 2: Create GAIA home directory
step "Creating GAIA home directory..."
mkdir -p "$GAIA_HOME"
success "GAIA home: $GAIA_HOME"

# Step 3: Create virtual environment
step "Creating Python environment..."
if [ -d "$GAIA_VENV" ]; then
    warn "Existing environment found, recreating..."
    rm -rf "$GAIA_VENV"
fi
uv venv "$GAIA_VENV" --python "$PYTHON_VERSION"
success "Python $PYTHON_VERSION environment created"

# Step 4: Install GAIA
step "Installing GAIA core..."
if [ "$GAIA_VERSION" = "latest" ]; then
    "$GAIA_VENV/bin/uv" pip install amd-gaia
else
    "$GAIA_VENV/bin/uv" pip install "amd-gaia==$GAIA_VERSION"
fi
success "GAIA installed"

# Step 5: Add to PATH
step "Configuring PATH..."
SHELL_NAME=$(basename "$SHELL")
case "$SHELL_NAME" in
    bash)
        RC_FILE="$HOME/.bashrc"
        ;;
    zsh)
        RC_FILE="$HOME/.zshrc"
        ;;
    fish)
        RC_FILE="$HOME/.config/fish/config.fish"
        ;;
    *)
        RC_FILE="$HOME/.profile"
        ;;
esac

PATH_LINE="export PATH=\"$GAIA_BIN:\$PATH\""
if [ "$SHELL_NAME" = "fish" ]; then
    PATH_LINE="set -gx PATH $GAIA_BIN \$PATH"
fi

if ! grep -q "$GAIA_BIN" "$RC_FILE" 2>/dev/null; then
    echo "" >> "$RC_FILE"
    echo "# GAIA" >> "$RC_FILE"
    echo "$PATH_LINE" >> "$RC_FILE"
    success "Added to PATH in $RC_FILE"
else
    success "Already in PATH"
fi

# Add to current session
export PATH="$GAIA_BIN:$PATH"

# Step 6: Verify installation
step "Verifying installation..."
if "$GAIA_BIN/gaia" --version &> /dev/null; then
    VERSION=$("$GAIA_BIN/gaia" --version)
    success "Verified: $VERSION"
else
    warn "Could not verify installation"
fi

# Success message
echo ""
echo -e "  ${GREEN}════════════════════════════════════════${NC}"
echo -e "  ${GREEN}✓ GAIA installed successfully!${NC}"
echo -e "  ${GREEN}════════════════════════════════════════${NC}"
echo ""
echo "  Quick start:"
echo "    gaia chat              # Chat with AI"
echo "    gaia-code              # Code assistant"
echo "    gaia mcp status        # MCP server status"
echo ""
echo "  Manage installation:"
echo "    gaia update            # Update GAIA"
echo "    gaia install emr       # Add EMR agent"
echo "    gaia doctor            # Check system health"
echo ""
echo -e "  ${YELLOW}Next step - Install Lemonade (local LLM server):${NC}"
echo -e "  ${CYAN}https://amd-gaia.ai/setup#lemonade${NC}"
echo ""
echo -e "  ${YELLOW}⚠ Run 'source $RC_FILE' or open a new terminal${NC}"
echo ""
```

---

## Update System

### `gaia update` Command

```python
# src/gaia/cli.py

@cli.command()
@click.option("--version", "-v", default=None, help="Specific version to install")
@click.option("--pre", is_flag=True, help="Include pre-release versions")
@click.option("--check", is_flag=True, help="Check for updates without installing")
@click.option("--channel", type=click.Choice(["stable", "beta", "nightly"]), default="stable")
def update(version, pre, check, channel):
    """Update GAIA to the latest version."""
    from gaia.installer.updater import Updater

    updater = Updater()

    if check:
        # Just check for updates
        result = updater.check_for_updates(channel=channel, include_pre=pre)
        if result.update_available:
            console.print(f"[green]Update available:[/green] {result.current} → {result.latest}")
            console.print(f"  Run [cyan]gaia update[/cyan] to install")
        else:
            console.print(f"[green]✓[/green] GAIA is up to date ({result.current})")
        return

    # Perform update
    updater.update(
        target_version=version,
        channel=channel,
        include_pre=pre,
    )
```

### Updater Implementation

```python
# src/gaia/installer/updater.py

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
from packaging.version import Version
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

@dataclass
class UpdateCheckResult:
    current: str
    latest: str
    update_available: bool
    release_notes: Optional[str] = None
    release_url: Optional[str] = None


class Updater:
    """GAIA self-updater."""

    PYPI_URL = "https://pypi.org/pypi/amd-gaia/json"
    GITHUB_API = "https://api.github.com/repos/amd/gaia/releases"

    def __init__(self):
        self.gaia_home = Path.home() / ".gaia"
        self.venv_path = self.gaia_home / "venv"
        self.is_managed_install = self.venv_path.exists()

    def get_current_version(self) -> str:
        """Get currently installed version."""
        from gaia import __version__
        return __version__

    def get_latest_version(self, channel: str = "stable", include_pre: bool = False) -> tuple[str, dict]:
        """
        Fetch latest version from PyPI.

        Returns:
            (version_string, release_info_dict)
        """
        try:
            response = httpx.get(self.PYPI_URL, timeout=10)
            response.raise_for_status()
            data = response.json()

            if channel == "stable" and not include_pre:
                # Get latest stable release
                version = data["info"]["version"]
            else:
                # Get latest including pre-releases
                versions = list(data["releases"].keys())
                versions.sort(key=Version, reverse=True)

                if include_pre:
                    version = versions[0]
                else:
                    # Filter out pre-releases
                    stable_versions = [v for v in versions if not Version(v).is_prerelease]
                    version = stable_versions[0] if stable_versions else versions[0]

            release_info = data["releases"].get(version, [{}])[0]
            return version, release_info

        except Exception as e:
            console.print(f"[red]Failed to check for updates:[/red] {e}")
            raise

    def check_for_updates(self, channel: str = "stable", include_pre: bool = False) -> UpdateCheckResult:
        """Check if updates are available."""
        current = self.get_current_version()
        latest, release_info = self.get_latest_version(channel, include_pre)

        current_ver = Version(current)
        latest_ver = Version(latest)

        return UpdateCheckResult(
            current=current,
            latest=latest,
            update_available=latest_ver > current_ver,
            release_url=release_info.get("project_url"),
        )

    def update(
        self,
        target_version: Optional[str] = None,
        channel: str = "stable",
        include_pre: bool = False,
    ):
        """
        Update GAIA to specified or latest version.

        Handles both managed installs (~/.gaia/venv) and pip installs.
        """
        current = self.get_current_version()

        if target_version:
            target = target_version
        else:
            target, _ = self.get_latest_version(channel, include_pre)

        if Version(target) <= Version(current):
            console.print(f"[green]✓[/green] Already at version {current}")
            if Version(target) < Version(current):
                console.print(f"  [dim](requested {target} but {current} is newer)[/dim]")
            return

        console.print(f"[cyan]Updating GAIA:[/cyan] {current} → {target}")
        console.print()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:

            # Step 1: Download new version
            task = progress.add_task("Downloading update...", total=None)

            if self.is_managed_install:
                # Use uv in the managed venv
                uv_path = self._get_uv_path()
                pip_args = [
                    str(uv_path), "pip", "install",
                    f"amd-gaia=={target}",
                    "--upgrade",
                ]
            else:
                # Use pip in current environment
                pip_args = [
                    sys.executable, "-m", "pip", "install",
                    f"amd-gaia=={target}",
                    "--upgrade",
                ]

            progress.update(task, description="Installing update...")

            try:
                result = subprocess.run(
                    pip_args,
                    capture_output=True,
                    text=True,
                    check=True,
                )
            except subprocess.CalledProcessError as e:
                progress.stop()
                console.print(f"[red]Update failed:[/red]")
                console.print(e.stderr)
                raise

            progress.update(task, description="Verifying...")

            # Step 2: Verify update
            new_version = self._get_installed_version()

            progress.stop()

        if new_version == target:
            console.print()
            console.print(f"[green]✓ Successfully updated to {target}[/green]")
            self._show_release_notes(target)
        else:
            console.print(f"[yellow]⚠ Update completed but version mismatch[/yellow]")
            console.print(f"  Expected: {target}, Got: {new_version}")

    def _get_uv_path(self) -> Path:
        """Get path to uv in managed install."""
        if sys.platform == "win32":
            return self.venv_path / "Scripts" / "uv.exe"
        else:
            return self.venv_path / "bin" / "uv"

    def _get_installed_version(self) -> str:
        """Get version after update by reimporting."""
        # Clear cached module
        if "gaia" in sys.modules:
            del sys.modules["gaia"]

        # Re-import to get new version
        import importlib
        gaia = importlib.import_module("gaia")
        return gaia.__version__

    def _show_release_notes(self, version: str):
        """Show release notes for the new version."""
        try:
            response = httpx.get(
                f"{self.GITHUB_API}/tags/v{version}",
                timeout=5,
            )
            if response.status_code == 200:
                data = response.json()
                body = data.get("body", "")
                if body:
                    console.print()
                    console.print("[dim]Release notes:[/dim]")
                    # Truncate if too long
                    lines = body.split("\n")[:10]
                    for line in lines:
                        console.print(f"  {line}")
                    if len(body.split("\n")) > 10:
                        console.print("  ...")
                        console.print(f"  [cyan]Full notes: {data.get('html_url')}[/cyan]")
        except Exception:
            pass  # Release notes are optional

    def rollback(self, version: str):
        """Rollback to a specific version."""
        console.print(f"[yellow]Rolling back to {version}...[/yellow]")
        self.update(target_version=version)
```

### Update Channels

```python
# src/gaia/installer/channels.py

CHANNELS = {
    "stable": {
        "description": "Production-ready releases",
        "pypi_filter": lambda v: not Version(v).is_prerelease,
        "update_check_interval_hours": 24,
    },
    "beta": {
        "description": "Beta releases for testing",
        "pypi_filter": lambda v: "b" in v or "rc" in v or not Version(v).is_prerelease,
        "update_check_interval_hours": 12,
    },
    "nightly": {
        "description": "Latest development builds",
        "pypi_filter": lambda v: True,  # All versions
        "update_check_interval_hours": 6,
    },
}
```

---

## Additional CLI Commands

### `gaia doctor` - System Health Check

```python
@cli.command()
def doctor():
    """Check system health and diagnose issues."""
    from gaia.installer.doctor import run_diagnostics
    run_diagnostics()
```

```python
# src/gaia/installer/doctor.py

def run_diagnostics():
    """Run system diagnostics."""
    console.print()
    console.print("[bold]GAIA System Diagnostics[/bold]")
    console.print("=" * 40)
    console.print()

    checks = [
        ("Python version", check_python_version),
        ("GAIA installation", check_gaia_install),
        ("Lemonade server", check_lemonade),
        ("Default model", check_default_model),
        ("Disk space", check_disk_space),
        ("Memory", check_memory),
        ("Network", check_network),
    ]

    all_passed = True
    for name, check_fn in checks:
        try:
            passed, message = check_fn()
            if passed:
                console.print(f"  [green]✓[/green] {name}: {message}")
            else:
                console.print(f"  [red]✗[/red] {name}: {message}")
                all_passed = False
        except Exception as e:
            console.print(f"  [yellow]?[/yellow] {name}: Error - {e}")
            all_passed = False

    console.print()
    if all_passed:
        console.print("[green]All checks passed![/green]")
    else:
        console.print("[yellow]Some issues detected. See above for details.[/yellow]")
```

### `gaia install <component>` - Install Extensions

```python
@cli.group()
def install():
    """Install GAIA components and extensions."""
    pass

@install.command("emr")
def install_emr():
    """Install EMR (Medical Intake) agent."""
    _install_extra("emr", "amd-gaia[emr]")

@install.command("talk")
def install_talk():
    """Install Talk (Voice) agent."""
    _install_extra("talk", "amd-gaia[talk]")

@install.command("blender")
def install_blender():
    """Install Blender automation agent."""
    _install_extra("blender", "amd-gaia[blender]")

@install.command("jira")
def install_jira():
    """Install Jira integration agent."""
    _install_extra("jira", "amd-gaia[jira]")

@install.command("eval")
def install_eval():
    """Install evaluation framework."""
    _install_extra("eval", "amd-gaia[eval]")

@install.command("all")
def install_all():
    """Install all optional components."""
    _install_extra("all", "amd-gaia[all]")

def _install_extra(name: str, package: str):
    """Install an optional extra."""
    console.print(f"[cyan]Installing {name}...[/cyan]")

    # Detect install method
    gaia_home = Path.home() / ".gaia"
    if (gaia_home / "venv").exists():
        # Managed install
        uv = gaia_home / "venv" / ("Scripts" if sys.platform == "win32" else "bin") / "uv"
        subprocess.run([str(uv), "pip", "install", package], check=True)
    else:
        # Pip install
        subprocess.run([sys.executable, "-m", "pip", "install", package], check=True)

    console.print(f"[green]✓ {name} installed successfully[/green]")
```

### `gaia uninstall` - Remove GAIA

```python
@cli.command()
@click.option("--purge", is_flag=True, help="Also remove data and configuration")
@click.confirmation_option(prompt="Are you sure you want to uninstall GAIA?")
def uninstall(purge):
    """Uninstall GAIA from this system."""
    gaia_home = Path.home() / ".gaia"

    if not gaia_home.exists():
        console.print("[yellow]GAIA home directory not found[/yellow]")
        return

    console.print("[cyan]Uninstalling GAIA...[/cyan]")

    # Remove venv
    venv_path = gaia_home / "venv"
    if venv_path.exists():
        shutil.rmtree(venv_path)
        console.print("  Removed virtual environment")

    if purge:
        # Remove all data
        shutil.rmtree(gaia_home)
        console.print("  Removed all GAIA data")
    else:
        console.print(f"  [dim]Data preserved at {gaia_home}[/dim]")
        console.print(f"  [dim]Use --purge to remove everything[/dim]")

    # Note about PATH
    console.print()
    console.print("[yellow]Note:[/yellow] You may need to remove GAIA from your PATH manually:")
    console.print(f"  Remove: {gaia_home / 'venv' / 'Scripts'}")
    console.print()
    console.print("[green]✓ GAIA uninstalled[/green]")
```

---

## winget Manifest

```yaml
# manifests/a/AMD/GAIA/0.16.0/AMD.GAIA.installer.yaml
PackageIdentifier: AMD.GAIA
PackageVersion: 0.16.0
InstallerType: exe
Installers:
  - Architecture: x64
    InstallerUrl: https://github.com/amd/gaia/releases/download/v0.16.0/gaia-installer-0.16.0.exe
    InstallerSha256: <SHA256>
    InstallerSwitches:
      Silent: /S
      SilentWithProgress: /S
ManifestType: installer
ManifestVersion: 1.4.0

---
# manifests/a/AMD/GAIA/0.16.0/AMD.GAIA.locale.en-US.yaml
PackageIdentifier: AMD.GAIA
PackageVersion: 0.16.0
PackageLocale: en-US
Publisher: Advanced Micro Devices, Inc.
PublisherUrl: https://www.amd.com
PackageName: GAIA
PackageUrl: https://github.com/amd/gaia
License: MIT
ShortDescription: Local AI framework for AMD Ryzen AI PCs
Description: |
  GAIA (Generative AI Is Awesome) is AMD's open-source framework for running
  generative AI applications locally on AMD hardware, with optimizations for
  Ryzen AI processors with NPU support.

  Features:
  - Chat with RAG (document Q&A)
  - Code assistant
  - MCP (Model Context Protocol) integration
  - 100% local, private AI
Tags:
  - ai
  - llm
  - local-ai
  - amd
  - ryzen-ai
  - chat
  - rag
  - privacy
ManifestType: defaultLocale
ManifestVersion: 1.4.0

---
# manifests/a/AMD/GAIA/0.16.0/AMD.GAIA.yaml
PackageIdentifier: AMD.GAIA
PackageVersion: 0.16.0
DefaultLocale: en-US
ManifestType: version
ManifestVersion: 1.4.0
```

---

## pyproject.toml (Updated)

```toml
[project]
name = "amd-gaia"
version = "0.16.0"
description = "Local AI framework for AMD Ryzen AI PCs"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.10"
authors = [
    {name = "AMD", email = "gaia@amd.com"}
]

dependencies = [
    # Core - minimal dependencies
    "click>=8.0",
    "rich>=13.0",
    "httpx>=0.24",
    "pydantic>=2.0",
    "packaging>=23.0",
]

[project.optional-dependencies]
# Chat SDK + RAG
chat = [
    "pypdf>=3.0",
    "sentence-transformers>=2.0",
    "faiss-cpu>=1.7",
    "numpy>=1.24",
]

# MCP integration
mcp = [
    "websockets>=12.0",
]

# Default = core + chat + mcp (what most users want)
default = [
    "amd-gaia[chat]",
    "amd-gaia[mcp]",
]

# Extended agents (install separately)
emr = [
    "amd-gaia[chat]",
    "pillow>=10.0",
    "python-multipart>=0.0.6",
]

blender = []  # No extra deps, requires Blender

jira = [
    "jira>=3.0",
]

talk = [
    "sounddevice>=0.4",
    "numpy>=1.24",
]

eval = [
    "amd-gaia[chat]",
    "pandas>=2.0",
    "matplotlib>=3.7",
]

# Development
dev = [
    "pytest>=7.0",
    "pytest-asyncio>=0.21",
    "black>=23.0",
    "isort>=5.12",
    "mypy>=1.0",
]

# Everything
all = [
    "amd-gaia[default]",
    "amd-gaia[emr]",
    "amd-gaia[blender]",
    "amd-gaia[jira]",
    "amd-gaia[talk]",
    "amd-gaia[eval]",
    "amd-gaia[dev]",
]

[project.scripts]
gaia = "gaia.cli:main"
gaia-code = "gaia.agents.code:main"

[project.entry-points."gaia.agents"]
# Entry points for optional agents (installed separately)
emr = "gaia.agents.emr:main"
blender = "gaia.agents.blender:main"
jira = "gaia.agents.jira:main"
talk = "gaia.talk:main"
```

---

## Directory Structure

```
src/gaia/
├── installer/
│   ├── __init__.py
│   ├── updater.py          # Update logic
│   ├── doctor.py           # Diagnostics
│   ├── channels.py         # Update channels
│   └── shortcuts.py        # Desktop shortcut creation
├── cli.py                  # Main CLI (includes install/update commands)
└── ...

scripts/
├── install.ps1             # Windows installer
├── install.sh              # Linux/macOS installer
└── build-winget.py         # Generate winget manifests
```

---

## Testing Plan

### Unit Tests
- [ ] Version comparison logic
- [ ] Update channel filtering
- [ ] PATH manipulation
- [ ] Shortcut creation

### Integration Tests
- [ ] Fresh install on clean Windows VM
- [ ] Fresh install on clean Linux VM
- [ ] Upgrade from previous version
- [ ] Rollback to previous version
- [ ] Install optional components

### Manual Testing Checklist
- [ ] PowerShell one-liner works
- [ ] Bash one-liner works
- [ ] `gaia update` works
- [ ] `gaia update --check` works
- [ ] `gaia doctor` works
- [ ] `gaia install emr` works
- [ ] `gaia uninstall` works
- [ ] `gaia uninstall --purge` works
- [ ] winget install works (after submission)

---

## Rollout Plan

1. **Phase 1:** Implement install scripts + host on amd-gaia.ai
2. **Phase 2:** Add `gaia update` and `gaia doctor` commands
3. **Phase 3:** Submit winget manifest
4. **Phase 4:** Add auto-update check (optional prompt)

---

## Success Metrics

| Metric | Target |
|--------|--------|
| Install time (one-liner) | < 60 seconds |
| Update time | < 30 seconds |
| Install script size | < 10 KB |
| Core package size | < 50 MB |
| Support tickets about install | < 5% of users |

---

*Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.*
*SPDX-License-Identifier: MIT*
