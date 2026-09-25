# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-scenario performance aggregation: min/max beside the averages, and tool
calls even when the backend reports no inference stats.

An average hides the slow turn, and the slow turn is the one a user notices.
Tool calls come from the judge's observed `agent_tools`, which exists whether or
not stats do — so a no-stats backend must still report tool usage rather than
dropping the scenario's numbers entirely.
"""

import pytest

from gaia.eval.runner import _aggregate_performance
from gaia.mcp.servers.agent_ui_mcp import _model_pin


def _turn(tps=None, ttft=None, inp=None, out=None, tools=()):
    return {
        "agent_tools": list(tools),
        "performance": {
            "tokens_per_second": tps,
            "time_to_first_token": ttft,
            "input_tokens": inp,
            "output_tokens": out,
            "flags": [] if tps else ["no_stats"],
        },
    }


class TestPerformanceSpread:
    def test_min_max_reported_beside_average(self):
        result = {
            "turns": [
                _turn(tps=40.0, ttft=1.0, inp=100, out=10),
                _turn(tps=20.0, ttft=3.0, inp=100, out=10),
                _turn(tps=30.0, ttft=2.0, inp=100, out=10),
            ]
        }
        _aggregate_performance(result, "s")
        ps = result["performance_summary"]
        assert ps["avg_tokens_per_second"] == 30.0
        assert (ps["min_tokens_per_second"], ps["max_tokens_per_second"]) == (
            20.0,
            40.0,
        )
        assert ps["avg_time_to_first_token"] == 2.0
        assert (ps["min_time_to_first_token"], ps["max_time_to_first_token"]) == (
            1.0,
            3.0,
        )
        assert ps["turns_measured"] == 3

    def test_tool_calls_counted_from_agent_tools(self):
        result = {
            "turns": [
                _turn(tps=10.0, tools=["read_file", "query_documents"]),
                _turn(tps=10.0, tools=["read_file"]),
            ]
        }
        _aggregate_performance(result, "s")
        assert result["tool_usage"]["total_tool_calls"] == 3
        assert result["tool_usage"]["max_tool_calls_in_a_turn"] == 2


class TestNoStatsBackend:
    def test_tool_calls_survive_when_no_inference_stats(self):
        """The Claude path reports no_stats — tool usage must not vanish with it."""
        result = {"turns": [_turn(tools=["search_web"]), _turn(tools=[])]}
        _aggregate_performance(result, "s")
        assert result["tool_usage"]["total_tool_calls"] == 1
        # performance_summary stays None: scorecard.py counts any dict here
        # toward scenarios_with_data, so a stats-less summary would overstate
        # how many scenarios were actually measured.
        assert result["performance_summary"] is None

    def test_no_turns_reports_nothing(self):
        result = {"turns": []}
        _aggregate_performance(result, "s")
        assert result["performance_summary"] is None
        assert "tool_usage" not in result


class TestEvalModelPin:
    """CI must evaluate Gemma. A regression here would silently evaluate
    whatever model Lemonade happened to have loaded."""

    def test_unset_provider_pins_the_lemonade_default(self, monkeypatch):
        from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

        monkeypatch.delenv("GAIA_EVAL_AGENT_PROVIDER", raising=False)
        assert _model_pin() == {"model": DEFAULT_MODEL_NAME}

    def test_claude_provider_sends_no_lemonade_pin(self, monkeypatch):
        monkeypatch.setenv("GAIA_EVAL_AGENT_PROVIDER", "claude")
        assert _model_pin() == {}

    def test_unknown_provider_refuses_to_guess(self, monkeypatch):
        monkeypatch.setenv("GAIA_EVAL_AGENT_PROVIDER", "ollama")
        with pytest.raises(ValueError, match="not a supported eval agent provider"):
            _model_pin()
