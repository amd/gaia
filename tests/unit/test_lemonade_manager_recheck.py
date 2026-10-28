# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``ensure_ready`` re-check of an already-initialized Lemonade fails loudly.

When the context window is below the floor, the warm path re-reads the server
status. Before this, any failure there was logged at DEBUG and the caller was
told everything was ready, so a server that started rejecting the API key passed
the check and the user hit a vaguer error mid-chat. Auth and other client errors
now propagate, as on the first-init path; anything unexpected is logged at WARNING.
"""

import logging
from unittest.mock import patch

import pytest

from gaia.llm.lemonade_client import (
    LemonadeAuthError,
    LemonadeClient,
    LemonadeClientError,
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
    # Real LemonadeClient.get_status, so its own error wrapping is exercised.
    with patch.object(LemonadeClient, "health_check", side_effect=error):
        return LemonadeManager.ensure_ready(min_context_size=32768, quiet=True)


def test_auth_error_on_recheck_propagates():
    with pytest.raises(LemonadeAuthError):
        _ensure_ready_with(LemonadeAuthError("401"))


def test_unreachable_server_keeps_cached_state():
    assert _ensure_ready_with(LemonadeClientError("refused")) is True


def test_unexpected_error_on_recheck_is_logged_at_warning(caplog):
    with patch.object(LemonadeClient, "get_status", side_effect=RuntimeError("boom")):
        with caplog.at_level(logging.WARNING):
            assert (
                LemonadeManager.ensure_ready(min_context_size=32768, quiet=True) is True
            )
    assert any(
        r.levelno == logging.WARNING and "boom" in r.getMessage()
        for r in caplog.records
    )
