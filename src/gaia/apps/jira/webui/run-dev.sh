#!/bin/bash
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# Development launch script for JAX (Jira Agent Experience) Electron app
# This script handles the workspace hoisting issue and launches the app in dev mode

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/../../../.." && pwd )"

echo "🚀 Starting JAX (Jira Agent Experience) in development mode..."
echo "   Project root: $PROJECT_ROOT"
echo "   App directory: $SCRIPT_DIR"

# Check if node_modules exists at root
if [ ! -d "$PROJECT_ROOT/node_modules" ]; then
    echo "⚠️  Root node_modules not found. Running npm install at root..."
    cd "$PROJECT_ROOT"
    npm ci
fi

# Check if electron is available
if [ ! -d "$PROJECT_ROOT/node_modules/electron" ]; then
    echo "❌ Electron not found in root node_modules!"
    echo "   Please run 'npm ci' from the project root first."
    exit 1
fi

# Ensure electron is accessible to electron-forge
if [ ! -d "$SCRIPT_DIR/node_modules/electron" ]; then
    echo "📦 Creating symlink for electron..."
    mkdir -p "$SCRIPT_DIR/node_modules"
    ln -sf "../../../../node_modules/electron" "$SCRIPT_DIR/node_modules/electron"
fi

# Change to app directory
cd "$SCRIPT_DIR"

# Start the app using npm start (which runs electron-forge start)
echo "✨ Launching JAX..."
npm start