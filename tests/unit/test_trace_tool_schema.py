# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What a ``--trace`` artifact says about the tools that went to the model.

Issue #3774: ``agent_output_<timestamp>.json`` carried the system prompt and
the conversation but nothing about the tool schema — the largest single
component of the prompt — so a run's prompt could not be decomposed into
system / tools / history from the artifact GAIA itself emits. These tests pin
the four properties that closes it: the schema is present, it reflects the
per-turn tool filter, its size is recorded, and the turn record is attached
before the file is written rather than after.
"""

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME


@pytest.fixture(autouse=True)
def _clean_registry():
    """Save/restore the global tool registry around each test."""
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch):
    """A developer's exported diagnostics must not flip these tests."""
    monkeypatch.delenv("GAIA_TURN_LOG", raising=False)
    monkeypatch.delenv("GAIA_TRACE_TOOL_SCHEMA", raising=False)


class _TraceAgent(Agent):
    """Minimal concrete Agent that pulls tools from the global registry."""

    def _register_tools(self):  # tools are registered per-test via @tool
        pass


def _register_tools():
    @tool
    def alpha_tool(target: str) -> dict:
        """Does the alpha thing to a target."""
        return {"status": "ok", "target": target}

    @tool
    def beta_tool(count: int) -> dict:
        """Does the beta thing count times."""
        return {"status": "ok", "count": count}


def _make_agent(tmp_path, **kwargs) -> _TraceAgent:
    """A real Agent, no Lemonade, writing traces into *tmp_path*."""
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _TraceAgent(
            skip_lemonade=True,
            silent_mode=True,
            model_id=DEFAULT_MODEL_NAME,
            output_dir=str(tmp_path),
            **kwargs,
        )
    agent.chat = MagicMock()
    # reasoning=None or MagicMock auto-creates it and the trace JSON won't encode.
    agent.chat.send_messages.return_value = MagicMock(
        text="all done", stats=None, reasoning=None
    )
    agent.chat.get_stats.return_value = None
    return agent


def _run_trace(agent, name="trace.json") -> dict:
    result = agent.process_query("hello", trace=True, filename=name)
    with open(result["output_file"], encoding="utf-8") as fh:
        return json.load(fh)


class TestSchemaPresentInTrace:
    def test_trace_file_carries_the_tool_schema(self, tmp_path):
        """The artifact names the tools and carries the schema text sent."""
        _register_tools()
        agent = _make_agent(tmp_path)
        assert agent._uses_native_tool_calls(), "test needs the native tools= path"

        written = _run_trace(agent)

        block = written["tool_schema"]
        assert block["sent"] is True
        assert block["render"] == "native"
        assert {"alpha_tool", "beta_tool"} <= set(block["tool_names"])
        assert block["tools_sent"] == len(block["tool_names"])
        assert [s["function"]["name"] for s in block["schemas"]] == block["tool_names"]
        alpha = next(
            s for s in block["schemas"] if s["function"]["name"] == "alpha_tool"
        )
        assert alpha["function"]["parameters"]["properties"] == {
            "target": {"type": "string"}
        }

    def test_size_fields_let_the_prompt_be_decomposed(self, tmp_path):
        """system / tools / history shares are computable from one file."""
        _register_tools()
        agent = _make_agent(tmp_path)

        written = _run_trace(agent)
        block = written["tool_schema"]

        assert block["schema_chars"] == len(
            json.dumps(block["schemas"], ensure_ascii=False)
        )
        assert block["schema_tokens"] > 0
        # The other half of the fixed prefill has to be in the same file.
        assert "system_prompt" in written

    def test_schema_matches_what_the_backend_was_handed(self, tmp_path):
        """Recorded from the value passed as ``tools=``, not re-rendered later."""
        _register_tools()
        agent = _make_agent(tmp_path)
        sent = {}

        def _send(*_args, **kwargs):
            sent["tools"] = kwargs.get("tools")
            return MagicMock(text="all done", stats=None, reasoning=None)

        agent.chat.send_messages.side_effect = _send

        written = _run_trace(agent)

        assert sent["tools"], "the mocked backend never saw tools="
        assert written["tool_schema"]["schemas"] == sent["tools"]


class TestFilterIsVisible:
    def test_filtered_run_is_distinguishable_from_a_full_one(
        self, tmp_path, monkeypatch
    ):
        """A per-turn subset shows up as the filter and the names sent."""
        _register_tools()
        agent = _make_agent(tmp_path)
        monkeypatch.setattr(agent, "_select_tools_for_turn", lambda _q: ["beta_tool"])

        block = _run_trace(agent)["tool_schema"]

        assert block["filter"] == ["beta_tool"]
        assert block["tool_names"] == ["beta_tool"]
        assert block["tools_sent"] == 1
        assert block["tools_registered"] > 1  # the registry itself is untouched

    def test_unfiltered_run_records_a_null_filter(self, tmp_path):
        _register_tools()
        agent = _make_agent(tmp_path)

        block = _run_trace(agent)["tool_schema"]

        assert block["filter"] is None
        assert block["tools_sent"] == block["tools_registered"]


class TestNoCarryOverBetweenTurns:
    def test_a_turn_that_never_called_the_backend_reports_nothing_sent(self, tmp_path):
        """No carry-over: last turn's schema must not be reported as this one's."""
        _register_tools()
        agent = _make_agent(tmp_path)
        _run_trace(agent, "first.json")

        agent._cancel_event = threading.Event()
        agent._cancel_event.set()
        block = _run_trace(agent, "second.json")["tool_schema"]

        assert block["sent"] is False
        assert block["tools_sent"] == 0
        assert block["tool_names"] == []


class TestTurnMetricsReachTheFile:
    def test_turn_metrics_are_attached_before_the_trace_is_written(
        self, tmp_path, monkeypatch
    ):
        """Pre-#3774 the record landed on the dict after the file was written."""
        monkeypatch.setenv("GAIA_TURN_LOG", str(tmp_path / "turns.jsonl"))
        _register_tools()
        agent = _make_agent(tmp_path)

        written = _run_trace(agent)

        assert written["turn_metrics"]["schema"] == "gaia.turn/1"
        assert (
            written["turn_metrics"]["prompt"]["tools_sent"]
            == written["tool_schema"]["tools_sent"]
        )


class TestSchemaTextCanBeDropped:
    def test_omission_is_visible_never_silent(self, tmp_path, monkeypatch):
        """Opting out of the bulky text leaves a marker, not a missing key."""
        monkeypatch.setenv("GAIA_TRACE_TOOL_SCHEMA", "0")
        _register_tools()
        agent = _make_agent(tmp_path)

        block = _run_trace(agent)["tool_schema"]

        assert "schemas" not in block
        assert "GAIA_TRACE_TOOL_SCHEMA" in block["schemas_omitted"]
        # Names and sizes survive the opt-out — that is the whole point.
        assert {"alpha_tool", "beta_tool"} <= set(block["tool_names"])
        assert block["schema_chars"] > 0

    def test_invalid_opt_out_value_fails_loudly(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAIA_TRACE_TOOL_SCHEMA", "maybe")
        _register_tools()
        agent = _make_agent(tmp_path)

        with pytest.raises(ValueError, match="GAIA_TRACE_TOOL_SCHEMA"):
            _run_trace(agent)


class TestNonNativeModel:
    def test_prompt_text_path_says_so(self, tmp_path, monkeypatch):
        """No ``tools=`` went out, and the artifact says that rather than lying."""
        _register_tools()
        agent = _make_agent(tmp_path)
        monkeypatch.setattr(agent, "_uses_native_tool_calls", lambda: False)

        block = _run_trace(agent)["tool_schema"]

        assert block["sent"] is False
        assert block["tools_sent"] == 0
        assert block["render"] == "prompt_text"
