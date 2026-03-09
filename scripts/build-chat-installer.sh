#!/bin/bash
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Build GAIA Agent UI installer for Linux (.deb package)
#
# Usage:
#   ./build-chat-installer.sh                # Full Electron + .deb build
#   ./build-chat-installer.sh --browser      # Browser-only build (for gaia chat --ui)
#   ./build-chat-installer.sh --skip-install # Skip npm install

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WEBUI_DIR="$REPO_ROOT/src/gaia/apps/webui"
ELECTRON_DIR="$REPO_ROOT/src/gaia/electron"

MODE="electron"
SKIP_INSTALL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --browser) MODE="browser"; shift ;;
        --skip-install) SKIP_INSTALL=true; shift ;;
        -h|--help)
            echo "Usage: $0 [--browser] [--skip-install]"
            echo ""
            echo "Options:"
            echo "  --browser       Build frontend only (for gaia chat --ui)"
            echo "  --skip-install  Skip npm install step"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo ""
echo "========================================"
echo "  GAIA Agent UI Installer Builder (Linux)"
echo "  Mode: $MODE"
echo "========================================"
echo ""

# ── Prerequisites ────────────────────────────────────────────────────────────

echo "[1/5] Checking prerequisites..."

# Check Node.js
if command -v node &>/dev/null; then
    NODE_VERSION=$(node --version)
    echo "  Node.js: $NODE_VERSION"
else
    echo "  ERROR: Node.js not found. Install from https://nodejs.org"
    exit 1
fi

# Check npm
if command -v npm &>/dev/null; then
    NPM_VERSION=$(npm --version)
    echo "  npm: v$NPM_VERSION"
else
    echo "  ERROR: npm not found."
    exit 1
fi

if [ "$MODE" = "electron" ]; then
    # Check dpkg for .deb creation
    if command -v dpkg &>/dev/null; then
        echo "  dpkg: available"
    else
        echo "  WARNING: dpkg not found. .deb package creation may fail."
    fi
fi

# ── Install Dependencies ────────────────────────────────────────────────────

echo ""
echo "[2/5] Installing dependencies..."

if [ "$SKIP_INSTALL" = false ]; then
    cd "$WEBUI_DIR"
    npm ci 2>/dev/null || npm install
    echo "  Frontend dependencies installed"
else
    echo "  Skipping npm install (--skip-install)"
fi

# ── Build Frontend ──────────────────────────────────────────────────────────

echo ""
echo "[3/5] Building frontend..."

cd "$WEBUI_DIR"
npm run build

# Verify build output
if [ -f "$WEBUI_DIR/dist/index.html" ]; then
    JS_SIZE=$(find "$WEBUI_DIR/dist/assets" -name "*.js" -exec du -cb {} + 2>/dev/null | tail -1 | cut -f1)
    CSS_SIZE=$(find "$WEBUI_DIR/dist/assets" -name "*.css" -exec du -cb {} + 2>/dev/null | tail -1 | cut -f1)
    echo "  Build output: $((JS_SIZE / 1024))KB JS, $((CSS_SIZE / 1024))KB CSS"
else
    echo "  ERROR: dist/index.html not found"
    exit 1
fi

if [ "$MODE" = "browser" ]; then
    echo ""
    echo "========================================"
    echo "  Browser build complete!"
    echo "  Run: gaia chat --ui"
    echo "========================================"
    exit 0
fi

# ── Build Electron App ──────────────────────────────────────────────────────

echo ""
echo "[4/5] Packaging Electron app..."

cd "$WEBUI_DIR"
npx electron-forge package
echo "  Electron app packaged"

# ── Create .deb Installer ──────────────────────────────────────────────────

echo ""
echo "[5/5] Creating .deb installer..."

cd "$WEBUI_DIR"
npx electron-forge make

# Find the output
DEB_FILE=$(find "$WEBUI_DIR/out/make" -name "*.deb" -type f 2>/dev/null | head -1)
if [ -n "$DEB_FILE" ]; then
    DEB_SIZE=$(du -m "$DEB_FILE" | cut -f1)
    echo "  Installer: $(basename "$DEB_FILE") (${DEB_SIZE} MB)"
    echo "  Location: $DEB_FILE"
    echo ""
    echo "  Install with: sudo dpkg -i $DEB_FILE"
else
    echo "  WARNING: No .deb file found in output"
    find "$WEBUI_DIR/out/make" -type f 2>/dev/null | while read -r f; do
        echo "    Found: $f"
    done
fi

echo ""
echo "========================================"
echo "  GAIA Agent UI installer build complete!"
echo "========================================"
