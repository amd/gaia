#!/bin/bash
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Local development script for JAX - builds and runs the app

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$(git rev-parse --show-toplevel)"

echo "🚀 JAX - Jira Agent Experience"

# Build installer
echo "📦 Building installer..."
cd "$ROOT_DIR"
rm -rf node_modules package-lock.json
cd "$SCRIPT_DIR"
npm install --prefix .
npm run make
echo "✅ Build complete: $SCRIPT_DIR/out/make/"

# Restore workspace and run dev
echo "💻 Starting development mode..."
cd "$ROOT_DIR"
echo "📦 Restoring workspace dependencies..."
npm install
mkdir -p "$SCRIPT_DIR/node_modules"
rm -rf "$SCRIPT_DIR/node_modules/electron"
cp -r "$ROOT_DIR/node_modules/electron" "$SCRIPT_DIR/node_modules/electron"
cd "$SCRIPT_DIR"
npm start