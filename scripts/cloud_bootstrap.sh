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

# Defensive only: the hook interpolates $CLAUDE_PROJECT_DIR to locate this
# script, so an unset value fails before this runs. Reachable when invoked directly.
if [ -z "$CLAUDE_PROJECT_DIR" ]; then
    echo "cloud_bootstrap: CLAUDE_PROJECT_DIR is unset, cannot locate the repo root." >&2
    echo "  Set it to the repo root, or run via the SessionStart hook in .claude/settings.json." >&2
    exit 1
fi

if ! cd "$CLAUDE_PROJECT_DIR"; then
    echo "cloud_bootstrap: cannot enter CLAUDE_PROJECT_DIR ($CLAUDE_PROJECT_DIR)." >&2
    exit 1
fi

# Claude Code clones the repo before SessionStart hooks run, so a missing
# checkout means something is wrong upstream. Say so rather than acting on it.
if [ ! -f pyproject.toml ]; then
    echo "cloud_bootstrap: no pyproject.toml at $CLAUDE_PROJECT_DIR, skipping the dev install." >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "cloud_bootstrap: uv is not installed, cannot build the dev environment." >&2
    echo "  Add 'curl -LsSf https://astral.sh/uv/install.sh | sh' to the cloud environment's setup script." >&2
    exit 1
fi

set -e

# Probe the package, not the interpreter: an install interrupted partway leaves
# a working .venv/bin/python behind but no gaia, and every later session would
# skip the rebuild while still being told the install is ready.
if ! .venv/bin/python -c "import gaia" >/dev/null 2>&1; then
    # --clear because uv refuses to build over an existing venv, which is
    # exactly the state the probe above catches.
    uv venv .venv --clear --python 3.12

    # --extra-index-url is load-bearing: without the CPU wheel index this
    # resolves to the CUDA torch build and drags in ~4.7 GB of packages.
    uv pip install --python .venv/bin/python -e ".[dev]" \
        --extra-index-url https://download.pytorch.org/whl/cpu
fi

# SessionStart stdout becomes session context. Without this the session reaches
# for the system python, where gaia isn't importable.
echo "GAIA dev install is in .venv — use .venv/bin/python, .venv/bin/pytest, .venv/bin/gaia."
