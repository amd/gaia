# Add `gaia init` command for one-stop setup

## Summary

Adds `gaia init` command for one-stop GAIA setup: installs Lemonade Server, starts the server, downloads required models, and verifies the installation.

## New Commands

| Command | Description |
|---------|-------------|
| `gaia init` | One-stop setup: install Lemonade, start server, download models, verify |
| `gaia install --lemonade` | Install Lemonade Server only |
| `gaia uninstall --lemonade` | Uninstall Lemonade Server |
| `gaia kill --lemonade` | Kill Lemonade server process |

## Changes

### Core Features
- **New `gaia init` command** with profile support (minimal, chat, code, rag, all)
- **Model verification** - Tests each model with small inference requests (chat completions for LLMs, embeddings for embedding models)
- **PATH refresh** - Automatically refreshes PATH from Windows registry after installation so `lemonade-server` is immediately available
- **Installation path display** - Shows where `lemonade-server` was found in step 1
- **New `gaia install --lemonade`** - Install Lemonade Server from GitHub releases
- **New `gaia uninstall --lemonade`** - Uninstall Lemonade Server (downloads matching MSI)
- **New `gaia kill --lemonade`** - Kill Lemonade server (port 8000)
- **Version checking** - Warns on version mismatch, suggests uninstall/reinstall

### Improvements
- **Rich console formatting** for `gaia llm` output
- **Fixed streaming** - Handles None chunks in streaming responses
- **Improved uninstall UI** - Clean Rich-formatted output, suppressed duplicate log messages
- **Updated scripts** - Changed `lemonade-server-dev` to `lemonade-server`

## Usage

```bash
# Full setup
gaia init                      # Default chat profile (~25GB)
gaia init --profile minimal    # Fast setup with 4B model (~4GB)
gaia init --yes                # Non-interactive mode
gaia init --skip-models        # Only install Lemonade

# Individual commands
gaia install --lemonade        # Install Lemonade only
gaia uninstall --lemonade      # Uninstall Lemonade
gaia kill --lemonade           # Stop Lemonade server
```

## Files

| New | Modified |
|-----|----------|
| `src/gaia/installer/` | `src/gaia/cli.py` |
| `tests/unit/test_init_command.py` | `setup.py` |
| | `docs/reference/cli.mdx` |
| | `docs/setup.mdx`, `docs/quickstart.mdx` |
| | `scripts/start-lemonade.*` |

## Test Plan

- [x] Unit tests pass (30 tests)
- [ ] `gaia init --profile minimal --yes`
- [ ] `gaia install --lemonade --yes`
- [ ] `gaia uninstall --lemonade --yes`
- [ ] `gaia kill --lemonade`
