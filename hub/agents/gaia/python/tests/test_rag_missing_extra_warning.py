# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A missing RAG extra is reported once, with only the hint true for this build.

The warning used to prefix every report with `uv pip install -e '.[rag]'`, an
editable-checkout command that is wrong for a pip install and contradicts the
"isn't bundled" hint a frozen build gives.
"""

import logging

from gaia_agent.chat_agent import ChatAgent


def _bare():
    agent = ChatAgent.__new__(ChatAgent)
    agent.observers = []
    return agent


def test_missing_extra_logs_the_exceptions_own_hint_once(caplog):
    agent = _bare()
    exc = ImportError(
        "Missing required RAG dependencies: faiss-cpu\n\nGAIA[rag] isn't bundled"
    )
    with caplog.at_level(logging.WARNING):
        agent._record_rag_failure(exc, missing=True)
        agent._record_rag_failure(exc, missing=True)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "isn't bundled" in warnings[0]
    assert "pip install -e" not in warnings[0]
    assert agent._rag_missing is True
