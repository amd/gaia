#!/bin/bash

# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# GAIA - Claude Code cloud session bootstrap
#
# Creates the editable dev install for a Claude Code cloud session. A cloud
# environment's setup script runs before the repo is cloned, so anything that
# needs the checkout has to run here instead, as a SessionStart hook wired up
# in .claude/settings.json.
#
# No-op on local machines.

# CLAUDE_CODE_REMOTE is "true" only inside a cloud session VM.
if [ "$CLAUDE_CODE_REMOTE" != "true" ]; then
    exit 0
fi

# Belt and braces: several workflows run Claude Code in GitHub Actions, which
# provisions its own environment. Never provision one there.
if [ -n "$CI" ]; then
    exit 0
fi

if [ -z "$CLAUDE_PROJECT_DIR" ]; then
    echo "cloud_bootstrap: CLAUDE_PROJECT_DIR is unset, cannot locate the repo root." >&2
    echo "  Expected Claude Code to set it. Check the SessionStart hook in .claude/settings.json." >&2
    exit 1
fi

cd "$CLAUDE_PROJECT_DIR" || exit 1

# Claude Code clones the repo before SessionStart hooks run, so a missing
# checkout means something is wrong upstream. Say so rather than acting on it.
if [ ! -f pyproject.toml ]; then
    echo "cloud_bootstrap: no pyproject.toml at $CLAUDE_PROJECT_DIR, skipping the dev install." >&2
    exit 1
fi

set -e

# A cached environment snapshot may already carry a provisioned venv.
if [ ! -x .venv/bin/python ]; then
    uv venv .venv --python 3.12

    # --extra-index-url is load-bearing: without the CPU wheel index this
    # resolves to the CUDA torch build and drags in ~4.7 GB of packages.
    uv pip install --python .venv/bin/python -e ".[dev]" \
        --extra-index-url https://download.pytorch.org/whl/cpu
fi

# SessionStart stdout becomes session context. Without this the session reaches
# for the system python, where gaia isn't importable.
echo "GAIA dev install is in .venv — use .venv/bin/python, .venv/bin/pytest, .venv/bin/gaia."
