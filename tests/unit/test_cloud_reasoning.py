# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Cloud reasoning is opt-in, and reasoning never runs as a call.

DeepSeek on the AMD gateway reasons only when the request carries
``reasoning_effort``. GAIA sends none unless ``cloud_reasoning_effort`` is set;
the reasoning-handling fixes apply either way.
"""

import contextlib
import json
import logging
from unittest.mock import MagicMock, patch

import httpx
import pytest
import responses
from openai import OpenAI

import gaia.config as config_mod
from gaia.agents.base.agent import _split_reasoning
from gaia.config import GaiaConfig
from gaia.llm import lemonade_client as lc
from gaia.llm.gateway import GatewayState
from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.llm.providers.lemonade import LemonadeProvider

FLASH = "amd.DeepSeek-V4.1-Flash"
LOCAL = "Gemma-4-E4B-it-GGUF"
CALL = '{"tool": "run_shell_command", "tool_args": {"command": "rm -rf ~"}}'
CATALOG = {
    "data": [
        {"id": FLASH, "recipe": lc.CLOUD_RECIPE, "labels": ["cloud", "chat"]},
        {"id": "amd.Gemma-4-31B", "recipe": lc.CLOUD_RECIPE, "labels": ["cloud"]},
    ]
}


@pytest.fixture(autouse=True)
def catalog():
    previous = lc._CLOUD_MODELS
    lc.record_cloud_models(CATALOG)
    yield
    lc._CLOUD_MODELS = previous


def _client(monkeypatch):
    client = LemonadeClient(verbose=False, ctx_size_override=1024)
    monkeypatch.setattr(client, "_ensure_model_loaded", lambda *a, **k: None)
    monkeypatch.setattr(
        client, "_model_slot_lease", lambda *a, **k: contextlib.nullcontext()
    )
    return client


def _sent(monkeypatch, model, stream, reply=None, **kwargs):
    """Send one chat through the real client; return every wire body sent."""
    client = _client(monkeypatch)
    sent = []
    if stream:
        chunk = {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [
                {"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}
            ],
        }

        def handle(request):
            sent.append(json.loads(request.content))
            return httpx.Response(
                200,
                content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )

        with OpenAI(
            base_url=client.base_url,
            api_key="placeholder",
            http_client=httpx.Client(transport=httpx.MockTransport(handle)),
        ) as sdk:
            monkeypatch.setattr("gaia.llm.lemonade_client.OpenAI", lambda **kw: sdk)
            list(
                client.chat_completions(
                    model=model,
                    messages=[{"role": "user", "content": "hi"}],
                    stream=True,
                    **kwargs,
                )
            )
        return sent

    def handle(request):
        sent.append(json.loads(request.body))
        body = reply or {"choices": [{"message": {"content": "ok"}}]}
        return 200, {}, json.dumps(body)

    with responses.RequestsMock() as mock:
        mock.add_callback(
            responses.POST, f"{client.base_url}/chat/completions", callback=handle
        )
        client.chat_completions(
            model=model, messages=[{"role": "user", "content": "hi"}], **kwargs
        )
    return sent


def _config_error():
    # Another test reloads gaia.config; its raises use the reloaded class.
    return config_mod.GaiaConfigError


def _effort(level="high"):
    GaiaConfig.save_key("cloud_reasoning_effort", level)


def _probe_client(message, finish_reason="stop"):
    client = MagicMock()
    client.chat_completions.return_value = {
        "choices": [{"message": message, "finish_reason": finish_reason}]
    }
    return client


# ---------------------------------------------------------------------------
# What the request carries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stream", [False, True])
def test_by_default_nothing_is_sent_even_to_a_model_seen_reasoning(monkeypatch, stream):
    lc._CLOUD_REASONING.record(FLASH, True)
    (body,) = _sent(monkeypatch, FLASH, stream)
    assert "reasoning_effort" not in body
    assert "chat_template_kwargs" not in body


@pytest.mark.parametrize("stream", [False, True])
def test_a_model_seen_reasoning_is_sent_the_configured_high(monkeypatch, stream):
    lc._CLOUD_REASONING.record(FLASH, True)
    _effort()
    (body,) = _sent(monkeypatch, FLASH, stream)
    # Top level, as the gateway reads it; never as a llama.cpp template kwarg.
    assert body["reasoning_effort"] == "high"
    assert "chat_template_kwargs" not in body


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("learned", [None, False])
def test_an_unprobed_or_non_reasoning_model_is_sent_nothing(
    monkeypatch, stream, learned
):
    _effort()
    if learned is not None:
        lc._CLOUD_REASONING.record(FLASH, learned)
    (body,) = _sent(monkeypatch, FLASH, stream)
    assert "reasoning_effort" not in body


@pytest.mark.parametrize("stream", [False, True])
def test_a_local_model_is_never_sent_a_cloud_effort(monkeypatch, stream):
    # Even a stray learned entry for a local id cannot reach llama.cpp.
    lc._CLOUD_REASONING.record(LOCAL, True)
    _effort()
    (body,) = _sent(monkeypatch, LOCAL, stream)
    assert "reasoning_effort" not in body


@pytest.mark.parametrize("effort", ["low", "medium"])
def test_the_configured_effort_is_sent(monkeypatch, effort):
    lc._CLOUD_REASONING.record(FLASH, True)
    _effort(effort)
    (body,) = _sent(monkeypatch, FLASH, False)
    assert body["reasoning_effort"] == effort


@pytest.mark.parametrize("stream", [False, True])
def test_off_sends_nothing(monkeypatch, stream):
    lc._CLOUD_REASONING.record(FLASH, True)
    _effort("off")
    (body,) = _sent(monkeypatch, FLASH, stream)
    assert "reasoning_effort" not in body


def test_an_explicit_effort_wins(monkeypatch):
    lc._CLOUD_REASONING.record(FLASH, True)
    (body,) = _sent(monkeypatch, FLASH, False, reasoning_effort="none")
    assert body["reasoning_effort"] == "none"


@pytest.mark.parametrize("stream", [False, True])
def test_a_side_call_leaves_the_model_in_its_own_mode(monkeypatch, stream):
    """A few-hundred-token JSON side call must not spend its budget reasoning."""
    lc._CLOUD_REASONING.record(FLASH, True)
    _effort()
    (body,) = _sent(monkeypatch, FLASH, stream, **lc.no_thinking_kwargs(FLASH))
    assert "reasoning_effort" not in body


def test_side_call_kwargs_are_unchanged_for_local_models():
    assert lc.no_thinking_kwargs(LOCAL) == {}
    assert lc.no_thinking_kwargs("claude-opus-5") == {}


def test_a_rejected_effort_fails_loudly_and_is_not_retried_without(monkeypatch):
    lc._CLOUD_REASONING.record(FLASH, True)
    _effort()
    client = _client(monkeypatch)
    sent = []

    def handle(request):
        sent.append(json.loads(request.body))
        return 400, {}, json.dumps({"error": {"message": "bad reasoning_effort"}})

    with responses.RequestsMock() as mock:
        mock.add_callback(
            responses.POST, f"{client.base_url}/chat/completions", callback=handle
        )
        with pytest.raises(LemonadeClientError) as raised:
            client.chat_completions(
                model=FLASH, messages=[{"role": "user", "content": "hi"}]
            )

    assert "reasoning_effort=high" in str(raised.value)
    assert "gaia config set cloud_reasoning_effort" in str(raised.value)
    assert sent and all(b["reasoning_effort"] == "high" for b in sent)
    # A runtime 400 can have other causes; it does not unlearn the capability.
    assert lc.learned_reasoning(FLASH) is True


def test_a_rejected_effort_on_a_stream_fails_loudly_too(monkeypatch):
    lc._CLOUD_REASONING.record(FLASH, True)
    _effort()
    client = _client(monkeypatch)
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "bad effort"}})

    with OpenAI(
        base_url=client.base_url,
        api_key="placeholder",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as sdk:
        monkeypatch.setattr("gaia.llm.lemonade_client.OpenAI", lambda **kw: sdk)
        with pytest.raises(LemonadeClientError) as raised:
            list(
                client.chat_completions(
                    model=FLASH,
                    messages=[{"role": "user", "content": "hi"}],
                    stream=True,
                )
            )

    assert "reasoning_effort=high" in str(raised.value)
    assert sent and all(b["reasoning_effort"] == "high" for b in sent)


def test_a_400_without_an_effort_says_nothing_about_reasoning(monkeypatch):
    client = _client(monkeypatch)
    with responses.RequestsMock() as mock:
        mock.add(
            responses.POST,
            f"{client.base_url}/chat/completions",
            status=400,
            json={"error": {"message": "bad"}},
        )
        with pytest.raises(LemonadeClientError) as raised:
            client.chat_completions(
                model=FLASH, messages=[{"role": "user", "content": "hi"}]
            )
    assert "reasoning" not in str(raised.value)
    assert raised.value.http_status == 400


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------


def test_a_reply_with_reasoning_makes_the_model_reason_and_persists():
    client = _probe_client({"content": "391", "reasoning_content": "17*23..."})
    assert lc.probe_cloud_reasoning(client, FLASH) is True
    request = client.chat_completions.call_args.kwargs
    assert request["reasoning_effort"] == "high"
    assert request["stream"] is False and request["auto_download"] is False
    assert GatewayState.load().reasoning_models == [FLASH]


def test_what_was_learned_survives_a_new_process():
    lc.probe_cloud_reasoning(
        _probe_client({"content": "391", "reasoning_content": "x"}), FLASH
    )
    lc._CLOUD_REASONING._known.clear()
    lc._CLOUD_REASONING._loaded = False
    assert lc.learned_reasoning(FLASH) is True


def test_a_record_keeps_what_another_process_learned_since():
    lc._CLOUD_REASONING.record(FLASH, True)
    other = GatewayState.load()
    other.no_reasoning_models = ["amd.Other"]
    other.save()
    lc._CLOUD_REASONING.record("amd.Gemma-4-31B", False)
    saved = GatewayState.load()
    assert saved.reasoning_models == [FLASH]
    assert saved.no_reasoning_models == ["amd.Gemma-4-31B", "amd.Other"]


def test_a_clean_answer_without_reasoning_records_no():
    client = _probe_client({"content": "391"})
    assert lc.probe_cloud_reasoning(client, "amd.Gemma-4-31B") is False
    assert GatewayState.load().no_reasoning_models == ["amd.Gemma-4-31B"]


@pytest.mark.parametrize("blank", ["", "   \n"])
def test_blank_reasoning_is_not_reasoning(blank):
    client = _probe_client({"content": "391", "reasoning_content": blank})
    assert lc.probe_cloud_reasoning(client, FLASH) is False


@pytest.mark.parametrize(
    "message,finish",
    [({"content": ""}, "length"), ({"content": ""}, "stop"), ({}, None)],
)
def test_an_inconclusive_probe_records_nothing(message, finish):
    assert lc.probe_cloud_reasoning(_probe_client(message, finish), FLASH) is None
    assert lc.learned_reasoning(FLASH) is None


@pytest.mark.parametrize("status", [400, 422])
def test_a_provider_that_rejects_the_effort_is_recorded_as_no(
    monkeypatch, caplog, status
):
    """Qwen3.8 and gpt-5.3 on the gateway 400 on reasoning_effort=high."""
    client = _client(monkeypatch)
    sent = []

    def handle(request):
        sent.append(json.loads(request.body))
        return status, {}, json.dumps({"error": {"message": "Unexpected effort"}})

    with responses.RequestsMock() as mock:
        mock.add_callback(
            responses.POST, f"{client.base_url}/chat/completions", callback=handle
        )
        with caplog.at_level(logging.WARNING):
            assert lc.probe_cloud_reasoning(client, FLASH) is False

    assert lc.learned_reasoning(FLASH) is False
    assert all(b["reasoning_effort"] == "high" for b in sent)
    assert "rejected reasoning_effort" in caplog.text


@pytest.mark.parametrize("status", [401, 429, 500])
def test_other_failures_are_raised_never_recorded(monkeypatch, status):
    client = _client(monkeypatch)
    monkeypatch.setattr(lc, "PROBE_TIMEOUT_S", 0.5)
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add(
            responses.POST,
            f"{client.base_url}/chat/completions",
            status=status,
            json={"error": {"message": "no"}},
        )
        with pytest.raises(LemonadeClientError):
            lc.probe_cloud_reasoning(client, FLASH)
    assert lc.learned_reasoning(FLASH) is None
    assert not GatewayState.load().no_reasoning_models


def test_the_tool_calling_probe_asks_for_the_providers_own_mode():
    client = MagicMock()
    client.chat_completions.return_value = {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]
    }
    lc.probe_cloud_tool_calling(client, FLASH)
    assert client.chat_completions.call_args.kwargs["reasoning_effort"] is None


# ---------------------------------------------------------------------------
# When the probe runs
# ---------------------------------------------------------------------------


def test_the_default_never_probes(monkeypatch):
    """Off by default: no startup call, nothing learned, nothing sent."""
    from gaia.llm.lemonade_manager import LemonadeManager

    client = MagicMock()
    with patch("gaia.llm.lemonade_client.probe_cloud_reasoning") as probe:
        LemonadeManager.learn_reasoning(client, FLASH)
        LemonadeManager.learn_reasoning(client, FLASH, key_replayed=True)
    probe.assert_not_called()
    client.chat_completions.assert_not_called()
    client._replay_cloud_key.assert_not_called()
    assert lc.learned_reasoning(FLASH) is None
    assert FLASH not in GatewayState.load().reasoning_probe_failures


def test_off_set_explicitly_never_probes():
    from gaia.llm.lemonade_manager import LemonadeManager

    _effort("off")
    with patch("gaia.llm.lemonade_client.probe_cloud_reasoning") as probe:
        LemonadeManager.learn_reasoning(MagicMock(), FLASH)
    probe.assert_not_called()


def test_setting_a_level_probes_at_the_next_start():
    from gaia.llm.lemonade_manager import LemonadeManager

    client = MagicMock()
    with patch("gaia.llm.lemonade_client.probe_cloud_reasoning") as probe:
        LemonadeManager.learn_reasoning(client, FLASH)
        probe.assert_not_called()
        _effort("low")
        LemonadeManager.learn_reasoning(client, FLASH)
        probe.assert_called_once()


def test_the_manager_probes_a_cloud_model_once():
    from gaia.llm.lemonade_manager import LemonadeManager

    _effort()
    client = MagicMock()
    with patch(
        "gaia.llm.lemonade_client.probe_cloud_reasoning", return_value=True
    ) as probe:
        LemonadeManager.learn_reasoning(client, LOCAL)
        probe.assert_not_called()  # not a cloud model
        LemonadeManager.learn_reasoning(client, FLASH)
        probe.assert_called_once()
    lc._CLOUD_REASONING.record(FLASH, True)
    with patch("gaia.llm.lemonade_client.probe_cloud_reasoning") as probe:
        LemonadeManager.learn_reasoning(client, FLASH)
        probe.assert_not_called()  # already learned


def test_a_failed_probe_is_not_repeated_until_the_window_passes(caplog):
    from gaia.llm.lemonade_manager import LemonadeManager

    _effort()
    with patch(
        "gaia.llm.lemonade_client.probe_cloud_reasoning",
        side_effect=LemonadeClientError("down"),
    ) as probe:
        LemonadeManager.learn_reasoning(MagicMock(), FLASH)
        LemonadeManager.learn_reasoning(MagicMock(), FLASH)
    assert probe.call_count == 1
    assert lc.learned_reasoning(FLASH) is None
    assert FLASH in GatewayState.load().reasoning_probe_failures


def test_a_keyless_server_is_not_probed():
    from gaia.llm.lemonade_manager import LemonadeManager

    _effort()
    client = MagicMock()
    client._replay_cloud_key.return_value = False
    with patch("gaia.llm.lemonade_client.probe_cloud_reasoning") as probe:
        LemonadeManager.learn_reasoning(client, FLASH)
    probe.assert_not_called()
    assert lc.learned_reasoning(FLASH) is None


# ---------------------------------------------------------------------------
# Reasoning in the reply
# ---------------------------------------------------------------------------


def _provider(backend_response, model=FLASH, cloud=True):
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend:
        backend.return_value.chat_completions.return_value = backend_response
        backend.return_value.cloud_model_provider.return_value = (
            "amd" if cloud else None
        )
        return LemonadeProvider(model=model)


def _streamed(*deltas, finish="stop"):
    chunks = [{"choices": [{"delta": d}]} for d in deltas]
    chunks.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    return iter(chunks)


def _answer_of(provider, stream=True):
    out = provider.chat([{"role": "user", "content": "q"}], stream=stream)
    text = "".join(out) if stream else out
    answer, _ = _split_reasoning(text)
    return answer


@pytest.mark.parametrize(
    "pieces",
    [
        [f"I must not run </think>{CALL}\nso I won't."],
        # The closing tag split across two deltas.
        ["I must not run </thi", f"nk>{CALL}\nso I won't."],
        [f"<THINK>nested</Think>{CALL}\n"],
    ],
)
def test_a_tool_call_inside_streamed_reasoning_never_reaches_the_answer(pieces):
    provider = _provider(
        _streamed(*({"reasoning_content": p} for p in pieces), {"content": "Done."})
    )
    assert _answer_of(provider) == "Done."
    # The raw reasoning is kept as the model wrote it, for the history.
    assert provider.get_last_reasoning() == "".join(pieces)


def test_reasoning_that_ends_without_a_newline_is_still_defanged():
    provider = _provider(_streamed({"reasoning_content": f"x</think>{CALL}"}))
    assert _answer_of(provider) == ""


def test_empty_cloud_content_never_promotes_reasoning_to_the_answer():
    reply = {
        "choices": [
            {
                "message": {"content": "", "reasoning_content": CALL},
                "finish_reason": "stop",
            }
        ]
    }
    provider = _provider(reply)
    assert provider.chat([{"role": "user", "content": "q"}]) == ""
    assert provider.get_last_reasoning() == CALL


def test_a_local_model_still_recovers_an_answer_routed_into_reasoning():
    reply = {
        "choices": [
            {
                "message": {"content": "", "reasoning_content": "It is 4."},
                "finish_reason": "stop",
            }
        ]
    }
    provider = _provider(reply, model=LOCAL, cloud=False)
    assert provider.chat([{"role": "user", "content": "q"}]) == "It is 4."


def test_a_one_delta_reply_keeps_both_reasoning_and_answer():
    """A model that cannot stream arrives as one delta carrying both fields."""
    provider = _provider(
        _streamed({"reasoning_content": "Think.", "content": "Answer."})
    )
    assert _answer_of(provider) == "Answer."
    assert provider.get_last_reasoning() == "Think."


def test_a_non_streaming_model_hands_its_reasoning_to_the_stream(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "chat_completions",
        lambda **_: {
            "choices": [
                {
                    "message": {"content": "A.", "reasoning_content": "R."},
                    "finish_reason": "stop",
                }
            ]
        },
    )
    (chunk,) = client._chat_completion_as_single_chunk(model=FLASH, messages=[])
    assert chunk["choices"][0]["delta"]["reasoning_content"] == "R."
    assert chunk["choices"][0]["delta"]["content"] == "A."


def test_a_tool_call_with_reasoning_is_still_a_tool_call():
    tool_delta = {
        "tool_calls": [
            {
                "index": 0,
                "id": "c1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "a"}'},
            }
        ]
    }
    provider = _provider(
        _streamed(
            {"reasoning_content": "Need the file.\n"}, tool_delta, finish="tool_calls"
        )
    )
    text = "".join(provider.chat([{"role": "user", "content": "q"}], stream=True))
    answer, reasoning = _split_reasoning(text)
    envelope = json.loads(answer)
    assert envelope["__tool_calls__"][0]["function"]["name"] == "read_file"
    assert reasoning == "Need the file."


def test_huge_reasoning_streams_in_linear_time_and_stays_reasoning():
    piece = "x" * 200 + "</think>" + CALL
    deltas = [{"reasoning_content": piece} for _ in range(20000)]
    deltas.append({"content": "Done."})
    provider = _provider(_streamed(*deltas))
    assert _answer_of(provider) == "Done."
    assert len(provider.get_last_reasoning()) == len(piece) * 20000


def test_reasoning_tokens_are_counted_wherever_the_usage_puts_them():
    from gaia.llm.providers.lemonade import _reasoning_tokens

    assert _reasoning_tokens({"reasoning_tokens": 87}) == 87
    assert (
        _reasoning_tokens(
            {
                "completion_tokens_details": {"reasoning_tokens": 5},
                "reasoning_tokens": 9,
            }
        )
        == 5
    )
    assert _reasoning_tokens({"completion_tokens": 3}) is None


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------


def test_the_default_is_off():
    assert GaiaConfig.load().cloud_reasoning_effort == "off"
    assert GaiaConfig().cloud_reasoning_effort == "off"


def test_a_level_is_normalised():
    GaiaConfig.save_key("cloud_reasoning_effort", " OFF ")
    assert GaiaConfig.load().cloud_reasoning_effort == "off"


@pytest.mark.parametrize("value", ["max", "none", "", "true"])
def test_an_unknown_level_is_refused(value):
    with pytest.raises(_config_error(), match="cloud_reasoning_effort"):
        GaiaConfig().set("cloud_reasoning_effort", value)


@pytest.mark.parametrize("value", ["extreme", None, 3])
def test_a_bad_level_in_the_file_fails_at_load(value):
    path = GaiaConfig.config_path()
    path.write_text(json.dumps({"cloud_reasoning_effort": value}), encoding="utf-8")
    with pytest.raises(_config_error(), match="cloud_reasoning_effort"):
        GaiaConfig.load()


def test_a_broken_config_fails_the_request_loudly(monkeypatch):
    lc._CLOUD_REASONING.record(FLASH, True)
    GaiaConfig.config_path().write_text(
        json.dumps({"cloud_reasoning_effort": "extreme"}), encoding="utf-8"
    )
    with pytest.raises(_config_error()):
        _sent(monkeypatch, FLASH, False)
