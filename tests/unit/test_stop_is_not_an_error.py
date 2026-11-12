# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pressing Stop is logged as a stop, not as three errors.

In v0.26.0-rc18 every Stop while the model was still silent wrote three ERROR
lines to gaia.log (the Lemonade client, the SDK and the agent loop), so 30
stops buried real failures under 90 errors in a troubleshooting report.
"""

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
from gaia.llm.retry import StreamCancelledError, stopped_by_user
from tests.unit.agents import test_model_unreachable_retry as _base
from tests.unit.agents.test_step_timing import _sdk

clean_registry = _base.clean_registry

_STOP = "Stopped while waiting for the model to respond."


def _wrapped_stop() -> LemonadeClientError:
    try:
        raise StreamCancelledError(_STOP)
    except StreamCancelledError as e:
        try:
            raise LemonadeClientError(f"Streaming request failed: {e}") from e
        except LemonadeClientError as wrapped:
            return wrapped


def test_a_wrapped_stop_is_recognised_and_other_errors_are_not():
    assert stopped_by_user(StreamCancelledError(_STOP))
    assert stopped_by_user(_wrapped_stop())
    assert not stopped_by_user(LemonadeClientError("Streaming request failed: 500"))
    assert not stopped_by_user(RuntimeError(_STOP))


def test_the_lemonade_client_logs_a_stop_without_an_error():
    client = LemonadeClient(base_url="http://127.0.0.1:1", verbose=False)
    client.log = Mock()

    def stream(*_a, **_k):
        raise StreamCancelledError(_STOP)
        yield  # pragma: no cover

    client._model_retrier = lambda *a, **k: SimpleNamespace(stream=stream)

    with pytest.raises(LemonadeClientError) as raised:
        list(client._stream_chat_chunks("Gemma-4-E4B-it-GGUF", [{"role": "user"}]))

    assert stopped_by_user(raised.value)
    client.log.error.assert_not_called()


def test_the_sdk_logs_a_stop_without_an_error():
    stop = _wrapped_stop()

    class _Provider:
        def chat(self, **_kwargs):
            raise stop
            yield  # pragma: no cover

    chat = _sdk(_Provider())

    with pytest.raises(LemonadeClientError):
        list(chat.send_messages_stream([{"role": "user", "content": "x"}]))

    chat.log.error.assert_not_called()


def test_the_sdk_logs_a_non_streaming_stop_without_an_error():
    """A Stop during the busy-server wait reaches non-streaming calls too."""
    stop = _wrapped_stop()

    class _Provider:
        def chat(self, **_kwargs):
            raise stop

    chat = _sdk(_Provider())

    with pytest.raises(LemonadeClientError):
        chat.send_messages([{"role": "user", "content": "x"}])

    chat.log.error.assert_not_called()


def test_the_agent_loop_logs_a_non_streaming_stop_without_an_error(
    clean_registry, caplog
):
    agent = _base._make_agent(streaming=False)
    stopped = threading.Event()
    agent.console.cancelled = stopped

    def _send(*_a, **_k):
        stopped.set()
        raise _wrapped_stop()

    agent.chat = Mock()
    agent.chat.get_stats = Mock(return_value={})
    agent.chat.send_messages = Mock(side_effect=_send)

    with caplog.at_level(logging.INFO, logger="gaia.agents.base.agent"):
        agent.process_query("Check the ledger", max_steps=3)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == []
    assert any("stopped" in r.getMessage().lower() for r in caplog.records)


def test_the_agent_loop_logs_a_stop_without_an_error(clean_registry, caplog):
    agent = _base._make_agent(streaming=True)
    stopped = threading.Event()
    agent.console.cancelled = stopped

    def _stream(*_a, **_k):
        stopped.set()
        raise _wrapped_stop()
        yield  # pragma: no cover

    agent.chat = Mock()
    agent.chat.get_stats = Mock(return_value={})
    agent.chat.send_messages_stream = Mock(side_effect=_stream)

    with caplog.at_level(logging.INFO, logger="gaia.agents.base.agent"):
        agent.process_query("Check the ledger", max_steps=3)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == []
    assert any("stopped" in r.getMessage().lower() for r in caplog.records)


def test_a_real_streaming_failure_is_still_an_error(clean_registry, caplog):
    agent = _base._make_agent(streaming=True)

    def _stream(*_a, **_k):
        raise LemonadeClientError("Streaming request failed: 500 Internal")
        yield  # pragma: no cover

    agent.chat = Mock()
    agent.chat.get_stats = Mock(return_value={})
    agent.chat.send_messages_stream = Mock(side_effect=_stream)

    with caplog.at_level(logging.INFO, logger="gaia.agents.base.agent"):
        agent.process_query("Check the ledger", max_steps=1)

    assert any(
        r.levelno == logging.ERROR and "500 Internal" in r.getMessage()
        for r in caplog.records
    )
