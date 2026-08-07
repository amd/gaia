#!/bin/bash
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# SessionStart hook for Claude Code on the web: installs the Python
# dependencies needed to run `pytest tests/unit/` and `python util/lint.py`.
# Local sessions are left alone -- contributors manage their own .venv per
# docs/reference/dev.mdx.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# [api] + [rag] + [dev] is the smallest set that satisfies both gates:
#   - util/lint.py --all imports gaia.rag.sdk, which needs the [rag] deps
#   - tests/unit/ needs the [dev] test stack (pytest-timeout is not in [dev])
# Mirrors .github/workflows/test_unit.yml and lint.yml.
uv pip install --system -e ".[api,rag,dev]" pytest-timeout

# Lemonade Server is not reachable from the web container, so agent memory
# init would fail on import. Same switch CI's unit-test job sets.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo 'export GAIA_MEMORY_DISABLED=1' >> "$CLAUDE_ENV_FILE"
fi

# util/lint.py shells out to `uvx <tool>`; warm the tool cache so the first
# lint run in a session does not pay the download.
if command -v uvx >/dev/null 2>&1; then
  for tool in black isort flake8 pylint mypy autoflake bandit; do
    uvx "$tool" --version >/dev/null 2>&1 || true
  done
fi

echo "GAIA dev environment ready."
