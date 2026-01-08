#!/usr/bin/env python3
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pre-commit lint hook for Claude Code - cross-platform."""

import os
import re
import subprocess
import sys


def main():
    """Run linting before git commits when executed from Claude Code hooks."""
    tool_input = os.environ.get("TOOL_INPUT", "")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")

    # Skip if env vars not set (not in Claude Code context)
    if not tool_input or not project_dir:
        return 0

    # Only run for git commit commands
    if not re.search(r"git commit|git add.*&&.*git commit", tool_input):
        return 0

    print("Pre-commit lint check...")
    lint_script = os.path.join(project_dir, "util", "lint.py")
    result = subprocess.run([sys.executable, lint_script, "--all", "--fix"])
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
