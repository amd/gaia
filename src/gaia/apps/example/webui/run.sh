#!/bin/bash
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Local development script for Example App - builds and runs the app

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

echo "🚀 Example App - MCP Integration Demo"

# Build installer
echo "📦 Building installer..."
cd "$SCRIPT_DIR"
npm install
npm run make
echo "✅ Build complete: $SCRIPT_DIR/out/make/"

# Run in development mode
echo "💻 Starting development mode..."
cd "$SCRIPT_DIR"
npm start