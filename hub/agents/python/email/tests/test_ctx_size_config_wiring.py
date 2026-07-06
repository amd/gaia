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


def _build_agent(tmp_path, *, ctx_size=None):
    cfg = EmailAgentConfig(
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
        ctx_size=ctx_size,
    )
    with patch.object(LemonadeManager, "ensure_ready", return_value=True):
        return EmailTriageAgent(config=cfg)


class TestEmailAgentConfigCtxSize:
    def test_ctx_size_field_defaults_to_none(self):
        cfg = EmailAgentConfig()
        assert cfg.ctx_size is None

    def test_ctx_size_field_accepts_explicit_value(self):
        cfg = EmailAgentConfig(ctx_size=16384)
        assert cfg.ctx_size == 16384


class TestEmailAgentWiresCtxSizeOverride:
    def test_email_agent_wires_ctx_size_override(self, tmp_path):
        agent = _build_agent(tmp_path, ctx_size=16384)

        backend = agent.chat.llm_client._backend
        assert backend.ctx_size_override == 16384

    def test_email_agent_leaves_override_unset_when_ctx_size_not_configured(
        self, tmp_path
    ):
        """No ctx_size configured -> no override wired (None), so the
        client keeps LemonadeClient's default floor semantics."""
        agent = _build_agent(tmp_path, ctx_size=None)

        backend = agent.chat.llm_client._backend
        assert backend.ctx_size_override is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
