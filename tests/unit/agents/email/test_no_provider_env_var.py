# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Guard test: GAIA_EMAIL_PROVIDER (and any hidden mailbox env var) must be absent
from the repo (#1603 AC).

The email agent's provider selection is connector-derived (peeks keyring, not
env). An env-var-based override would create a hidden back-door that bypasses
the connector gate and silently selects a provider. This test locks in that the
codebase never introduces such a variable.
"""

from __future__ import annotations

import re
from pathlib import Path

# Repo root is four levels up from this test file:
# tests/unit/agents/email/test_no_provider_env_var.py → ../../../../
_REPO_ROOT = Path(__file__).resolve().parents[4]

# Paths to scan — the agent source and the connectors framework.
_SCAN_PATHS = [
    _REPO_ROOT / "src" / "gaia",
    _REPO_ROOT / "hub" / "agents" / "email" / "python",
]

# Env var names that would constitute a hidden mailbox-provider override.
# GAIA_EMAIL_MCP_FAKE_SEND is an APPROVED test seam — it is NOT on this list.
_BANNED_ENV_VARS = [
    "GAIA_EMAIL_PROVIDER",
    "GAIA_MAIL_PROVIDER",
    "EMAIL_PROVIDER",
    "MAIL_PROVIDER",
]

_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(v) for v in _BANNED_ENV_VARS) + r")\b"
)


def test_no_hidden_mailbox_provider_env_var():
    """No banned GAIA_EMAIL_PROVIDER (or similar) env var appears in the codebase.

    The email send path is connector-derived; provider selection via env var
    would bypass the keyring gate and create a silent fallback. This test is a
    repo-wide static check — if it fails, the new reference must be removed and
    replaced with the connector-derived path.
    """
    hits = []
    for scan_root in _SCAN_PATHS:
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in _PATTERN.finditer(text):
                # Compute line number for the match.
                line_no = text[: match.start()].count("\n") + 1
                hits.append(
                    f"{path.relative_to(_REPO_ROOT)}:{line_no}: {match.group()!r}"
                )

    assert not hits, (
        "Found banned mailbox-provider env var references in the codebase "
        "(provider selection must be connector-derived, not env-var-based):\n"
        + "\n".join(f"  {h}" for h in hits)
    )
