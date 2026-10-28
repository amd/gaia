# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""What GAIA learns about a gateway model it cannot read from the catalog.

The AMD gateway labels no model ``tool-calling``. Taken at face value, that
kept capable models on the in-text tool format.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from gaia.llm import lemonade_client as lc

CLOUD = {
    "data": [
        {
            "id": "amd.Opus",
            "recipe": lc.CLOUD_RECIPE,
            "labels": ["cloud", "chat"],
            "context_length": 4096,
        },
        {
            "id": "amd.Flash",
            "recipe": lc.CLOUD_RECIPE,
            "labels": ["cloud", "chat"],
            "context_length": 1000000,
            "max_context_window": 1000000,
        },
        {
            "id": "amd.Labelled",
            "recipe": lc.CLOUD_RECIPE,
            "labels": ["cloud", "tool-calling"],
        },
    ]
}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_GATEWAY_FILE", str(tmp_path / "gateway.json"))
    monkeypatch.setattr(
        "gaia.llm.gateway.GATEWAY_STATE_FILE", tmp_path / "gateway.json"
    )
    monkeypatch.setattr(lc, "_CLOUD_TOOL_CALLING", {})
    monkeypatch.setattr(lc, "_TOOL_CALLING_LOADED", False)
    previous = lc._CLOUD_MODELS
    lc.record_cloud_models(CLOUD)
    yield tmp_path / "gateway.json"
    lc._CLOUD_MODELS = previous


def _client(message, finish_reason="stop"):
    client = MagicMock()
    client.chat_completions.return_value = {
        "choices": [{"message": message, "finish_reason": finish_reason}]
    }
    return client


CALLED = {"tool_calls": [{"function": {"name": "report_ready", "arguments": "{}"}}]}


def test_an_unlabelled_cloud_model_is_not_assumed_native():
    assert lc.is_tool_calling_model("amd.Opus") is False


def test_a_label_is_authoritative_without_a_probe():
    assert lc.is_tool_calling_model("amd.Labelled") is True


def test_a_probe_that_gets_the_call_makes_the_model_native_and_persists(isolated):
    assert lc.probe_cloud_tool_calling(_client(CALLED), "amd.Opus") is True
    assert lc.is_tool_calling_model("amd.Opus") is True
    saved = json.loads(isolated.read_text())
    assert saved["tool_calling_models"] == ["amd.Opus"]


def test_a_probe_answered_in_text_records_no_tool_calling(isolated):
    text = {"content": '<invoke name="report_ready">...</invoke>'}
    assert lc.probe_cloud_tool_calling(_client(text), "amd.Opus") is False
    assert lc.is_tool_calling_model("amd.Opus") is False
    assert json.loads(isolated.read_text())["no_tool_calling_models"] == ["amd.Opus"]


def test_a_call_to_some_other_tool_is_not_support():
    other = {"tool_calls": [{"function": {"name": "rm_rf", "arguments": "{}"}}]}
    assert lc.probe_cloud_tool_calling(_client(other), "amd.Opus") is False


def test_a_network_failure_is_raised_never_recorded_as_no(isolated):
    client = MagicMock()
    client.chat_completions.side_effect = lc.LemonadeClientError("connection refused")
    with pytest.raises(lc.LemonadeClientError):
        lc.probe_cloud_tool_calling(client, "amd.Opus")
    assert lc.learned_tool_calling("amd.Opus") is None
    assert not isolated.exists()


def test_what_was_learned_survives_a_new_process(isolated):
    lc.probe_cloud_tool_calling(_client(CALLED), "amd.Opus")
    lc._CLOUD_TOOL_CALLING.clear()
    lc._TOOL_CALLING_LOADED = False
    assert lc.is_tool_calling_model("amd.Opus") is True


def test_the_manager_probes_once_and_tolerates_a_failed_probe():
    from gaia.llm.lemonade_manager import LemonadeManager

    with patch("gaia.llm.lemonade_client.probe_cloud_tool_calling") as probe:
        LemonadeManager.learn_tool_calling(MagicMock(), "amd.Labelled")
        probe.assert_not_called()  # the label already says
        lc.record_tool_calling("amd.Flash", True)
        LemonadeManager.learn_tool_calling(MagicMock(), "amd.Flash")
        probe.assert_not_called()  # already learned
        probe.side_effect = lc.LemonadeClientError("down")
        LemonadeManager.learn_tool_calling(MagicMock(), "amd.Opus")  # logs, no raise
        probe.assert_called_once()


def test_the_probe_request_is_a_valid_tool_request():
    client = _client(CALLED)
    lc.probe_cloud_tool_calling(client, "amd.Opus")
    kwargs = client.chat_completions.call_args.kwargs
    tool = kwargs["tools"][0]
    assert tool["type"] == "function" and tool["function"]["name"] == "report_ready"
    assert tool["function"]["parameters"]["required"] == ["ready"]
    assert kwargs["stream"] is False and kwargs["auto_download"] is False


@pytest.mark.parametrize(
    "message, finish",
    [
        ({"content": "Let me think about which tool"}, "length"),  # thinking ran out
        ({"content": ""}, "stop"),
        ({}, None),
    ],
)
def test_an_inconclusive_probe_records_nothing(isolated, message, finish):
    assert lc.probe_cloud_tool_calling(_client(message, finish), "amd.Opus") is None
    assert lc.learned_tool_calling("amd.Opus") is None
    assert not isolated.exists()


def test_the_probe_cannot_hang_a_waiting_caller():
    client = _client(CALLED)
    lc.probe_cloud_tool_calling(client, "amd.Opus")
    assert client.chat_completions.call_args.kwargs["timeout"] == lc.PROBE_TIMEOUT_S


@pytest.mark.parametrize(
    "message, finish",
    [
        ({"content": "I can't help with that."}, "content_filter"),
        ({"content": "", "tool_calls": []}, "tool_calls"),  # claims a call, has none
        ({"content": "Sure."}, None),  # no finish reason at all
        ({"content": None}, "stop"),
    ],
)
def test_a_reply_that_is_neither_a_call_nor_a_clean_stop_records_nothing(
    isolated, message, finish
):
    assert lc.probe_cloud_tool_calling(_client(message, finish), "amd.Opus") is None
    assert lc.learned_tool_calling("amd.Opus") is None
    assert lc.is_tool_calling_model("amd.Opus") is False
    assert not isolated.exists()


def test_a_gateway_file_from_an_older_gaia_loads_and_keeps_its_fields(isolated):
    from gaia.llm.gateway import GatewayState

    isolated.write_text(
        json.dumps(
            {
                "base_url": "https://gw.example/v1",
                "enabled_models": ["amd.Opus"],
                "active_model": "amd.Opus",
                "non_streaming_models": ["amd.Flash"],
            }
        ),
        encoding="utf-8",
    )
    state = GatewayState.load()
    assert state.tool_calling_models == [] and state.no_tool_calling_models == []
    assert lc.learned_tool_calling("amd.Opus") is None

    lc.probe_cloud_tool_calling(_client(CALLED), "amd.Opus")
    saved = json.loads(isolated.read_text())
    assert saved["tool_calling_models"] == ["amd.Opus"]
    assert saved["non_streaming_models"] == ["amd.Flash"]
    assert saved["active_model"] == "amd.Opus"
