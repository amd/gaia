# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A reply that turns into noise is stopped, never shown, and fails the turn.

Gemma 4 on a corrupted state streamed ``<unused49>`` until the output limit,
and every token reached the chat.
"""

import itertools
import logging
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.chat.sdk import AgentResponse
from gaia.llm.degenerate_output import (
    REPEAT_LIMIT,
    DegenerateOutput,
    DegenerateOutputGuard,
)
from gaia.llm.lemonade_client import LemonadeClient, ModelOutputCorruptedError
from gaia.llm.providers.lemonade import (
    LemonadeOutputCorruptedError,
    classify_lemonade_exception,
)

MODEL = "Gemma-4-E4B-it-GGUF"
BASE_URL = "http://127.0.0.1:1/api/v1"
NOISE = "<unused49>"


def _chunk(content=None, reasoning=None, finish_reason=None):
    return {
        "choices": [
            {
                "index": 0,
                "delta": {"content": content, "reasoning_content": reasoning},
                "finish_reason": finish_reason,
            }
        ]
    }


def _run(guard, chunks):
    out = []
    for chunk in chunks:
        out.extend(guard.feed(chunk))
    return out + guard.flush()


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------


def test_a_reserved_token_stops_the_reply_before_it_is_passed_on():
    guard = DegenerateOutputGuard()
    assert guard.feed(_chunk("Hello")) == [_chunk("Hello")]
    with pytest.raises(DegenerateOutput) as caught:
        guard.feed(_chunk(NOISE))
    assert caught.value.token == NOISE


def test_a_reserved_token_in_the_reasoning_stops_it_too():
    with pytest.raises(DegenerateOutput):
        DegenerateOutputGuard().feed(_chunk(reasoning=NOISE + NOISE))


@pytest.mark.parametrize(
    "text",
    [
        "Those registers are unused",
        " unused",
        "unused49",
        "Gemma reserves slots named `<unused49>` in its vocabulary.",
    ],
)
def test_text_that_mentions_unused_passes(text):
    chunks = [_chunk("The "), _chunk(text), _chunk(".", finish_reason="stop")]
    assert _run(DegenerateOutputGuard(), chunks) == chunks


def test_the_same_token_over_and_over_stops_the_reply_and_none_of_it_is_shown():
    guard = DegenerateOutputGuard(repeat_limit=5)
    shown = guard.feed(_chunk("Answer:"))
    with pytest.raises(DegenerateOutput) as caught:
        for _ in range(10):
            shown += guard.feed(_chunk(" ha"))
    assert caught.value.token == " ha"
    assert shown == [_chunk("Answer:"), _chunk(" ha")]


def test_a_short_run_of_one_token_is_passed_on_whole():
    chunks = [_chunk("=")] * (REPEAT_LIMIT - 1) + [_chunk("\n", finish_reason="stop")]
    assert _run(DegenerateOutputGuard(), chunks) == chunks


def test_a_run_that_ends_the_stream_is_flushed():
    chunks = [_chunk("Total:"), _chunk("0"), _chunk("0"), _chunk("0")]
    assert _run(DegenerateOutputGuard(), chunks) == chunks


def test_chunks_without_text_pass_through_untouched():
    role_only = {"choices": [{"index": 0, "delta": {"role": "assistant"}}]}
    tool_call = {
        "choices": [
            {
                "index": 0,
                "delta": {
                    "content": None,
                    "tool_calls": [{"index": 0, "function": {"arguments": "{"}}],
                },
                "finish_reason": None,
            }
        ]
    }
    not_text = _chunk(content=MagicMock(), reasoning=0)
    chunks = [role_only, _chunk(), tool_call, tool_call, not_text, _chunk("Hi")]
    assert _run(DegenerateOutputGuard(repeat_limit=2), chunks) == chunks


# --------------------------------------------------------------------------
# The Lemonade client
# --------------------------------------------------------------------------


class _Choice:
    def __init__(self, content, finish_reason=None):
        self.index = 0
        self.delta = MagicMock()
        self.delta.role = None
        self.delta.content = content
        self.delta.reasoning_content = None
        self.delta.tool_calls = None
        self.finish_reason = finish_reason


class _Chunk:
    def __init__(self, n, content, finish_reason=None):
        self.id = str(n)
        self.created = 1742927481
        self.model = MODEL
        self.choices = [_Choice(content, finish_reason)]
        self.usage = None


class _Stream:
    """An endless server stream that records how far it was read and if it closed.

    The client reads and closes it on its stream worker thread, so the close
    can land after the call has already raised: wait on *closed*.
    """

    def __init__(self, pieces, read_delay=0.0):
        self.pieces = pieces
        self.read_delay = read_delay
        self.read = 0
        self.closed = threading.Event()

    def __iter__(self):
        for n, piece in enumerate(self.pieces):
            if self.read_delay:
                time.sleep(self.read_delay)
            self.read += 1
            yield _Chunk(n, piece)

    def close(self):
        self.closed.set()


@pytest.fixture
def client():
    lc = LemonadeClient(base_url=BASE_URL, model=MODEL, verbose=False)
    lc._ensure_model_loaded = MagicMock()
    lc.unload_model = MagicMock(return_value={"status": "success"})
    return lc


def _serve(stream):
    openai = MagicMock()
    openai.return_value.chat.completions.create.return_value = stream
    return patch("gaia.llm.lemonade_client.OpenAI", openai)


_MESSAGES = [
    {"role": "system", "content": "You are GAIA." * 100},
    {"role": "user", "content": "How many transmon qubits does Sycamore have?"},
]


# A slow read is a busy CI runner: the worker closes the stream after the call ends.
@pytest.mark.parametrize("read_delay", [0.0, 0.01])
def test_an_endless_reserved_token_stream_is_cut_off_and_raises(
    client, caplog, read_delay
):
    stream = _Stream(itertools.chain(["The"], itertools.repeat(NOISE)), read_delay)
    shown = []
    with _serve(stream), caplog.at_level(logging.ERROR, "gaia.llm.lemonade_client"):
        with pytest.raises(ModelOutputCorruptedError) as caught:
            for chunk in client.chat_completions(
                model=MODEL, messages=_MESSAGES, stream=True
            ):
                shown.append(chunk["choices"][0]["delta"]["content"])
    assert shown == ["The"]
    assert stream.closed.wait(timeout=10), "the server stream was never closed"
    read = stream.read
    time.sleep(0.2)
    assert stream.read == read  # the server stream stopped being read
    assert "corrupted" in str(caught.value)
    logged = caplog.text
    assert NOISE in logged
    assert MODEL in logged
    assert "2 messages" in logged
    chars = sum(len(m["content"]) for m in _MESSAGES)
    assert f"{chars} chars" in logged


def test_the_model_is_unloaded_so_the_next_message_gets_a_fresh_one(client):
    with _serve(_Stream(itertools.repeat(NOISE))):
        with pytest.raises(ModelOutputCorruptedError):
            list(client.chat_completions(model=MODEL, messages=_MESSAGES, stream=True))
    client.unload_model.assert_called_once_with(MODEL, ignore_if_not_loaded=True)


def test_a_failed_unload_is_logged_and_the_corrupted_error_still_raises(client, caplog):
    from gaia.llm.lemonade_client import LemonadeClientError

    client.unload_model.side_effect = LemonadeClientError("server said no")
    with _serve(_Stream(itertools.repeat(NOISE))), caplog.at_level(logging.ERROR):
        with pytest.raises(ModelOutputCorruptedError):
            list(client.chat_completions(model=MODEL, messages=_MESSAGES, stream=True))
    assert "server said no" in caplog.text


def test_the_users_own_server_is_left_alone(client):
    client.is_users_server = MagicMock(return_value=True)
    with _serve(_Stream(itertools.repeat(NOISE))):
        with pytest.raises(ModelOutputCorruptedError):
            list(client.chat_completions(model=MODEL, messages=_MESSAGES, stream=True))
    client.unload_model.assert_not_called()


def test_a_normal_reply_that_says_unused_streams_unchanged(client):
    pieces = ["Port", " 8080", " is", " unused", " here", "."]
    with _serve(_Stream(pieces)):
        chunks = list(
            client.chat_completions(model=MODEL, messages=_MESSAGES, stream=True)
        )
    assert [c["choices"][0]["delta"]["content"] for c in chunks] == pieces
    client.unload_model.assert_not_called()


# --------------------------------------------------------------------------
# What the user sees
# --------------------------------------------------------------------------


def _wrapped_in_runtime_error():
    try:
        raise ModelOutputCorruptedError()
    except ModelOutputCorruptedError as inner:
        wrapped = RuntimeError(str(inner))
        wrapped.__cause__ = inner
        return wrapped


@pytest.mark.parametrize(
    "exc",
    [
        _wrapped_in_runtime_error(),
        RuntimeError(f"Streaming failed: {ModelOutputCorruptedError()}"),
    ],
    ids=["chained", "flattened"],
)
def test_the_error_reads_as_a_plain_message_through_any_wrapper(exc):
    typed = classify_lemonade_exception(exc)
    assert isinstance(typed, LemonadeOutputCorruptedError)
    assert typed.user_message == (
        "The model's answer came back corrupted, so GAIA stopped it. "
        "Send your message again."
    )
    assert not typed.retryable


class _Agent(Agent):
    def _get_system_prompt(self):
        return "Answer."

    def _register_tools(self):
        pass


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _Agent(silent_mode=True, skip_lemonade=True)
    a.console = MagicMock()
    a.console.cancelled = None
    yield a
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def test_the_turn_fails_with_the_plain_message_and_is_not_retried(agent):
    calls = []

    def stream(*a, **kw):
        calls.append(1)
        yield AgentResponse(text="The", is_complete=False)
        raise ModelOutputCorruptedError()

    agent.streaming = True
    agent.chat = MagicMock()
    agent.chat.send_messages_stream.side_effect = stream
    result = agent.process_query("How many qubits?")
    assert len(calls) == 1
    assert result["status"] == "failed"
    assert "came back corrupted" in result["result"]
    assert NOISE not in str(result)
