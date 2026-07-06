# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Fixed contract (#1892): ``EmailAgentConfig.ctx_size`` wires down to the
concrete ``LemonadeClient`` as its instance-scoped ``ctx_size_override``.

The email eval path must be able to pin the model's context window to an
exact size (the 16K target / 32K max envelope). That pin travels:

    EmailAgentConfig(ctx_size=...) -> EmailTriageAgent.__init__ ->
    Agent.__init__ -> AgentSDK -> LemonadeProvider ->
    LemonadeClient(ctx_size_override=...)

so the concrete client is reachable at
``agent.chat.llm_client._backend`` (a real ``LemonadeClient`` — this test
deliberately does NOT mock ``AgentSDK``, so the whole provider chain is
exercised).

RED state today: ``EmailAgentConfig`` has no ``ctx_size`` field, so the
``EmailAgentConfig(ctx_size=16384)`` call raises ``TypeError`` (unexpected
keyword argument). Once the field + wiring land, the assertion holds.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path / import bootstrap (mirrors the sibling email tests).
# parents[0] = tests/, [1] = email/, [2] = python/, [3] = agents/,
# [4] = hub/, [5] = repo-root
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402


class _MinimalMailBackend:
    """Satisfies the GmailBackend protocol just enough for construction."""

    pass


class _MinimalCalendarBackend:
    """Satisfies the CalendarBackend protocol just enough for construction."""

    pass


def _build_agent(tmp_path: Path, *, ctx_size: int) -> EmailTriageAgent:
    """Construct a real EmailTriageAgent hermetically.

    Unlike the other email unit tests, ``AgentSDK`` is left REAL so the
    LemonadeProvider -> LemonadeClient chain actually builds and the
    ``ctx_size_override`` wiring can be asserted end-to-end.
    ``LemonadeManager.ensure_ready`` is patched to a no-op so no live
    server is required, and memory is disabled so init_memory stays
    hermetic (FTS5-only, no embedder network call).
    """
    cfg = EmailAgentConfig(
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
        ctx_size=ctx_size,
    )

    old = os.environ.get("GAIA_MEMORY_DISABLED")
    os.environ["GAIA_MEMORY_DISABLED"] = "1"
    try:
        with patch(
            "gaia.llm.lemonade_manager.LemonadeManager.ensure_ready",
            return_value=True,
        ):
            return EmailTriageAgent(config=cfg)
    finally:
        if old is None:
            del os.environ["GAIA_MEMORY_DISABLED"]
        else:
            os.environ["GAIA_MEMORY_DISABLED"] = old


def test_email_agent_wires_ctx_size_override(tmp_path):
    agent = _build_agent(tmp_path, ctx_size=16384)
    try:
        assert agent.chat.llm_client._backend.ctx_size_override == 16384
    finally:
        # close_db is defined by DatabaseMixin; guard in case construction
        # partially failed on a future refactor.
        close = getattr(agent, "close_db", None)
        if callable(close):
            close()
