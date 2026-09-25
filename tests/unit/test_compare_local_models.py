# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/compare_local_models.py: the numbers that decide the Strix Halo default."""

import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest

from gaia.llm.lemonade_client import LemonadeClientError

_PATH = Path(__file__).resolve().parents[2] / "util" / "compare_local_models.py"
_spec = importlib.util.spec_from_file_location("compare_local_models", _PATH)
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)


def _tool_response(name, args, as_string=True):
    return {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": name,
                                "arguments": json.dumps(args) if as_string else args,
                            }
                        }
                    ]
                }
            }
        ]
    }


def _right_answers():
    return [_tool_response(tool, args) for _, tool, args in cm.TOOL_CASES]


class TestToolCalls:
    def test_correct_calls_all_pass(self):
        client = mock.Mock()
        client.chat_completions.side_effect = _right_answers()
        result = cm.ModelResult(model="m")
        cm.check_tool_calls(client, "m", result)

        assert result.tool_calls_passed == result.tool_calls_total == len(cm.TOOL_CASES)
        # Every request offers every tool, as the agent loop does.
        assert all(
            c.kwargs["tools"] == cm.TOOLS for c in client.chat_completions.mock_calls
        )

    def test_argument_case_and_whitespace_do_not_matter(self):
        answers = _right_answers()
        answers[0] = _tool_response(
            "get_weather", {"city": " TORONTO", "unit": "Celsius"}
        )
        client = mock.Mock()
        client.chat_completions.side_effect = answers
        result = cm.ModelResult(model="m")
        cm.check_tool_calls(client, "m", result)

        assert result.tool_calls_passed == len(cm.TOOL_CASES)

    def test_wrong_tool_wrong_value_prose_and_bad_json_fail(self):
        answers = _right_answers()
        answers[0] = _tool_response("create_event", {"title": "x", "date": "y"})
        answers[1] = _tool_response(
            "create_event",
            {"title": "Design review", "date": "2026-10-03", "duration_minutes": 45},
        )
        answers[2] = {"choices": [{"message": {"content": "Sure, searching now."}}]}
        answers[3] = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"function": {"name": "get_weather", "arguments": "{oops"}}
                        ]
                    }
                }
            ]
        }
        client = mock.Mock()
        client.chat_completions.side_effect = answers
        result = cm.ModelResult(model="m")
        cm.check_tool_calls(client, "m", result)

        assert result.tool_calls_passed == 0
        assert len(result.tool_failures) == 4

    def test_arguments_given_as_an_object_are_accepted(self):
        answers = [
            _tool_response(tool, args, as_string=False)
            for _, tool, args in cm.TOOL_CASES
        ]
        client = mock.Mock()
        client.chat_completions.side_effect = answers
        result = cm.ModelResult(model="m")
        cm.check_tool_calls(client, "m", result)

        assert result.tool_calls_passed == len(cm.TOOL_CASES)


class TestFiller:
    def test_filler_is_never_a_cache_hit(self):
        assert cm._filler(1000).splitlines()[0] != cm._filler(1000).splitlines()[0]

    def test_filler_scales_with_the_requested_size(self):
        assert len(cm._filler(32_000)) > 3 * len(cm._filler(8_000))


def _answer(**kwargs):
    """A model that answers every tool request correctly, and prose otherwise."""
    content = kwargs["messages"][0]["content"]
    for request, tool, args in cm.TOOL_CASES:
        if content == request:
            return _tool_response(tool, args)
    return {"choices": [{"message": {"content": "ok"}}]}


def _client(memory_gb=96.0):
    client = mock.Mock()
    client.base_url = "http://localhost:1/api/v1"
    client.health_check.return_value = {"version": "2026.39.1"}
    client.get_system_info.return_value = {
        "devices": {
            "amd_gpu": [
                {
                    "available": True,
                    "integrated": True,
                    "vram_gb": memory_gb,
                    "virtual_mem_gb": 0.0,
                }
            ]
        },
        "model_storage": {"free_bytes": 900e9},
    }
    client.chat_completions.side_effect = _answer
    client.get_stats.return_value = {
        "input_tokens": 8000,
        "time_to_first_token": 4.0,
        "tokens_per_second": 50.0,
    }
    return client


class TestCompare:
    def test_a_model_that_does_not_fit_is_never_downloaded(self, tmp_path):
        client = _client(memory_gb=16.0)
        with mock.patch.object(cm, "LemonadeClient", return_value=client):
            results = cm.compare(
                [cm.LARGE_DEFAULT_MODEL_NAME], 65536, None, False, tmp_path
            )

        assert results[0].fits is False
        client.ensure_model_downloaded.assert_not_called()
        client.load_model.assert_not_called()

    def test_measures_each_model_then_unloads_it(self, tmp_path):
        client = _client()
        with mock.patch.object(cm, "LemonadeClient", return_value=client):
            results = cm.compare(
                [cm.QWEN3_30B_MODEL_NAME], 65536, None, False, tmp_path
            )

        r = results[0]
        assert [s.label for s in r.speed] == ["short", "8K", "32K"]
        assert r.speed[1].prompt_tps == 2000.0 and r.speed[1].decode_tps == 50.0
        client.load_model.assert_called_once()
        assert client.load_model.call_args.kwargs["ctx_size"] == 65536
        client.unload_model.assert_called_once()
        # A built-in: no registration fields, which Lemonade would 400.
        assert client.ensure_model_downloaded.call_args.kwargs == {"timeout": 7200 * 4}

    def test_unreachable_server_names_gaia_init(self, tmp_path):
        client = _client()
        client.health_check.side_effect = LemonadeClientError("refused")
        with mock.patch.object(cm, "LemonadeClient", return_value=client):
            with pytest.raises(SystemExit, match="gaia init"):
                cm.compare([cm.QWEN3_30B_MODEL_NAME], 65536, None, False, tmp_path)


class TestExitCode:
    def test_a_failed_request_fails_the_run(self, tmp_path):
        client = _client()
        client.get_stats.side_effect = LemonadeClientError("Compute error.")
        with mock.patch.object(cm, "LemonadeClient", return_value=client):
            code = cm.main(
                ["--models", cm.QWEN3_30B_MODEL_NAME, "--out", str(tmp_path)]
            )

        assert code == 1
        assert "Compute error." in (tmp_path / "results.md").read_text(encoding="utf-8")

    def test_a_clean_run_passes(self, tmp_path):
        client = _client()
        with mock.patch.object(cm, "LemonadeClient", return_value=client):
            code = cm.main(
                ["--models", cm.QWEN3_30B_MODEL_NAME, "--out", str(tmp_path)]
            )

        assert code == 0
        results = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
        assert results[0]["tool_calls_passed"] == len(cm.TOOL_CASES)
