# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Regression test for issue #845.

The Agent UI boot path (gaia.ui.server._import_modules) eagerly imports
faiss + sentence_transformers, and gaia.rag.sdk lazily imports
pypdf / numpy / fitz (pymupdf). After AppImage install — which only
resolves setup.py[ui] — those modules were missing and RAG broke
silently. This test asserts the [ui] extra in setup.py declares every
distribution required by the boot path.

This is a packaging assertion, not a runtime import test, so it works
in the CI unit-tests venv that does not actually install [ui].
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"

# PyPI distribution names (NOT importable module names) that
# setup.py[ui] must declare. Each entry maps a distribution
# requirement-string substring to the import-site that needs it,
# for diagnostic clarity when the assertion fails.
REQUIRED_UI_DISTS = {
    "faiss-cpu": "src/gaia/ui/server.py boot import",
    "sentence-transformers": "src/gaia/ui/server.py boot import",
    "pypdf": "src/gaia/rag/sdk.py PdfReader",
    "pymupdf": "src/gaia/rag/sdk.py fitz",
    "numpy": "src/gaia/rag/sdk.py / faiss",
}


def _parse_ui_extra() -> list[str]:
    """Extract the list of requirement strings from setup.py[ui].

    Walks the file line by line so brackets that appear inside ``# comments``
    don't confuse a naive non-greedy regex match.
    """
    lines = SETUP_PY.read_text().splitlines()
    in_block = False
    body: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not in_block:
            if re.match(r'"ui"\s*:\s*\[', stripped):
                in_block = True
            continue
        if stripped.startswith("]"):
            break
        # Skip comment-only lines so brackets in comments don't matter.
        if stripped.startswith("#"):
            continue
        body.append(raw)
    assert in_block, 'Could not find "ui" extra in setup.py extras_require'
    return re.findall(r'"([^"]+)"', "\n".join(body))


@pytest.mark.parametrize("dist,reason", list(REQUIRED_UI_DISTS.items()))
def test_ui_extra_declares_rag_runtime_dep(dist: str, reason: str) -> None:
    """setup.py[ui] must declare each RAG runtime dependency — see #845."""
    ui_reqs = _parse_ui_extra()
    matches = [r for r in ui_reqs if r.lower().startswith(dist.lower())]
    assert matches, (
        f"setup.py[ui] is missing distribution '{dist}' (needed by {reason}).\n"
        f"Current [ui] extra: {ui_reqs}"
    )
