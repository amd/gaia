# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A missing RAG extra is reported once, with only the hint true for this build.

The warning used to prefix every report with `uv pip install -e '.[rag]'`, an
editable-checkout command that is wrong for a pip install and contradicts the
"isn't bundled" hint a frozen build gives.
"""

import logging

from gaia_agent.chat_agent import _UNSET, ChatAgent

import gaia.rag.sdk as rag_sdk
from gaia.agents import install_hints


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


def _real_missing_extra_warning(caplog, monkeypatch, *, frozen, editable_root):
    """Warning text when the real RAGSDK dependency check fails on this build."""
    monkeypatch.setattr(rag_sdk, "faiss", None)
    monkeypatch.setattr(install_hints, "_frozen", lambda: frozen)
    monkeypatch.setattr(install_hints, "editable_gaia_root", lambda: editable_root)
    agent = _bare()
    agent.path_validator = None
    agent._rag = _UNSET
    agent._rag_config = rag_sdk.RAGConfig(model="test-model")
    agent._rag_missing = False
    agent._rag_init_error = None
    agent._rag_failure_logged = None
    with caplog.at_level(logging.WARNING):
        assert agent.rag is None
        assert agent.rag is None
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    return warnings[0]


def test_a_frozen_build_is_told_only_that_rag_is_not_bundled(caplog, monkeypatch):
    message = _real_missing_extra_warning(
        caplog, monkeypatch, frozen=True, editable_root=None
    )
    assert "isn't bundled" in message
    assert "install with" not in message.lower()


def test_a_pip_install_gets_the_pip_command_not_an_editable_one(caplog, monkeypatch):
    message = _real_missing_extra_warning(
        caplog, monkeypatch, frozen=False, editable_root=None
    )
    assert message.lower().count("install with") == 1
    assert "amd-gaia[rag]" in message
    assert "-e" not in message.split()
    assert "isn't bundled" not in message


def test_a_source_checkout_still_gets_its_editable_command(caplog, monkeypatch):
    message = _real_missing_extra_warning(
        caplog, monkeypatch, frozen=False, editable_root="/src/gaia"
    )
    assert message.lower().count("install with") == 1
    assert "-e" in message.split()
    assert "[rag]" in message
