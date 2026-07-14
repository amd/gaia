# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fixed contract (#1892): EmailAgentConfig.ctx_size + EmailTriageAgent wiring.

Two pieces, both new:

1. ``EmailAgentConfig`` gains an optional ``ctx_size: Optional[int] = None``
   field.
2. When ``config.ctx_size`` is set, ``EmailTriageAgent.__init__`` must set
   the override on the concrete ``LemonadeClient`` the agent chats through.
   The pre-existing (read-only, confirmed) object graph:
   ``EmailTriageAgent.chat`` is an ``AgentSDK`` (``self.chat = AgentSDK(...)``
   in ``Agent.__init__``, ``src/gaia/agents/base/agent.py``);
   ``AgentSDK.llm_client`` is created via ``gaia.llm.create_client`` which
   defaults to a ``LemonadeProvider`` (``src/gaia/chat/sdk.py``); and
   ``LemonadeProvider`` holds the real client at ``self._backend``
   (``src/gaia/llm/providers/lemonade.py``). So the wiring assertion is
   ``agent.chat.llm_client._backend.ctx_size_override == 16384``.

Construction avoids hitting a live Lemonade server by patching
``LemonadeManager.ensure_ready`` (the network-touching gate in
``Agent.__init__``) rather than mocking ``AgentSDK`` wholesale -- mocking
``AgentSDK`` would replace ``agent.chat`` with a ``MagicMock`` and make the
``_backend`` assertion untestable. ``LemonadeClient.__init__`` itself makes
no network calls (confirmed by reading ``lemonade_client.py`` -- read-only,
pre-existing code), so the rest of the object graph constructs for real.
"""

from unittest.mock import patch

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402

from gaia.llm.lemonade_manager import LemonadeManager  # noqa: E402


class _MinimalMailBackend:
    """Satisfies the GmailBackend protocol just enough for construction."""


class _MinimalCalendarBackend:
    """Satisfies the CalendarBackend protocol just enough for construction."""


def _make_config(tmp_path, *, ctx_size=None):
    return EmailAgentConfig(
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
        ctx_size=ctx_size,
    )


def _build_agent(tmp_path, monkeypatch, *, ctx_size=None):
    # AgentSDK is left real (unlike the mocked-SDK tests elsewhere in this
    # suite) so the LemonadeProvider -> LemonadeClient chain actually
    # builds and the ctx_size_override wiring is testable end-to-end. That
    # means memory init would otherwise touch a live embedder — disable it.
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    cfg = _make_config(tmp_path, ctx_size=ctx_size)
    with patch.object(LemonadeManager, "ensure_ready", return_value=True):
        return EmailTriageAgent(config=cfg)


def _build_agent_capturing_ensure_ready(tmp_path, monkeypatch, *, ctx_size=None):
    """Construct the agent and return (agent, ensure_ready_mock) so a test
    can assert the ``min_context_size`` floor every ``ensure_ready`` call
    was made with during construction."""
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    cfg = _make_config(tmp_path, ctx_size=ctx_size)
    with patch.object(LemonadeManager, "ensure_ready", return_value=True) as mock:
        agent = EmailTriageAgent(config=cfg)
    return agent, mock


class TestEmailAgentConfigCtxSize:
    def test_ctx_size_field_defaults_to_none(self):
        cfg = EmailAgentConfig()
        assert cfg.ctx_size is None

    def test_ctx_size_field_accepts_explicit_value(self):
        cfg = EmailAgentConfig(ctx_size=16384)
        assert cfg.ctx_size == 16384


class TestEmailAgentWiresCtxSizeOverride:
    def test_email_agent_wires_ctx_size_override(self, tmp_path, monkeypatch):
        agent = _build_agent(tmp_path, monkeypatch, ctx_size=16384)
        try:
            backend = agent.chat.llm_client._backend
            assert backend.ctx_size_override == 16384
        finally:
            close = getattr(agent, "close_db", None)
            if callable(close):
                close()

    def test_email_agent_leaves_override_unset_when_ctx_size_not_configured(
        self, tmp_path, monkeypatch
    ):
        """No ctx_size configured -> no override wired (None), so the
        client keeps LemonadeClient's default floor semantics."""
        agent = _build_agent(tmp_path, monkeypatch, ctx_size=None)
        try:
            backend = agent.chat.llm_client._backend
            assert backend.ctx_size_override is None
        finally:
            close = getattr(agent, "close_db", None)
            if callable(close):
                close()


class TestEnsureReadyFloorMatchesPin:
    """The base ``Agent.__init__`` calls ``LemonadeManager.ensure_ready`` at a
    ``min_context_size`` FLOOR (default 32768). That gate owns write paths
    (idle-server preload of the default model + a singleton-recheck reload)
    that can load/reload the model at the floor in the SAME process that wants
    a smaller pin. So a pinned process must lower the construction-time floor
    to the pin: ``ensure_ready(min_context_size=<pin>)``. An unpinned process
    keeps the historical default (32768).
    """

    def test_pinned_agent_passes_pin_as_ensure_ready_floor(self, tmp_path, monkeypatch):
        """ctx_size=16384 -> EVERY ensure_ready call carries
        min_context_size=16384 so the floor equals the pin. RED at HEAD:
        EmailTriageAgent passes no min_context_size today, so the base
        default (32768) reaches ensure_ready, not 16384."""
        agent, mock = _build_agent_capturing_ensure_ready(
            tmp_path, monkeypatch, ctx_size=16384
        )
        try:
            assert mock.call_count >= 1
            for call in mock.call_args_list:
                assert call.kwargs.get("min_context_size") == 16384
        finally:
            close = getattr(agent, "close_db", None)
            if callable(close):
                close()

    def test_unpinned_agent_keeps_default_ensure_ready_floor(
        self, tmp_path, monkeypatch
    ):
        """ctx_size=None -> EVERY ensure_ready call keeps the historical
        default floor (32768). Regression guard for the unpinned path: the
        base ``Agent.__init__`` default is 32768 and EmailTriageAgent must
        not disturb it when no pin is configured."""
        agent, mock = _build_agent_capturing_ensure_ready(
            tmp_path, monkeypatch, ctx_size=None
        )
        try:
            assert mock.call_count >= 1
            for call in mock.call_args_list:
                assert call.kwargs.get("min_context_size") == 32768
        finally:
            close = getattr(agent, "close_db", None)
            if callable(close):
                close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
