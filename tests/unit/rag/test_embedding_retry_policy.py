# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Embedding a batch on a busy GPU: wait visibly, never resend blindly.

A batch used to be tried three times at 180 s each, with only a log line
between tries — about nine minutes of silence per batch on a contended GPU,
and the same work queued two more times on the hardware that was already too
busy to finish it once.
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from gaia.agents.tools import rag_tools
from gaia.llm.lemonade_client import LemonadeClientError
from gaia.rag import sdk as rag_sdk
from gaia.rag.sdk import RAGSDK, EmbeddingUnavailableError

MODEL = "test-embedder"
_REAL_SLEEP = time.sleep


@pytest.fixture
def sdk(monkeypatch):
    monkeypatch.setattr("gaia.rag.sdk.time.sleep", lambda _: None)
    instance = RAGSDK.__new__(RAGSDK)
    instance.config = SimpleNamespace(embedding_model=MODEL, show_stats=False)
    instance.log = Mock()
    instance.embedder = Mock()
    return instance


def _lemonade_error(cause: BaseException) -> LemonadeClientError:
    """What ``LemonadeClient.embeddings`` raises: the requests error as context."""
    try:
        try:
            raise cause
        except requests.exceptions.RequestException as e:
            raise LemonadeClientError(f"Request failed: {e}")
    except LemonadeClientError as wrapped:
        return wrapped


def _read_timeout() -> LemonadeClientError:
    return _lemonade_error(
        requests.exceptions.ReadTimeout(
            "HTTPConnectionPool(host='localhost', port=13305): Read timed out."
        )
    )


def test_a_batch_that_times_out_is_not_resent(sdk):
    sdk.embedder.embeddings.side_effect = _read_timeout()

    with pytest.raises(EmbeddingUnavailableError) as exc:
        sdk._encode_texts(["chunk"])

    assert sdk.embedder.embeddings.call_count == 1
    assert exc.value.busy
    assert not exc.value.unreachable
    assert "busy" in str(exc.value)
    assert f"{rag_sdk.EMBED_BATCH_TIMEOUT_S}s" in str(exc.value)


def test_the_batch_gets_one_bounded_wait(sdk):
    sdk.embedder.embeddings.return_value = {"data": [{"embedding": [1, 2]}]}

    sdk._encode_texts(["chunk"])

    assert (
        sdk.embedder.embeddings.call_args.kwargs["timeout"]
        == rag_sdk.EMBED_BATCH_TIMEOUT_S
    )


def test_an_unreachable_server_is_reported_at_once(sdk):
    sdk.embedder.embeddings.side_effect = _lemonade_error(
        requests.exceptions.ConnectionError("Connection refused")
    )

    with pytest.raises(EmbeddingUnavailableError) as exc:
        sdk._encode_texts(["chunk"])

    assert sdk.embedder.embeddings.call_count == 1
    assert exc.value.unreachable


@pytest.mark.parametrize("count", [1, 24, 26])
def test_a_wrong_vector_count_fails_without_resending(sdk, count):
    # Same input, same model: a resend gets the same wrong answer.
    sdk.embedder.embeddings.return_value = {"data": [{"embedding": [1, 2]}] * count}

    with pytest.raises(EmbeddingUnavailableError, match=f"{count}/25 vectors"):
        sdk._encode_texts([f"chunk {i}" for i in range(25)])

    assert sdk.embedder.embeddings.call_count == 1


def test_a_server_error_is_not_resent(sdk):
    sdk.embedder.embeddings.side_effect = LemonadeClientError(
        "Request failed with status 500: model crashed"
    )

    with pytest.raises(EmbeddingUnavailableError):
        sdk._encode_texts(["chunk"])

    assert sdk.embedder.embeddings.call_count == 1


def test_a_warming_embedder_is_waited_for_visibly_and_boundedly(sdk):
    sdk.embedder.embeddings.return_value = {"data": []}

    with pytest.raises(EmbeddingUnavailableError, match="still loading"):
        sdk._encode_texts(["chunk"])

    assert sdk.embedder.embeddings.call_count == rag_sdk.EMBED_WARMUP_ATTEMPTS
    assert sdk.log.warning.call_count == rag_sdk.EMBED_WARMUP_ATTEMPTS - 1


def test_a_slow_batch_reports_that_it_is_waiting(sdk, monkeypatch):
    monkeypatch.setattr(rag_sdk, "EMBED_WAIT_REPORT_S", 0.05)
    waits = []

    def slow(texts, **kwargs):
        _REAL_SLEEP(0.3)
        return {"data": [{"embedding": [1, 2]} for _ in texts]}

    sdk.embedder.embeddings.side_effect = slow
    # The fixture stubs time.sleep for the SDK; the report thread needs a real clock.
    monkeypatch.setattr("gaia.rag.sdk.time.sleep", _REAL_SLEEP)

    sdk._encode_texts(["chunk"], wait_callback=waits.append)

    assert len(waits) >= 2
    assert waits[-1] == 0, "the wait must be cleared once the batch answers"
    assert max(waits) > 0


def test_index_job_shows_the_wait_to_the_user():
    job = rag_tools._IndexJob("/docs/big.pdf")
    job.progress(250, 1000)
    job.waiting(95)

    result = rag_tools._indexing_in_progress(job)

    assert "busy" in result["message"]
    assert "95s" in result["message"]
    assert result["embedder_wait_seconds"] == 95

    job.waiting(0)
    assert "busy" not in rag_tools._indexing_in_progress(job)["message"]


def test_a_busy_embedder_gets_a_wait_and_retry_next_step():
    error = EmbeddingUnavailableError(MODEL, _read_timeout())
    result = rag_tools._embedding_error(error)

    assert "busy" in result["next_step"]
    assert "gaia init" not in result["next_step"]


def test_wait_reporting_thread_stops_when_the_batch_fails(sdk, monkeypatch):
    monkeypatch.setattr(rag_sdk, "EMBED_WAIT_REPORT_S", 0.05)
    monkeypatch.setattr("gaia.rag.sdk.time.sleep", _REAL_SLEEP)
    before = threading.active_count()
    sdk.embedder.embeddings.side_effect = _read_timeout()

    with pytest.raises(EmbeddingUnavailableError):
        sdk._encode_texts(["chunk"], wait_callback=lambda _s: None)

    _REAL_SLEEP(0.2)
    assert threading.active_count() <= before
