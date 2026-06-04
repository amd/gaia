# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression budget for ``ChatAgent._get_system_prompt`` (#1030).

The Gemma-4-E4B default LLM was timing out on the very first PLANNING
call when running ``gaia chat --index <pdf> --query "..."`` because the
ChatAgent system prompt had grown to ~15 000 tokens of inlined rules and
multi-paragraph examples — large enough that prompt processing on
Windows iGPU exceeded Lemonade's internal upstream curl timeout.

This test pins a hard size budget so future additions to the prompt are
forced to budget-balance (compact something else, or move new rules
behind a conditional) instead of silently re-bloating it.

If you intentionally need more headroom for a new feature, raise the
budget here in the same commit that adds the prompt content — and only
after verifying with a live ``gaia chat --index ... --query ...`` run on
Gemma that the cold-start latency is still sane.
"""

from __future__ import annotations

import importlib.util
import sys
from unittest.mock import MagicMock, patch

import pytest

# ChatAgent ships as the standalone gaia-agent-chat wheel (#1102); skip the
# whole module when a framework-only env lacks it.
pytest.importorskip("gaia_agent_chat")

# Stub heavy optional deps so this test can run in a minimal env, but ONLY
# for modules that can't actually be imported in the current environment.
# Stubbing an installed module poisons every transitive consumer's cache —
# e.g. ``gaia.rag.sdk`` binds ``pypdf = <MagicMock>`` at import time, and
# later PDF tests (``tests/unit/rag/test_pdf_extraction_errors.py``) get
# that MagicMock back because the GAIA module stays cached after we pop
# our stub from ``sys.modules``. The result was a flaky "blank PDF reads
# as encrypted" failure that only fired when this test ran first.
_stubbed_modules: list[str] = []
for _mod in (
    "faiss",
    "numpy",
    "sentence_transformers",
    "pdfplumber",
    "pypdf",
    "pypdfium2",
):
    if _mod in sys.modules:
        continue
    if importlib.util.find_spec(_mod) is not None:
        # Real module is installed — let it import normally so downstream
        # caches bind the real implementation, not a MagicMock.
        continue
    sys.modules[_mod] = MagicMock()
    _stubbed_modules.append(_mod)

# Import once: ``gaia_agent_chat.agent`` resolves its faiss/numpy/etc.
# references at this point, so the cached module keeps working even after
# we remove the stubs from ``sys.modules`` below.
from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig  # noqa: E402

# Roll back the temporary stubs AND evict GAIA modules that bound them so
# subsequent tests get a fresh import that resolves against the real
# environment (or fails loudly if the real dep is missing).
for _mod in _stubbed_modules:
    sys.modules.pop(_mod, None)
if _stubbed_modules:
    for _gaia_mod in (
        "gaia.rag.sdk",
        "gaia_agent_chat.agent",
        "gaia.agents.tools.rag_tools",
    ):
        sys.modules.pop(_gaia_mod, None)

# Hard ceilings — chosen so Gemma 4 E4B can comfortably prompt-process
# the full system prompt + a typical RAG tool result + the user query
# on Windows iGPU within Lemonade's upstream timeout. The numbers below
# include healthy headroom (~2× of post-trim sizes measured at the time
# of the #1030 fix).
_BUDGET_CHARS = {
    # profile + indexed-doc count -> max chars
    ("full", 0): 12_000,
    ("full", 1): 14_000,
    ("full", 5): 14_500,
    # Large-N budget — pins the scaling property so a regression that
    # made any per-doc block grow super-linearly would fail here long
    # before it became user-visible. At #1030 fix time, full+100 indexed
    # measured ~11.7K chars; we leave generous headroom.
    ("full", 100): 18_000,
    ("doc", 1): 12_000,
    ("chat", 0): 4_000,
}


def _make_agent(prompt_profile: str, n_indexed: int) -> ChatAgent:
    """Build a ChatAgent skeleton with a fake RAG index of *n_indexed* docs.

    The Agent base class is bypassed (only the prompt-composition path
    is exercised here); we want the cost of the prompt itself, not a
    real Lemonade init.
    """
    cfg = ChatAgentConfig(
        rag_documents=[],
        streaming=False,
        silent_mode=True,
        prompt_profile=prompt_profile,
    )
    with patch("gaia.agents.base.agent.Agent.__init__", return_value=None):
        a = ChatAgent.__new__(ChatAgent)
        a.config = cfg
        indexed = {f"/tmp/doc{i}.pdf" for i in range(n_indexed)} if n_indexed else set()
        a.rag = type("R", (), {"indexed_files": indexed})()
        a.library_documents = []
    return a


@pytest.mark.parametrize(("profile", "n_indexed"), sorted(_BUDGET_CHARS.keys()))
def test_chat_system_prompt_within_budget(profile: str, n_indexed: int) -> None:
    """The composed ChatAgent system prompt must stay under its char budget.

    Regression guard for #1030 — pre-fix the ``full`` profile with one
    indexed document was ~52 000 chars / ~15 000 tokens, which was
    enough to time out Gemma 4 E4B's first planning call on iGPU.
    """
    agent = _make_agent(profile, n_indexed)
    prompt = agent._get_system_prompt()
    budget = _BUDGET_CHARS[(profile, n_indexed)]

    assert prompt, "system prompt should not be empty"
    assert len(prompt) <= budget, (
        f"ChatAgent system prompt exceeded budget for "
        f"profile={profile!r}, indexed={n_indexed}: "
        f"{len(prompt)} chars > {budget} budget. "
        f"This regressed the #1030 fix — either trim what you added, "
        f"move it behind a conditional, or raise the budget in the same "
        f"commit (only after re-validating live on Gemma)."
    )


def test_chat_system_prompt_directives_preserved() -> None:
    """The trimmed prompt must still carry the imperative RAG directives.

    The trim done for #1030 removed multi-paragraph examples, NOT the
    actual rules. This test pins the load-bearing directives so we
    don't silently lose them in a future shrink pass.
    """
    agent = _make_agent("full", 1)
    prompt = agent._get_system_prompt().lower()

    must_contain = [
        # Indexed-doc retrieval is mandatory — the core RAG contract
        "query_specific_file",
        "query_documents",
        # Don't answer document-specific questions from training memory
        "training",
        # No fake JSON / fabricated tool output
        "fake",
        # Index-then-query workflow
        "index_document",
        # Section explicitly listing what's currently indexed
        "currently indexed documents",
    ]
    missing = [m for m in must_contain if m not in prompt]
    assert not missing, (
        f"trimmed system prompt is missing load-bearing directives: {missing}. "
        "These were preserved on purpose for #1030 — re-add them or revert."
    )
