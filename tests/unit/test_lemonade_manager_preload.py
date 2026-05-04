# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for `LemonadeManager._try_preload_with_ctx` (issue #839).

The preload helper closes the gap in `_try_reload_with_ctx` (which only fires
when `context_size > 0`): when the Lemonade server is up but **idle** — no
model loaded, no context size reported — GAIA must proactively load the default
model with the required `ctx_size` instead of asking the user to run a manual
`lemonade-server serve --ctx-size N` command.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from gaia.llm.lemonade_client import LemonadeClientError, LemonadeStatus
from gaia.llm.lemonade_manager import DEFAULT_CONTEXT_SIZE, LemonadeManager


@pytest.fixture(autouse=True)
def _reset_manager():
    """Reset the singleton's class state before AND after every test.

    Without this, a test that successfully initialises the manager poisons the
    next test (it hits the `_initialized=True` fast path and skips re-checking
    server state). pytest-xdist would also flake otherwise.
    """
    LemonadeManager.reset()
    yield
    LemonadeManager.reset()


def _status(running=True, context_size=0, loaded_models=None):
    return LemonadeStatus(
        running=running,
        context_size=context_size,
        loaded_models=[] if loaded_models is None else loaded_models,
    )


def _make_client_mock(status):
    """Build a LemonadeClient mock returning the given status from get_status()."""
    client = MagicMock()
    client.base_url = "http://localhost:13305/api/v1"
    client.get_status.return_value = status
    return client


# ---------------------------------------------------------------------------
# Case 1 — idle server: preload must fire with ctx_size + auto_download=True
# ---------------------------------------------------------------------------


@patch("gaia.llm.lemonade_manager.LemonadeClient")
def test_idle_server_triggers_preload_with_ctx_size(mock_cls):
    """server running, no model loaded → load_model(DEFAULT_MODEL_NAME, ctx_size=N, auto_download=True)."""
    client = _make_client_mock(_status(running=True, context_size=0, loaded_models=[]))
    # After load_model, get_status should report the new ctx_size so callers can
    # observe the change (mirrors the real Lemonade response).
    client.get_status.side_effect = [
        _status(running=True, context_size=0, loaded_models=[]),  # first call (init)
        _status(
            running=True,
            context_size=32768,
            loaded_models=[{"id": "Gemma-4-E4B-it-GGUF"}],
        ),  # post-load probe
    ]
    mock_cls.return_value = client

    ok = LemonadeManager.ensure_ready(min_context_size=32768, quiet=True)

    assert ok is True
    client.load_model.assert_called_once()
    call = client.load_model.call_args
    # Model name passed positionally in the helper; assert by either path.
    args = call.args
    kwargs = call.kwargs
    assert (args and args[0] == "Gemma-4-E4B-it-GGUF") or kwargs.get(
        "model_name"
    ) == "Gemma-4-E4B-it-GGUF"
    assert kwargs.get("ctx_size") == 32768  # Literal — catch value-drift regressions.
    assert kwargs.get("prompt") is False
    assert kwargs.get("auto_download") is True, (
        "auto_download=True is required so first-run users (no model on disk) "
        "get a download instead of a silent failure (AC3)."
    )
    assert LemonadeManager.get_context_size() >= 32768


# ---------------------------------------------------------------------------
# Case 2 — happy path: ctx already big enough, no model load
# ---------------------------------------------------------------------------


@patch("gaia.llm.lemonade_manager.LemonadeClient")
def test_sufficient_ctx_skips_preload(mock_cls):
    client = _make_client_mock(
        _status(
            running=True,
            context_size=32768,
            loaded_models=[{"id": "Gemma-4-E4B-it-GGUF"}],
        )
    )
    mock_cls.return_value = client

    ok = LemonadeManager.ensure_ready(min_context_size=32768, quiet=True)

    assert ok is True
    client.load_model.assert_not_called()


# ---------------------------------------------------------------------------
# Case 3 — model loaded with too-small ctx routes through existing reload, not preload
# ---------------------------------------------------------------------------


@patch("gaia.llm.lemonade_manager.LemonadeClient")
def test_small_ctx_with_loaded_model_uses_existing_reload(mock_cls):
    """Existing `_try_reload_with_ctx` owns this path; new preload helper must NOT fire."""
    client = _make_client_mock(
        _status(
            running=True,
            context_size=8192,
            loaded_models=[{"id": "Gemma-4-E4B-it-GGUF"}],
        )
    )
    # Reload path: load_model is called with the existing model id.
    client.get_status.side_effect = [
        _status(
            running=True,
            context_size=8192,
            loaded_models=[{"id": "Gemma-4-E4B-it-GGUF"}],
        ),
        _status(
            running=True,
            context_size=32768,
            loaded_models=[{"id": "Gemma-4-E4B-it-GGUF"}],
        ),
    ]
    mock_cls.return_value = client

    ok = LemonadeManager.ensure_ready(min_context_size=32768, quiet=True)

    assert ok is True
    # load_model is called by the EXISTING reload path with the loaded model id,
    # NOT by the new preload helper with DEFAULT_MODEL_NAME (which here happens to
    # be the same string — distinguish by the *call-site*: the reload path does
    # not pass auto_download).
    assert client.load_model.called
    kwargs = client.load_model.call_args.kwargs
    # Existing _try_reload_with_ctx does not pass auto_download — that is the
    # signature distinguishing it from the new preload helper.
    assert "auto_download" not in kwargs or kwargs.get("auto_download") is False


# ---------------------------------------------------------------------------
# Case 4 — preload failure raises LemonadeClientError with actionable message
#          and leaves the singleton in a retryable state
# ---------------------------------------------------------------------------


@patch("gaia.llm.lemonade_manager.LemonadeClient")
def test_preload_failure_raises_actionable_and_does_not_poison_singleton(mock_cls):
    """If load_model raises, the helper re-raises a LemonadeClientError carrying
    the three actionable substrings AND `_initialized` stays False so a retry
    will reattempt initialisation (per adversarial reflection C1)."""
    client = _make_client_mock(_status(running=True, context_size=0, loaded_models=[]))
    client.load_model.side_effect = LemonadeClientError("server returned 500")
    mock_cls.return_value = client

    with pytest.raises(LemonadeClientError) as exc_info:
        LemonadeManager.ensure_ready(min_context_size=32768, quiet=True)

    msg = str(exc_info.value)
    assert "Lemonade" in msg
    assert "ctx_size=32768" in msg or "32768" in msg
    assert "lemonade-server serve" in msg

    # Singleton must NOT be in the broken (initialized=True, ctx=0) state.
    assert (
        LemonadeManager.is_initialized() is False
    ), "Singleton must allow retry after a failed preload (adversarial reflection C1)."

    # And the lock must be released.
    assert LemonadeManager._lock.acquire(timeout=1.0) is True
    LemonadeManager._lock.release()


# ---------------------------------------------------------------------------
# Case 5 — server not running: existing print_server_error path, no preload
# ---------------------------------------------------------------------------


@patch("gaia.llm.lemonade_manager.LemonadeClient")
def test_server_not_running_skips_preload(mock_cls):
    client = _make_client_mock(_status(running=False))
    mock_cls.return_value = client

    ok = LemonadeManager.ensure_ready(min_context_size=32768, quiet=True)

    assert ok is False
    client.load_model.assert_not_called()


# ---------------------------------------------------------------------------
# Case 6 — server returns loaded_models=None (defensive: don't crash on null JSON)
# ---------------------------------------------------------------------------


@patch("gaia.llm.lemonade_manager.LemonadeClient")
def test_loaded_models_none_does_not_crash(mock_cls):
    """Some Lemonade versions can return `loaded_models: null`. We must not
    `TypeError` from iterating None — the new guard normalises to []."""
    bad_status = _status(running=True, context_size=0, loaded_models=[])
    bad_status.loaded_models = None  # simulate a server returning null
    client = _make_client_mock(bad_status)
    client.get_status.side_effect = [
        bad_status,
        _status(
            running=True,
            context_size=32768,
            loaded_models=[{"id": "Gemma-4-E4B-it-GGUF"}],
        ),
    ]
    mock_cls.return_value = client

    ok = LemonadeManager.ensure_ready(min_context_size=32768, quiet=True)

    assert ok is True
    client.load_model.assert_called_once()


# ---------------------------------------------------------------------------
# Case 7 — concurrent ensure_ready() callers preload exactly once
# ---------------------------------------------------------------------------


@patch("gaia.llm.lemonade_manager.LemonadeClient")
def test_concurrent_ensure_ready_loads_model_once(mock_cls):
    """Two threads racing into ensure_ready() — only one load_model call."""
    client = _make_client_mock(_status(running=True, context_size=0, loaded_models=[]))
    # First call: idle. Subsequent: already loaded.
    statuses = [
        _status(running=True, context_size=0, loaded_models=[]),
        _status(
            running=True,
            context_size=32768,
            loaded_models=[{"id": "Gemma-4-E4B-it-GGUF"}],
        ),
        _status(
            running=True,
            context_size=32768,
            loaded_models=[{"id": "Gemma-4-E4B-it-GGUF"}],
        ),
    ]
    client.get_status.side_effect = statuses
    mock_cls.return_value = client

    results = []
    barrier = threading.Barrier(2)

    def _go():
        barrier.wait()
        results.append(LemonadeManager.ensure_ready(min_context_size=32768, quiet=True))

    threads = [threading.Thread(target=_go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert results == [True, True]
    assert client.load_model.call_count == 1


# ---------------------------------------------------------------------------
# Case 8 — sanity: DEFAULT_CONTEXT_SIZE constant matches expected literal
# ---------------------------------------------------------------------------


def test_default_context_size_literal():
    """Belt-and-braces: assert the *literal* 32768 — testing-against-the-import
    is circular and would silently accept a value-drift regression."""
    assert DEFAULT_CONTEXT_SIZE == 32768
