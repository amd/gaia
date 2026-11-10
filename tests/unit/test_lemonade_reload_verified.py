# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A reload to a larger context window counts only once the server confirms it.

The server could report a smaller window after the reload and GAIA cached the
window it had asked for anyway, so chat ran on a context it did not have. Now
the reported window is kept, and a reload that keeps falling short stops after
a bounded number of attempts with an error naming the model and both windows.
"""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia.llm.lemonade_client import ContextReloadUnverifiedError
from gaia.llm.lemonade_manager import LemonadeManager

MODEL = "Gemma-4-E4B-it-GGUF"


def _status(ctx):
    return SimpleNamespace(
        loaded_models=[
            {
                "model_name": MODEL,
                "type": "llm",
                "device": "gpu",
                "recipe": "llamacpp",
                "recipe_options": {"ctx_size": ctx},
            }
        ]
    )


@pytest.fixture(autouse=True)
def _fresh_manager():
    LemonadeManager.reset()
    yield
    LemonadeManager.reset()


def _client(reported_after_reload):
    client = MagicMock()
    client.base_url = "http://127.0.0.1:1/api/v1"
    client.get_model_max_context_window.return_value = None
    client.get_status.return_value = _status(reported_after_reload)
    return client


def _reload(client, lock):
    return LemonadeManager._try_reload_with_ctx(
        client, _status(4096), 32768, True, lock
    )


def test_a_confirmed_reload_caches_the_confirmed_window():
    client = _client(32768)
    lock = threading.Lock()
    lock.acquire()
    assert _reload(client, lock) is True
    assert LemonadeManager._context_size == 32768


def test_an_unconfirmed_reload_keeps_the_reported_window_then_stops_loudly():
    client = _client(4096)
    lock = threading.Lock()
    lock.acquire()

    assert _reload(client, lock) is False
    assert LemonadeManager._context_size == 4096

    with pytest.raises(ContextReloadUnverifiedError) as excinfo:
        _reload(client, lock)
    message = str(excinfo.value)
    assert MODEL in message and "32768" in message and "4096" in message
    assert lock.locked()

    # Past the bound, no further reload is sent.
    with pytest.raises(ContextReloadUnverifiedError):
        _reload(client, lock)
    assert client.load_model.call_count == 2
    assert lock.locked()


def test_a_stuck_reload_is_tried_again_after_the_wait_and_recovers(monkeypatch):
    """Freeing memory must get the user unstuck without restarting GAIA."""
    import gaia.llm.lemonade_manager as manager_module

    now = [1000.0]
    monkeypatch.setattr(manager_module.time, "monotonic", lambda: now[0])
    client = _client(4096)
    lock = threading.Lock()
    lock.acquire()

    assert _reload(client, lock) is False
    with pytest.raises(ContextReloadUnverifiedError):
        _reload(client, lock)
    sent = client.load_model.call_count

    # Still inside the wait: refused without another reload.
    now[0] += LemonadeManager._UNVERIFIED_RELOAD_RETRY_AFTER - 1
    with pytest.raises(ContextReloadUnverifiedError) as excinfo:
        _reload(client, lock)
    assert client.load_model.call_count == sent
    assert "again after about a minute" in str(excinfo.value)

    # After the wait, with memory freed, the reload is sent and confirmed.
    now[0] += 2
    client.get_status.return_value = _status(32768)
    assert _reload(client, lock) is True
    assert client.load_model.call_count == sent + 1
    assert LemonadeManager._context_size == 32768


def test_a_reload_that_still_falls_short_after_the_wait_stops_again(monkeypatch):
    import gaia.llm.lemonade_manager as manager_module

    now = [1000.0]
    monkeypatch.setattr(manager_module.time, "monotonic", lambda: now[0])
    client = _client(4096)
    lock = threading.Lock()
    lock.acquire()

    assert _reload(client, lock) is False
    with pytest.raises(ContextReloadUnverifiedError):
        _reload(client, lock)
    sent = client.load_model.call_count

    # One retry per wait, not a loop.
    now[0] += LemonadeManager._UNVERIFIED_RELOAD_RETRY_AFTER + 1
    with pytest.raises(ContextReloadUnverifiedError):
        _reload(client, lock)
    assert client.load_model.call_count == sent + 1
    with pytest.raises(ContextReloadUnverifiedError):
        _reload(client, lock)
    assert client.load_model.call_count == sent + 1
