# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A streamed chat reloads its model once when Lemonade has unloaded it.

Lemonade holds one chat model. A client outside the model-slot lease (another
PC, a container) can make it load a different one after this client's load
check and before its request, which then fails with "No model loaded". The
non-streaming call already reloaded and retried; the streamed one ended the turn.

No Lemonade server required.
"""

from contextlib import nullcontext
from unittest.mock import call, patch

import pytest

from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError

MODEL = "Qwen3.6-35B-A3B-GGUF"
NOT_LOADED = (
    "OpenAI APIError from the model server at localhost:13305: "
    f"No model loaded: {MODEL}"
)
CHUNKS = [{"choices": [{"delta": {"content": text}}]} for text in ("Hel", "lo")]


class _Streams:
    """Stands in for ``_stream_chat_chunks``: one scripted outcome per call."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)

        def stream():
            for item in outcome:
                if isinstance(item, Exception):
                    raise item
                yield item

        return stream()


@pytest.fixture
def client():
    made = LemonadeClient(host="localhost", port=13305)
    with (
        patch.object(made, "_model_slot_lease", return_value=nullcontext()),
        patch.object(made, "_ensure_model_loaded") as ensure,
    ):
        made.ensure = ensure
        yield made


def _stream(client, streams, **kwargs):
    with patch.object(client, "_stream_chat_chunks", streams):
        return list(
            client._stream_chat_completions_with_openai(
                model=MODEL, messages=[{"role": "user", "content": "hi"}], **kwargs
            )
        )


def test_an_unloaded_model_is_reloaded_and_the_request_sent_again(client, caplog):
    streams = _Streams([LemonadeClientError(NOT_LOADED)], CHUNKS)

    with caplog.at_level("INFO"):
        chunks = _stream(client, streams)

    assert chunks == CHUNKS
    assert len(streams.calls) == 2
    assert streams.calls[0] == streams.calls[1]
    assert client.ensure.mock_calls == [
        call(MODEL, True),
        call(MODEL, auto_download=True, force=True),
    ]
    assert f"Model '{MODEL}' not loaded" in caplog.text


def test_it_reloads_once_then_reports_the_error(client):
    streams = _Streams(
        [LemonadeClientError(NOT_LOADED)], [LemonadeClientError(NOT_LOADED)], CHUNKS
    )

    with pytest.raises(LemonadeClientError, match="No model loaded"):
        _stream(client, streams)

    assert len(streams.calls) == 2
    assert client.ensure.call_count == 2


def test_a_failed_reload_is_the_error_reported(client):
    streams = _Streams([LemonadeClientError(NOT_LOADED)], CHUNKS)
    client.ensure.side_effect = [None, LemonadeClientError("not enough memory")]

    with pytest.raises(LemonadeClientError, match="not enough memory"):
        _stream(client, streams)

    assert len(streams.calls) == 1


def test_a_stream_that_already_sent_output_is_not_sent_again(client):
    streams = _Streams([CHUNKS[0], LemonadeClientError(NOT_LOADED)], CHUNKS)
    received = []

    with patch.object(client, "_stream_chat_chunks", streams):
        with pytest.raises(LemonadeClientError, match="No model loaded"):
            for chunk in client._stream_chat_completions_with_openai(
                model=MODEL, messages=[{"role": "user", "content": "hi"}]
            ):
                received.append(chunk)

    assert received == [CHUNKS[0]]
    assert len(streams.calls) == 1
    assert client.ensure.call_count == 1


def test_another_error_is_not_retried(client):
    overflow = LemonadeClientError(
        "OpenAI APIError: request (9000 tokens) exceeds the available context size"
    )
    streams = _Streams([overflow], CHUNKS)

    with pytest.raises(LemonadeClientError, match="exceeds the available context"):
        _stream(client, streams)

    assert len(streams.calls) == 1
    assert client.ensure.call_count == 1


def test_a_caller_that_turned_loading_off_gets_the_error(client):
    streams = _Streams([LemonadeClientError(NOT_LOADED)], CHUNKS)

    with pytest.raises(LemonadeClientError, match="No model loaded"):
        _stream(client, streams, auto_download=False)

    assert len(streams.calls) == 1
    assert client.ensure.call_count == 1
