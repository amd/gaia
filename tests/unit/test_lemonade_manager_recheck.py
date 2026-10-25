# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``ensure_ready`` re-check of an already-initialized Lemonade fails loudly.

When the context window is below the floor, the warm path re-reads the server
status. Before this, any failure there was logged at DEBUG and the caller was
told everything was ready, so a server that started rejecting the API key passed
the check and the user hit a vaguer error mid-chat. Auth and other client errors
now propagate, as on the first-init path; an unreachable server keeps the cached
state; anything unexpected is logged at WARNING.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from gaia.llm.lemonade_client import (
    LemonadeAuthError,
    LemonadeClientError,
    LemonadeUnresponsiveError,
)
from gaia.llm.lemonade_manager import LemonadeManager


@pytest.fixture(autouse=True)
def _warm_manager_with_small_ctx():
    """Initialized manager whose cached ctx is below the floor, recheck due."""
    LemonadeManager.reset()
    LemonadeManager._initialized = True
    LemonadeManager._context_size = 4096
    LemonadeManager._last_recheck_time = 0.0
    yield
    LemonadeManager.reset()


def _ensure_ready_with(error):
    with patch("gaia.llm.lemonade_manager.LemonadeClient") as client_cls:
        client_cls.return_value = MagicMock(get_status=MagicMock(side_effect=error))
        return LemonadeManager.ensure_ready(min_context_size=32768, quiet=True)


def test_auth_error_on_recheck_propagates():
    with pytest.raises(LemonadeAuthError):
        _ensure_ready_with(LemonadeAuthError("401"))


def test_other_client_error_on_recheck_propagates():
    with pytest.raises(LemonadeClientError):
        _ensure_ready_with(LemonadeUnresponsiveError("no answer"))


@pytest.mark.parametrize(
    "error",
    [
        requests.exceptions.ConnectionError("refused"),
        requests.exceptions.ConnectTimeout("slow"),
    ],
)
def test_unreachable_server_on_recheck_keeps_cached_state(error):
    assert _ensure_ready_with(error) is True


def test_unexpected_error_on_recheck_is_logged_at_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert _ensure_ready_with(RuntimeError("boom")) is True
    assert any(
        r.levelno == logging.WARNING and "boom" in r.getMessage()
        for r in caplog.records
    )
