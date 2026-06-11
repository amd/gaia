# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ChatAgent wiring for the dynamic tool loader (#1449).

Covers the ChatAgent-level glue without a Lemonade backend: loader construction
gating (profile + toggle + env), the three off-states reverting to the full
registry (``None`` filter), the selection-query builder, the LRU record hook,
env-override parsing (incl. loud failure on malformed values), and the
native-model known-gap warning.

ChatAgent is built via ``__new__`` with only the attributes each method needs —
``Agent.__init__`` (Lemonade) is never run.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Stub heavy optional deps only when genuinely absent (mirrors the budget test).
_stubbed: list[str] = []
for _mod in ("faiss", "sentence_transformers", "pdfplumber", "pypdf", "pypdfium2"):
    if _mod in sys.modules or importlib.util.find_spec(_mod) is not None:
        continue
    sys.modules[_mod] = MagicMock()
    _stubbed.append(_mod)

from gaia.agents.base.tool_loader import ToolLoader  # noqa: E402
from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig  # noqa: E402

for _mod in _stubbed:
    sys.modules.pop(_mod, None)


def _bare_agent(**attrs) -> ChatAgent:
    """A ChatAgent instance with only the attributes a method-under-test needs."""
    a = ChatAgent.__new__(ChatAgent)
    a.observers = []  # quiet __del__ during GC
    a.conversation_history = []
    a.tool_loader = None
    a._memory_store = object()
    a._dynamic_tools_native_warned = False
    a.model_id = None
    for k, v in attrs.items():
        setattr(a, k, v)
    return a


def _real_loader() -> ToolLoader:
    """A real loader over a tiny registry, with a deterministic embedder."""
    return ToolLoader(
        core_tools=frozenset({"c1"}),
        bundles=[],
        embed_fn=lambda t: np.zeros(768, dtype=np.float32),
        threshold=0.55,
        max_tools=14,
    )


# ── construction gating ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("profile", "dynamic", "expect_loader"),
    [
        ("doc", True, True),
        ("doc", False, False),
        ("full", True, False),
        ("chat", True, False),
    ],
)
def test_loader_built_only_for_doc_profile_with_toggle_on(
    monkeypatch, profile, dynamic, expect_loader
):
    monkeypatch.delenv("GAIA_DYNAMIC_TOOLS", raising=False)
    a = _bare_agent(
        config=ChatAgentConfig(prompt_profile=profile, dynamic_tools=dynamic)
    )
    loader = a._maybe_build_tool_loader()
    assert (loader is not None) == expect_loader


def test_env_toggle_overrides_config(monkeypatch):
    a = _bare_agent(config=ChatAgentConfig(prompt_profile="doc", dynamic_tools=False))
    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS", "1")
    assert a._maybe_build_tool_loader() is not None
    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS", "0")
    assert a._maybe_build_tool_loader() is None


def test_env_threshold_and_max_overrides(monkeypatch):
    a = _bare_agent(config=ChatAgentConfig(prompt_profile="doc", dynamic_tools=True))
    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS_TAU", "0.42")
    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS_MAX", "9")
    loader = a._maybe_build_tool_loader()
    assert loader._threshold == pytest.approx(0.42)
    assert loader._max_tools == 9


def test_malformed_env_threshold_fails_loudly(monkeypatch):
    a = _bare_agent(config=ChatAgentConfig(prompt_profile="doc", dynamic_tools=True))
    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS_TAU", "not-a-float")
    with pytest.raises(ValueError, match="GAIA_DYNAMIC_TOOLS_TAU"):
        a._maybe_build_tool_loader()


def test_malformed_env_max_fails_loudly(monkeypatch):
    a = _bare_agent(config=ChatAgentConfig(prompt_profile="doc", dynamic_tools=True))
    monkeypatch.setenv("GAIA_DYNAMIC_TOOLS_MAX", "ten")
    with pytest.raises(ValueError, match="GAIA_DYNAMIC_TOOLS_MAX"):
        a._maybe_build_tool_loader()


# ── off-states → full registry (None filter) ──────────────────────────────


def test_off_state_toggle_off_returns_none():
    a = _bare_agent(tool_loader=None)  # loader not built
    assert a._dynamic_tools_active() is False
    assert a._select_tools_for_turn("anything") is None


def test_off_state_memory_disabled_returns_none():
    a = _bare_agent(tool_loader=_real_loader(), _memory_store=None)
    assert a._dynamic_tools_active() is False
    assert a._select_tools_for_turn("anything") is None


def test_off_state_embedder_disabled_returns_none():
    loader = _real_loader()
    loader._session_disabled = True  # simulate embedder failure
    a = _bare_agent(tool_loader=loader)
    assert a._dynamic_tools_active() is False
    assert a._select_tools_for_turn("anything") is None


def test_active_state_delegates_to_loader_select():
    sentinel = ["c1", "read_file"]
    loader = MagicMock()
    loader.session_disabled = False
    loader.select.return_value = sentinel
    a = _bare_agent(tool_loader=loader)
    a._tools_registry_value = {}  # not used (loader mocked)
    with patch.object(
        ChatAgent, "_tools_registry", new_callable=lambda: property(lambda self: {})
    ):
        result = a._select_tools_for_turn("read the file")
    assert result is sentinel
    loader.select.assert_called_once()


# ── selection query builder ───────────────────────────────────────────────


def test_query_builder_turn1_is_just_current():
    a = _bare_agent(conversation_history=[])
    assert a._build_tool_selection_query("first message") == "first message"


def test_query_builder_prepends_previous_user_message():
    history = [
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "previous question"},
        {"role": "assistant", "content": "answer"},
    ]
    a = _bare_agent(conversation_history=history)
    q = a._build_tool_selection_query("current question")
    assert q == "previous question\ncurrent question"


def test_query_builder_excludes_assistant_and_truncates():
    history = [{"role": "user", "content": "P" * 5000}]
    a = _bare_agent(conversation_history=history)
    q = a._build_tool_selection_query("C" * 100)
    assert len(q) == 4000  # trailing 4K chars
    assert q.endswith("C" * 100)  # current turn always fully included


# ── record hook + known gap ───────────────────────────────────────────────


def test_on_tool_invoked_forwards_to_loader():
    loader = MagicMock()
    a = _bare_agent(tool_loader=loader)
    a._on_tool_invoked("read_file")
    loader.record_tool_use.assert_called_once_with("read_file")


def test_on_tool_invoked_noop_when_no_loader():
    a = _bare_agent(tool_loader=None)
    a._on_tool_invoked("read_file")  # must not raise


def test_native_model_known_gap_warned_once(caplog):
    loader = MagicMock()
    loader.session_disabled = False
    loader.select.return_value = ["c1"]
    a = _bare_agent(tool_loader=loader, model_id="Gemma-4-E4B-it-GGUF")
    with patch.object(
        ChatAgent, "_tools_registry", new_callable=lambda: property(lambda self: {})
    ):
        with caplog.at_level(logging.WARNING):
            a._select_tools_for_turn("q1")
            a._select_tools_for_turn("q2")
    gap_logs = [r for r in caplog.records if "known gap" in r.getMessage()]
    assert len(gap_logs) == 1  # logged exactly once


def test_non_native_model_no_known_gap_warning(caplog):
    loader = MagicMock()
    loader.session_disabled = False
    loader.select.return_value = ["c1"]
    a = _bare_agent(tool_loader=loader, model_id=None)  # non-tool-calling
    with patch.object(
        ChatAgent, "_tools_registry", new_callable=lambda: property(lambda self: {})
    ):
        with caplog.at_level(logging.WARNING):
            a._select_tools_for_turn("q1")
    assert not any("known gap" in r.getMessage() for r in caplog.records)
