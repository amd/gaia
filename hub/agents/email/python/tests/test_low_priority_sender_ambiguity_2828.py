# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regression tests for #2828: ``set_low_priority_sender``/``set_priority_sender``
must apply to the exact address given, and must fail loudly (naming the
candidates) rather than silently colliding with a near-duplicate address
already configured.

#2828's suspected cause — tool-side fuzzy resolution against mailbox
contacts — is refuted by a static read of ``preference_tools.py``: the
tools take a bare ``email`` string, normalize it (lowercase/strip only,
no lookup), and store exactly that, already echoing the stored value back
in ``added``. These tests exercise the deterministic guard added in its
place: a near-duplicate collision against the agent's OWN preference
store (the only "known senders" a tool with no mailbox contact list can
see) is rejected loudly; a genuinely new, unique address is not.

Evidence discipline: these are unit-level tool invocations against a real
EmailTriageAgent with fakes standing in for backends/LLM/embedder — no
live mailbox is available in this environment, so the issue's exact
end-to-end model-fabrication path is not reproduced here. What IS proven
is the tool-level contract the fix adds.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.tools.preference_tools import (  # noqa: E402
    _find_ambiguous_senders,
)


class _MinimalMailBackend:
    pass


class _MinimalCalendarBackend:
    pass


EMBEDDING_DIM = 768


def _fake_embed(text: str) -> np.ndarray:
    vec = np.ones(EMBEDDING_DIM, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _build_agent(tmp_path: Path) -> EmailTriageAgent:
    cfg = EmailAgentConfig(
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )
    with (
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
        patch(
            "gaia.agents.base.memory.MemoryMixin._get_embedder",
            return_value=MagicMock(),
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin._embed_text",
            side_effect=_fake_embed,
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin._backfill_embeddings",
            return_value=0,
        ),
        patch("gaia.agents.base.memory.MemoryMixin._rebuild_faiss_index"),
        patch("gaia.agents.base.memory.MemoryMixin.init_system_context"),
    ):
        mock_sdk.return_value = MagicMock()
        return EmailTriageAgent(config=cfg)


def _invoke(tool_name: str, *args) -> dict:
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get(tool_name)
    assert entry is not None, f"{tool_name} not registered"
    return json.loads(entry["function"](*args))


class TestFindAmbiguousSendersHelper:
    """Unit coverage of the pure similarity helper, independent of the agent."""

    def test_near_duplicate_local_part_is_ambiguous(self):
        known = {"tomasz.iniewicz@gmail.com"}
        result = _find_ambiguous_senders(
            "tomasz.testingiewicz@outlook.com", known
        )
        assert result == ["tomasz.iniewicz@gmail.com"]

    def test_unrelated_address_is_not_ambiguous(self):
        known = {"boss@company.com"}
        assert _find_ambiguous_senders("newsletter@stripe.com", known) == []

    def test_exact_match_is_not_ambiguous(self):
        known = {"boss@company.com"}
        assert _find_ambiguous_senders("boss@company.com", known) == []

    def test_empty_known_set_is_never_ambiguous(self):
        assert _find_ambiguous_senders("anyone@example.com", set()) == []


class TestSetLowPrioritySenderAmbiguity:
    """#2828 regression: address A explicitly named must land on A, and a
    near-duplicate B already known to the agent must trigger a loud failure
    rather than a silent misapplication."""

    def test_ambiguous_address_is_rejected_naming_candidates(self, tmp_path):
        agent = _build_agent(tmp_path)
        try:
            # B is already known (configured earlier, a prior turn).
            b_result = _invoke(
                "set_low_priority_sender", "tomasz.iniewicz@gmail.com"
            )
            assert b_result["ok"] is True

            # Now the user explicitly names A, a near-duplicate of B.
            a_result = _invoke(
                "set_low_priority_sender", "tomasz.testingiewicz@outlook.com"
            )
            assert a_result["ok"] is False, (
                "an address colliding with an already-known near-duplicate "
                f"must fail loudly, not silently apply. Got: {a_result}"
            )
            assert "tomasz.iniewicz@gmail.com" in a_result["error"]

            # A must NOT have been silently added to either list.
            prefs = agent._session_preferences
            assert "tomasz.testingiewicz@outlook.com" not in prefs[
                "low_priority_senders"
            ]
            assert "tomasz.testingiewicz@outlook.com" not in prefs[
                "priority_senders"
            ]
            # B — the address actually requested and stored — is untouched.
            assert "tomasz.iniewicz@gmail.com" in prefs["low_priority_senders"]
        finally:
            agent.close_db()

    def test_unique_address_is_applied_to_exactly_itself(self, tmp_path):
        """No collision — a brand-new, unrelated address is stored as given."""
        agent = _build_agent(tmp_path)
        try:
            result = _invoke(
                "set_low_priority_sender", "newsletter@stripe.com"
            )
            assert result["ok"] is True
            assert result["data"]["added"] == "newsletter@stripe.com"
            assert (
                "newsletter@stripe.com"
                in agent._session_preferences["low_priority_senders"]
            )
        finally:
            agent.close_db()

    def test_removal_by_same_phrasing_clears_the_exact_address(self, tmp_path):
        """Removal acts on an exact match — the address requested is the one
        cleared, independent of the ambiguity guard on the set path."""
        agent = _build_agent(tmp_path)
        try:
            _invoke("set_low_priority_sender", "tomasz.iniewicz@gmail.com")
            removal = _invoke(
                "remove_low_priority_sender", "tomasz.iniewicz@gmail.com"
            )
            assert removal["ok"] is True
            assert removal["data"]["removed"] is True
            assert (
                "tomasz.iniewicz@gmail.com"
                not in agent._session_preferences["low_priority_senders"]
            )
        finally:
            agent.close_db()

    def test_ambiguity_checked_against_priority_list_too(self, tmp_path):
        """The guard consults BOTH sender lists, not just the one being set —
        a near-duplicate of a priority sender must also be rejected when
        setting low-priority (and vice versa)."""
        agent = _build_agent(tmp_path)
        try:
            _invoke("set_priority_sender", "tomasz.iniewicz@gmail.com")
            result = _invoke(
                "set_low_priority_sender", "tomasz.testingiewicz@outlook.com"
            )
            assert result["ok"] is False
            assert "tomasz.iniewicz@gmail.com" in result["error"]
        finally:
            agent.close_db()


class TestSetPrioritySenderAmbiguity:
    def test_ambiguous_address_is_rejected_naming_candidates(self, tmp_path):
        agent = _build_agent(tmp_path)
        try:
            _invoke("set_priority_sender", "bob.smith@example.com")
            result = _invoke("set_priority_sender", "bob.smyth@example.com")
            assert result["ok"] is False
            assert "bob.smith@example.com" in result["error"]
            assert (
                "bob.smyth@example.com"
                not in agent._session_preferences["priority_senders"]
            )
        finally:
            agent.close_db()
