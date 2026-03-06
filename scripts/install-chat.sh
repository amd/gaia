#!/bin/bash

# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# GAIA Chat - Install Script
# Usage: curl -fsSL https://raw.githubusercontent.com/amd/gaia/main/scripts/install-chat.sh | bash
#
# Installs GAIA Chat globally via npm. After install, run `gaia-chat` from anywhere.

set -e

echo ""
echo "========================================"
echo "  GAIA Chat Installer"
echo "========================================"
echo ""

# ── Prerequisites ────────────────────────────────────────────────────────────

echo "Checking prerequisites..."

# Check Node.js
if ! command -v node &> /dev/null; then
    echo "  ERROR: Node.js is not installed."
    echo "  Install Node.js 18+ from https://nodejs.org"
    exit 1
fi

NODE_VERSION=$(node -v | sed 's/v//' | cut -d. -f1)
echo "  Node.js: $(node -v)"

if [ "$NODE_VERSION" -lt 18 ]; then
    echo "  ERROR: Node.js 18+ is required. Current version: $(node -v)"
    exit 1
fi

# Check npm
if ! command -v npm &> /dev/null; then
    echo "  ERROR: npm is not installed."
    exit 1
fi
echo "  npm: v$(npm -v)"

# Check Python gaia (optional)
if command -v gaia &> /dev/null; then
    echo "  gaia CLI: installed"
else
    echo "  WARNING: 'gaia' CLI not found (optional)"
    echo "    Install with: pip install amd-gaia"
    echo "    Required for full functionality (LLM backend)"
fi

echo ""

# ── Install ──────────────────────────────────────────────────────────────────

echo "Installing GAIA Chat..."
npm install -g @amd-gaia/chat@latest

echo ""
echo "========================================"
echo "  GAIA Chat installed successfully!"
echo "========================================"
echo ""
echo "  Usage:"
echo "    gaia-chat              Start the app (backend + browser)"
echo "    gaia-chat --serve      Serve frontend only"
echo "    gaia-chat --help       Show all options"
echo ""
echo "  Prerequisites for full functionality:"
echo "    pip install amd-gaia   Install Python backend"
echo "    lemonade-server serve  Start LLM server"
echo ""
echo "  Documentation: https://amd-gaia.ai/guides/chat-ui"
echo ""
