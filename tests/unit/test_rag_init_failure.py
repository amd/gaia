# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A document Q&A startup failure must not look like a missing extra.

``ImportError`` (the extra is not installed) is the only failure that says to
install ``[rag]``, and the only one cached for the session. A config or
embedder error keeps its cause and is tried again on the next access.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("gaia_agent")

from gaia_agent.chat_agent import _UNSET, ChatAgent  # noqa: E402


def _bare_agent():
    """ChatAgent without ``__init__`` — only the RAG build path is under test."""
    agent = ChatAgent.__new__(ChatAgent)
    agent.observers = []
    agent.path_validator = MagicMock()
    agent._rag = _UNSET
    agent._rag_config = _UNSET
    agent._rag_missing = False
    agent._rag_init_error = None
    agent._rag_failure_logged = None
    agent._rag_config_kwargs = {"model": "test-model"}
    return agent


def _warnings(caplog, text):
    return [
        record
        for record in caplog.records
        if record.levelno == logging.WARNING and text in record.getMessage()
    ]


def test_import_error_is_cached_and_names_the_extra(caplog):
    agent = _bare_agent()
    agent._rag_config = MagicMock()
    caplog.set_level(logging.WARNING, logger="gaia_agent.chat_agent")

    with patch(
        "gaia_agent.chat_agent.RAGSDK", side_effect=ImportError("no faiss")
    ) as sdk:
        assert agent.rag is None
        assert agent.rag is None

    assert sdk.call_count == 1
    assert agent._rag is None
    assert agent._rag_missing is True
    assert isinstance(agent._rag_init_error, ImportError)
    messages = [record.getMessage() for record in _warnings(caplog, "no faiss")]
    assert len(messages) == 1
    assert "install with" in messages[0]
    assert ".[rag]" in messages[0]


def test_other_startup_failure_is_retried_and_keeps_its_cause(caplog):
    agent = _bare_agent()
    agent._rag_config = MagicMock()
    caplog.set_level(logging.WARNING, logger="gaia_agent.chat_agent")
    built = MagicMock(name="sdk")
    calls = {"n": 0}

    def factory(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("embedder refused the config")
        return built

    with patch("gaia_agent.chat_agent.RAGSDK", side_effect=factory):
        assert agent.rag is None
        assert agent._rag is _UNSET
        assert agent.rag is built

    assert calls["n"] == 2
    assert agent._rag is built
    assert agent._rag_init_error is None
    assert agent._rag_missing is False
    messages = [record.getMessage() for record in _warnings(caplog, "embedder refused")]
    assert len(messages) == 1
    assert messages[0] == "RAG failed to start: embedder refused the config"
    assert "install with" not in messages[0]


def test_repeated_startup_failure_retries_without_repeating_the_warning(caplog):
    agent = _bare_agent()
    agent._rag_config = MagicMock()
    caplog.set_level(logging.DEBUG, logger="gaia_agent.chat_agent")

    with patch("gaia_agent.chat_agent.RAGSDK", side_effect=OSError("disk full")) as sdk:
        assert agent.rag is None
        assert agent._rag is _UNSET
        assert agent.rag is None

    assert sdk.call_count == 2
    assert isinstance(agent._rag_init_error, OSError)
    assert len(_warnings(caplog, "disk full")) == 1
    assert any(
        "RAG init failed again" in record.getMessage() for record in caplog.records
    )


def test_config_failure_is_retried_and_not_called_a_missing_extra(caplog):
    agent = _bare_agent()
    caplog.set_level(logging.WARNING, logger="gaia_agent.chat_agent")
    config = MagicMock(name="config")
    sdk = MagicMock(name="sdk")
    calls = {"n": 0}

    def factory(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("cache dir not writable")
        return config

    with (
        patch("gaia_agent.chat_agent.RAGConfig", side_effect=factory),
        patch("gaia_agent.chat_agent.RAGSDK", return_value=sdk) as rag_sdk,
    ):
        assert agent.rag is None
        assert agent._rag is _UNSET
        assert agent.rag is sdk

    assert calls["n"] == 2
    assert rag_sdk.call_count == 1
    assert rag_sdk.call_args.args[0] is config
    messages = [
        record.getMessage() for record in _warnings(caplog, "cache dir not writable")
    ]
    assert len(messages) == 1
    assert "install with" not in messages[0]


def test_config_import_error_is_not_retried(caplog):
    agent = _bare_agent()
    caplog.set_level(logging.WARNING, logger="gaia_agent.chat_agent")

    with (
        patch(
            "gaia_agent.chat_agent.RAGConfig",
            side_effect=ImportError("no faiss"),
        ) as rag_config,
        patch("gaia_agent.chat_agent.RAGSDK") as rag_sdk,
    ):
        assert agent.rag is None
        assert agent.rag is None

    assert rag_config.call_count == 1
    assert rag_sdk.call_count == 0
    assert agent._rag is None
    assert agent._rag_missing is True
    messages = [record.getMessage() for record in _warnings(caplog, "no faiss")]
    assert len(messages) == 1
    assert "install with" in messages[0]


def test_error_chained_from_an_import_error_is_not_a_missing_extra(caplog):
    """Only the raised type counts: a RuntimeError caused by an ImportError is
    a startup failure with its own cause, not a missing extra."""
    agent = _bare_agent()
    agent._rag_config = MagicMock()
    caplog.set_level(logging.WARNING, logger="gaia_agent.chat_agent")

    def factory(*_args, **_kwargs):
        try:
            raise ImportError("libomp missing")
        except ImportError as inner:
            raise RuntimeError("embedder backend failed") from inner

    with patch("gaia_agent.chat_agent.RAGSDK", side_effect=factory) as sdk:
        assert agent.rag is None
        assert agent.rag is None

    assert sdk.call_count == 2
    assert agent._rag is _UNSET
    assert agent._rag_missing is False
    assert isinstance(agent._rag_init_error, RuntimeError)
    assert not _warnings(caplog, "install with")


def test_module_not_found_counts_as_a_missing_extra():
    agent = _bare_agent()
    agent._rag_config = MagicMock()

    with patch(
        "gaia_agent.chat_agent.RAGSDK",
        side_effect=ModuleNotFoundError("No module named 'faiss'"),
    ) as sdk:
        assert agent.rag is None
        assert agent.rag is None

    assert sdk.call_count == 1
    assert agent._rag_missing is True


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit])
def test_interrupts_propagate_and_are_not_recorded(exc_type):
    agent = _bare_agent()
    agent._rag_config = MagicMock()

    with patch("gaia_agent.chat_agent.RAGSDK", side_effect=exc_type()):
        with pytest.raises(exc_type):
            _ = agent.rag

    assert agent._rag is _UNSET
    assert agent._rag_init_error is None
    assert agent._rag_missing is False


def test_a_different_failure_warns_again(caplog):
    agent = _bare_agent()
    agent._rag_config = MagicMock()
    caplog.set_level(logging.WARNING, logger="gaia_agent.chat_agent")
    errors = iter([OSError("disk full"), OSError("disk full"), ValueError("bad")])

    def factory(*_args, **_kwargs):
        raise next(errors)

    with patch("gaia_agent.chat_agent.RAGSDK", side_effect=factory):
        for _ in range(3):
            assert agent.rag is None

    assert len(_warnings(caplog, "disk full")) == 1
    assert len(_warnings(caplog, "bad")) == 1
    assert isinstance(agent._rag_init_error, ValueError)


def test_import_error_after_a_transient_failure_is_then_cached():
    agent = _bare_agent()
    agent._rag_config = MagicMock()
    errors = iter([OSError("disk full"), ImportError("no faiss")])

    def factory(*_args, **_kwargs):
        raise next(errors)

    with patch("gaia_agent.chat_agent.RAGSDK", side_effect=factory) as sdk:
        for _ in range(4):
            assert agent.rag is None

    assert sdk.call_count == 2
    assert agent._rag is None
    assert agent._rag_missing is True


def test_tool_message_follows_recovery():
    """Once a retry succeeds the stale startup error must not linger."""
    from gaia.agents.tools.rag_tools import _rag_unavailable_message

    agent = _bare_agent()
    agent._rag_config = MagicMock()
    built = MagicMock(name="sdk")
    outcomes = iter([RuntimeError("embedder down"), built])

    def factory(*_args, **_kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    with patch("gaia_agent.chat_agent.RAGSDK", side_effect=factory):
        assert agent.rag is None
        assert "embedder down" in _rag_unavailable_message(agent)
        assert agent.rag is built

    assert agent._rag_init_error is None
    assert agent.rag is built
