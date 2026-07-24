# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""#2447: the undo window is overridable via GAIA_EMAIL_UNDO_WINDOW_SECONDS.

The default is 120s — a chat-speed floor. A chat-mediated bulk archive runs
through the LLM tool-loop and can take real time to complete, so the window
must outlast that rather than the old instant-UI-button-calibrated 30s.
Making the floor configurable additionally lets a slower deployment raise it
further via GAIA_EMAIL_UNDO_WINDOW_SECONDS.
"""

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.config import (  # noqa: E402
    ConfigurationError,
    EmailAgentConfig,
    default_undo_window_seconds,
)


def test_default_is_120_when_env_unset(monkeypatch):
    monkeypatch.delenv("GAIA_EMAIL_UNDO_WINDOW_SECONDS", raising=False)
    assert default_undo_window_seconds() == 120
    assert EmailAgentConfig().undo_window_seconds == 120


def test_env_override_raises_window(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_UNDO_WINDOW_SECONDS", "120")
    assert default_undo_window_seconds() == 120
    assert EmailAgentConfig().undo_window_seconds == 120


def test_blank_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_UNDO_WINDOW_SECONDS", "   ")
    assert default_undo_window_seconds() == 120


def test_non_integer_env_raises_actionable_error(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_UNDO_WINDOW_SECONDS", "abc")
    with pytest.raises(ConfigurationError) as exc:
        default_undo_window_seconds()
    assert "GAIA_EMAIL_UNDO_WINDOW_SECONDS" in str(exc.value)


def test_non_positive_env_raises_actionable_error(monkeypatch):
    monkeypatch.setenv("GAIA_EMAIL_UNDO_WINDOW_SECONDS", "0")
    with pytest.raises(ConfigurationError):
        default_undo_window_seconds()


def test_validate_rejects_non_positive_explicit_value(monkeypatch):
    monkeypatch.delenv("GAIA_EMAIL_UNDO_WINDOW_SECONDS", raising=False)
    with pytest.raises(ConfigurationError):
        EmailAgentConfig(undo_window_seconds=0).validate()
