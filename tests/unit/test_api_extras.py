# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Regression test for issue #1617.

`gaia api` auto-mounts the email wheel's REST router whenever
gaia_agent_email is importable. That import chain reaches a module-level
``import keyring`` in gaia.connectors.store, so a documented
``pip install 'amd-gaia[api]' gaia-agent-email`` install crashed at server
startup with ModuleNotFoundError because keyring lived only in the [ui]/[dev]
extras. This test asserts the [api] extra declares it.

This is a packaging assertion, not a runtime import test, so it works
in the CI unit-tests venv that does not actually install [api].
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"

# PyPI distribution names (NOT importable module names) that
# setup.py[api] must declare, mapped to the import-site that needs them
# for diagnostic clarity when the assertion fails.
REQUIRED_API_DISTS = {
    "keyring": "gaia.connectors.store (reached via the email wheel router on gaia api startup)",
}


def _parse_api_extra() -> list[str]:
    """Extract the list of requirement strings from setup.py[api].

    Walks the file line by line so brackets that appear inside ``# comments``
    don't confuse a naive non-greedy regex match.
    """
    lines = SETUP_PY.read_text().splitlines()
    in_block = False
    body: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not in_block:
            if re.match(r'"api"\s*:\s*\[', stripped):
                in_block = True
            continue
        if stripped.startswith("]"):
            break
        # Skip comment-only lines so brackets in comments don't matter.
        if stripped.startswith("#"):
            continue
        body.append(raw)
    assert in_block, 'Could not find "api" extra in setup.py extras_require'
    return re.findall(r'"([^"]+)"', "\n".join(body))


@pytest.mark.parametrize("dist,reason", list(REQUIRED_API_DISTS.items()))
def test_api_extra_declares_startup_dep(dist: str, reason: str) -> None:
    """setup.py[api] must declare each gaia api startup dependency — see #1617."""
    api_reqs = _parse_api_extra()
    matches = [r for r in api_reqs if r.lower().startswith(dist.lower())]
    assert matches, (
        f"setup.py[api] is missing distribution '{dist}' (needed by {reason}).\n"
        f"Current [api] extra: {api_reqs}"
    )
