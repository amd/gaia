# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Contract for ``Agent._compose_system_prompt`` ordering and the tool-block gate.

Two properties, both latency-motivated (``docs/plans/gaia-agent-latency.md``):

1. A native tool-calling model must not receive the ``AVAILABLE TOOLS`` prose
   block — it already gets the full JSON schemas via ``tools=``, and the block
   restated every name and summary for 1,678 duplicate tokens per call on the
   flagship's registry.
2. Fragments that change mid-session compose LAST. llama.cpp reuses its KV cache
   only up to the first differing token, so volatile text above static text
   invalidates all of it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent


class _StubAgent(Agent):
    """Minimal concrete Agent — no LLM, no registry, no I/O."""

    def __init__(
        self, model_id="Gemma-4-E4B-it-GGUF"
    ):  # pylint: disable=super-init-not-called
        # Deliberately skips Agent.__init__: this exercises prompt composition
        # only, and the real __init__ builds databases, indexes and clients.
        self.model_id = model_id
        self._use_claude = False
        self._active_tool_filter = None
        self._instance_tools = {
            "alpha": {"description": "Do alpha.", "parameters": {}},
            "beta": {"description": "Do beta.", "parameters": {}},
        }
        self._memory_text = "MEMORY-BLOCK"
        self._skills_text = "SKILLS-BLOCK"

    def _register_tools(self):  # pragma: no cover - required abstract
        pass

    def _get_system_prompt(self) -> str:
        return "STATIC-AGENT-PROMPT"

    # Volatile fragments, named exactly as VOLATILE_PROMPT_FRAGMENTS expects.
    def get_memory_system_prompt(self) -> str:
        return self._memory_text

    def get_skills_system_prompt(self) -> str:
        return self._skills_text

    # A static fragment, to prove it is NOT moved to the tail.
    def get_vlm_system_prompt(self) -> str:
        return "STATIC-VISION"


class _StubAgentWithFormat(_StubAgent):
    _response_format_template = "RESPONSE-FORMAT-TEMPLATE"


# ── the tool-block gate ────────────────────────────────────────────────────


def test_native_tool_calling_model_gets_no_available_tools_block():
    agent = _StubAgent(model_id="Gemma-4-E4B-it-GGUF")
    with patch("gaia.llm.lemonade_client.is_tool_calling_model", return_value=True):
        prompt = agent._compose_system_prompt()

    assert "==== AVAILABLE TOOLS ====" not in prompt
    assert "alpha" not in prompt


def test_non_tool_calling_model_still_gets_the_block():
    """The text path is the ONLY way those models learn the tool names."""
    agent = _StubAgent(model_id="some-old-model")
    with patch("gaia.llm.lemonade_client.is_tool_calling_model", return_value=False):
        prompt = agent._compose_system_prompt()

    assert "==== AVAILABLE TOOLS ====" in prompt
    assert "alpha" in prompt and "beta" in prompt


def test_claude_backend_is_treated_as_native():
    agent = _StubAgent()
    agent._use_claude = True
    with patch("gaia.llm.lemonade_client.is_tool_calling_model", return_value=False):
        prompt = agent._compose_system_prompt()

    assert "==== AVAILABLE TOOLS ====" not in prompt


def test_response_format_template_and_tool_block_share_one_gate():
    """Both are redundant for the same reason; they must not disagree."""
    agent = _StubAgentWithFormat()
    with patch("gaia.llm.lemonade_client.is_tool_calling_model", return_value=True):
        native = agent._compose_system_prompt()
    with patch("gaia.llm.lemonade_client.is_tool_calling_model", return_value=False):
        text = agent._compose_system_prompt()

    assert "RESPONSE-FORMAT-TEMPLATE" not in native
    assert "==== AVAILABLE TOOLS ====" not in native
    assert "RESPONSE-FORMAT-TEMPLATE" in text
    assert "==== AVAILABLE TOOLS ====" in text


# ── volatile-last ordering ─────────────────────────────────────────────────


def _positions(prompt: str, *markers: str):
    return [prompt.index(m) for m in markers]


def test_volatile_fragments_compose_after_the_static_ones():
    agent = _StubAgent()
    with patch("gaia.llm.lemonade_client.is_tool_calling_model", return_value=True):
        prompt = agent._compose_system_prompt()

    vision, static, memory, skills = _positions(
        prompt, "STATIC-VISION", "STATIC-AGENT-PROMPT", "MEMORY-BLOCK", "SKILLS-BLOCK"
    )
    assert vision < static < memory
    assert vision < static < skills


def test_the_static_head_is_byte_identical_when_only_memory_changes():
    """The whole point: a remember() must not re-prefill the static text."""
    agent = _StubAgent()
    with patch("gaia.llm.lemonade_client.is_tool_calling_model", return_value=True):
        before = agent._compose_system_prompt()
        agent._memory_text = "MEMORY-BLOCK plus a newly stored fact"
        after = agent._compose_system_prompt()

    assert before != after
    shared = _common_prefix(before, after)
    # Everything static survives the change; only the memory tail differs.
    assert "STATIC-AGENT-PROMPT" in shared
    assert "STATIC-VISION" in shared


def test_the_static_head_is_byte_identical_when_only_skills_change():
    """Per-turn skill-body selection is designed to change every turn."""
    agent = _StubAgent()
    with patch("gaia.llm.lemonade_client.is_tool_calling_model", return_value=True):
        before = agent._compose_system_prompt()
        agent._skills_text = "SKILLS-BLOCK with a different body selected"
        after = agent._compose_system_prompt()

    shared = _common_prefix(before, after)
    assert "STATIC-AGENT-PROMPT" in shared


def test_a_subclass_filtering_mixin_prompts_still_gets_the_ordering():
    """ChatAgent overrides _get_mixin_prompts to drop the SD fragment; the
    volatile/static split is applied to whatever survives that filter."""

    class _Filtering(_StubAgent):
        def _get_mixin_prompts(self):
            return [p for p in super()._get_mixin_prompts() if p != "STATIC-VISION"]

    agent = _Filtering()
    with patch("gaia.llm.lemonade_client.is_tool_calling_model", return_value=True):
        prompt = agent._compose_system_prompt()

    assert "STATIC-VISION" not in prompt
    static, memory = _positions(prompt, "STATIC-AGENT-PROMPT", "MEMORY-BLOCK")
    assert static < memory


def test_a_raising_fragment_is_skipped_not_fatal():
    class _Raising(_StubAgent):
        def get_memory_system_prompt(self):
            raise RuntimeError("memory store is down")

    agent = _Raising()
    with patch("gaia.llm.lemonade_client.is_tool_calling_model", return_value=True):
        prompt = agent._compose_system_prompt()

    assert "STATIC-AGENT-PROMPT" in prompt
    assert "MEMORY-BLOCK" not in prompt


def test_filtered_tool_block_stays_at_the_very_end():
    """Under a dynamic filter the block grows mid-turn, so it is volatile too."""
    agent = _StubAgentWithFormat(model_id="some-old-model")
    agent._active_tool_filter = ["alpha"]
    with patch("gaia.llm.lemonade_client.is_tool_calling_model", return_value=False):
        prompt = agent._compose_system_prompt()

    tools = prompt.index("==== AVAILABLE TOOLS ====")
    assert tools > prompt.index("MEMORY-BLOCK")
    assert tools > prompt.index("RESPONSE-FORMAT-TEMPLATE")
    assert "beta" not in prompt


def _common_prefix(a: str, b: str) -> str:
    limit = min(len(a), len(b))
    i = 0
    while i < limit and a[i] == b[i]:
        i += 1
    return a[:i]


@pytest.mark.parametrize(
    "name",
    ["get_memory_system_prompt", "get_skills_system_prompt"],
)
def test_the_known_volatile_fragments_are_declared(name):
    assert name in Agent.VOLATILE_PROMPT_FRAGMENTS
