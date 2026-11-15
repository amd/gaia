"""A Gemma stream that emits a reserved ``<unusedN>`` token is stopped (#8172)."""

from unittest.mock import MagicMock

import pytest

from gaia.llm.lemonade_client import (
    OUTPUT_CORRUPTED_MESSAGE,
    LemonadeClient,
    LemonadeClientError,
    ModelOutputCorruptedError,
)
from gaia.llm.providers.lemonade import (
    LemonadeOutputCorruptedError,
    classify_lemonade_exception,
)

BASE = "http://localhost:13305/api/v1"
GEMMA = "Gemma-4-E4B-it-GGUF"


def _content(text):
    return {"choices": [{"delta": {"content": text}, "finish_reason": None}]}


def _client(chunks, users_server=False):
    client = LemonadeClient(base_url=BASE, verbose=False)
    client._ensure_model_loaded = lambda *a, **k: None
    client.is_users_server = lambda: users_server
    client.unload_model = MagicMock(return_value={"status": "success"})
    state = {"closed": False}

    def stream(**_kwargs):
        try:
            yield from chunks
        finally:
            state["closed"] = True

    client._stream_chat_chunks = stream
    return client, state


def _drain(client, model=GEMMA, seen=None):
    seen = [] if seen is None else seen
    for chunk in client.chat_completions(model=model, messages=[], stream=True):
        seen.append(chunk)
    return seen


def test_reserved_token_stops_the_stream_before_it_reaches_the_caller():
    chunks = [_content("Hello"), _content("<unused49>"), _content("<unused49>")]
    client, state = _client(chunks)
    seen = []
    with pytest.raises(ModelOutputCorruptedError) as err:
        _drain(client, seen=seen)
    assert [c["choices"][0]["delta"]["content"] for c in seen] == ["Hello"]
    assert err.value.__cause__.token == "<unused49>"
    assert state["closed"]
    client.unload_model.assert_called_once_with(GEMMA, ignore_if_not_loaded=True)


def test_a_token_split_across_chunks_is_still_caught():
    client, _ = _client([_content("<unu"), _content("sed12>")])
    with pytest.raises(ModelOutputCorruptedError):
        _drain(client)


def test_reasoning_is_guarded_too():
    chunk = {"choices": [{"delta": {"reasoning_content": "<unused3>"}}]}
    client, _ = _client([chunk])
    with pytest.raises(ModelOutputCorruptedError):
        _drain(client)


def test_the_users_own_server_is_never_unloaded():
    client, _ = _client([_content("<unused49>")], users_server=True)
    with pytest.raises(ModelOutputCorruptedError):
        _drain(client)
    client.unload_model.assert_not_called()


def test_a_failed_unload_still_fails_the_turn_as_corrupted():
    client, _ = _client([_content("<unused49>")])
    client.unload_model.side_effect = LemonadeClientError("server busy")
    with pytest.raises(ModelOutputCorruptedError):
        _drain(client)


def test_a_healthy_gemma_stream_passes_through():
    client, _ = _client([_content("Hi"), _content(" there")])
    assert len(_drain(client)) == 2
    client.unload_model.assert_not_called()


def test_other_models_are_not_guarded():
    client, _ = _client([_content("<unused49>")])
    assert len(_drain(client, model="Qwen3-30B-A3B-Instruct-2507-GGUF")) == 1


@pytest.mark.parametrize(
    "exc",
    [
        ModelOutputCorruptedError(),
        RuntimeError(OUTPUT_CORRUPTED_MESSAGE),
    ],
)
def test_the_agent_gets_a_plain_message(exc):
    classified = classify_lemonade_exception(exc)
    assert isinstance(classified, LemonadeOutputCorruptedError)
    assert classified.user_message == OUTPUT_CORRUPTED_MESSAGE
    assert not classified.retryable
