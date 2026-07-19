# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2115 — answer-quality cleanup.

Two grep-level guards + the LaTeX normalizer:

1. LaTeX artifacts (``$\\rightarrow$``) never reach a plain-text answer.
2. Every user-facing settings-path string says "Settings → Connectors"
   (the UI's actual ``<h4>Connectors</h4>`` label), never "Connections".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import _normalize_plain_text_answer  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[5]
EMAIL_PKG = Path(__file__).resolve().parents[2] / "gaia_agent_email"


# -- item 2: LaTeX normalization -------------------------------------------


def test_normalizes_dollar_wrapped_command():
    assert _normalize_plain_text_answer("go from A $\\rightarrow$ B") == "go from A → B"


def test_normalizes_bare_command():
    assert _normalize_plain_text_answer("A \\rightarrow B") == "A → B"


def test_normalizes_several_symbols():
    src = "x $\\leq$ y, cost $\\times$ 2, a $\\to$ b"
    assert _normalize_plain_text_answer(src) == "x ≤ y, cost × 2, a → b"


def test_leaves_plain_text_untouched():
    plain = "Here's your inbox pre-scan — 5 actionable, 1 suggested archive."
    assert _normalize_plain_text_answer(plain) == plain


def test_leaves_non_latex_backslash_untouched():
    # A Windows path or escaped char that isn't a known TeX command stays put.
    src = "saved to C:\\Users\\me and \\unknowncmd stays"
    assert _normalize_plain_text_answer(src) == src


# -- item 3: settings-path naming consistency ------------------------------


def _iter_source_files():
    for base, patterns in (
        (EMAIL_PKG, ("*.py",)),
        (
            REPO_ROOT / "hub" / "agents" / "python" / "connectors-demo",
            ("*.py",),
        ),
    ):
        for pat in patterns:
            for f in base.rglob(pat):
                if "__pycache__" in f.parts:
                    continue
                yield f


_SETTINGS_CONNECTIONS_RE = re.compile(r"Settings\s*(?:→|->)\s*Connections\b")


def test_no_settings_connections_drift_in_source():
    """User-facing settings path must be 'Connectors', never 'Connections'."""
    offenders = []
    for f in _iter_source_files():
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in _SETTINGS_CONNECTIONS_RE.finditer(text):
            offenders.append(f"{f}: {m.group(0)!r}")
    assert not offenders, "Settings → Connections drift found:\n" + "\n".join(offenders)
