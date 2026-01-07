#!/bin/bash
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Pre-commit lint wrapper for Claude Code hooks
# Only runs linting when command contains "git commit"

# Check if this is a git commit command
if echo "$TOOL_INPUT" | grep -qE "git commit|git add.*&&.*git commit"; then
    echo "Pre-commit lint check..."
    python "$CLAUDE_PROJECT_DIR/util/lint.py" --all --fix
    exit $?
fi

# Not a commit command, skip linting
exit 0
