# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2115 — answer-quality cleanup.

LaTeX artifacts (``$\\rightarrow$``) never reach a plain-text answer. The
"Settings → Connectors" naming guard lives in
``tests/unit/test_settings_connectors_name.py``, which covers every package.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import _normalize_plain_text_answer  # noqa: E402

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
