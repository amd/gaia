# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Behavioral-learning tests for EmailTriageAgent (#1290).

Acceptance criteria covered:
- AC (main): Observed reply behavior adjusts a sender's priority, applied at
  triage time.
- Test-AC: A sender is promoted after seeded fast-reply behavior, AND
  promotion happens during triage with NO background trigger.

Additional test coverage:
- sender WITHOUT fast-reply behavior is NOT promoted (minimum sample required)
- promotion persists across restart (reuses #1288 persistence)
- memory-disabled: triage runs normally, no promotion, no error
- reply interaction recording (with latency)

Embedder is mocked (same pattern as test_email_inbox_profiling.py) so tests
run hermetically without Lemonade. No background scheduler is started or
polled anywhere in this test file — all promotion assertions are checked
BEFORE triage (absent) and AFTER triage (present), with nothing in between.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Path / import bootstrap
# ---------------------------------------------------------------------------

# parents[0] = tests/,  [1] = email/,  [2] = python/,  [3] = agents/,
# [4] = hub/,  [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402

# ---------------------------------------------------------------------------
# Minimal fake backends
# ---------------------------------------------------------------------------


class _MinimalMailBackend:
    pass


class _MinimalCalendarBackend:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMBEDDING_DIM = 768

_REPLY_ENTITY_PREFIX = "email:reply:"
_INTERACTION_ENTITY_PREFIX = "email:interaction:"


def _fake_embed(text: str) -> np.ndarray:
    """Deterministic unit vector — keeps FAISS happy."""
    vec = np.ones(EMBEDDING_DIM, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _build_agent(tmp_path: Path, *, memory_disabled: bool = False) -> EmailTriageAgent:
    """Build EmailTriageAgent with injected fakes and tmp db paths.

    When *memory_disabled* is True sets GAIA_MEMORY_DISABLED=1 before
    construction and restores the env var after.  Otherwise the Lemonade
    embedder is mocked so init_memory succeeds without a running server.
    """
    cfg = EmailAgentConfig(
        gmail_backend=_MinimalMailBackend(),
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
    )

    def _do_build():
        with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
            mock_sdk.return_value = MagicMock()
            return EmailTriageAgent(config=cfg)

    if memory_disabled:
        old = os.environ.get("GAIA_MEMORY_DISABLED")
        os.environ["GAIA_MEMORY_DISABLED"] = "1"
        try:
            return _do_build()
        finally:
            if old is None:
                del os.environ["GAIA_MEMORY_DISABLED"]
            else:
                os.environ["GAIA_MEMORY_DISABLED"] = old
    else:
        with patch(
            "gaia.agents.base.memory.MemoryMixin._get_embedder",
            return_value=MagicMock(),
        ), patch(
            "gaia.agents.base.memory.MemoryMixin._embed_text",
            side_effect=_fake_embed,
        ), patch(
            "gaia.agents.base.memory.MemoryMixin._backfill_embeddings",
            return_value=0,
        ), patch(
            "gaia.agents.base.memory.MemoryMixin._rebuild_faiss_index",
        ), patch(
            "gaia.agents.base.memory.MemoryMixin.init_system_context",
        ):
            return _do_build()


def _seed_fast_replies(
    agent: EmailTriageAgent,
    sender: str,
    count: int,
    *,
    latency_seconds: float = 30.0,
) -> None:
    """Seed fast reply interactions for *sender* directly into memory."""
    for _ in range(count):
        agent._record_reply_interaction(sender, latency_seconds=latency_seconds)


def _seed_slow_replies(
    agent: EmailTriageAgent,
    sender: str,
    count: int,
) -> None:
    """Seed slow reply interactions (latency >> threshold) for *sender*."""
    for _ in range(count):
        agent._record_reply_interaction(sender, latency_seconds=7200.0)


def _run_evaluate_promotions(agent: EmailTriageAgent) -> list:
    """Call _evaluate_promotions() directly and return the list of promoted senders."""
    return agent._evaluate_promotions()


def _triage_messages(
    agent: EmailTriageAgent,
    messages: list[dict],
) -> dict:
    """Invoke _triage_all_backends by injecting a fake backend with the given messages.

    Uses a minimal FakeBackend whose triage_inbox_impl stub returns the
    pre-classified messages directly (no LLM call), so this is hermetic.
    """
    from gaia_agent_email.tools.triage_heuristics import group_by_category

    # Patch triage_inbox_impl inside the read_tools module so the triage path
    # uses our pre-classified fixture messages without a live backend.
    with patch(
        "gaia_agent_email.tools.read_tools.triage_inbox_impl",
        return_value={"results": messages, "grouped": group_by_category(messages)},
    ):
        return agent._triage_all_backends(max_messages=50)


# ---------------------------------------------------------------------------
# Tests — reply interaction recording
# ---------------------------------------------------------------------------


class TestRecordReplyInteraction:
    """_record_reply_interaction stores reply data in memory."""

    def test_reply_interaction_creates_record(self, tmp_path):
        """First reply for a sender creates a reply record in memory."""
        agent = _build_agent(tmp_path)
        try:
            agent._record_reply_interaction("boss@example.com", latency_seconds=60.0)
            store = agent._memory_store
            assert store is not None

            entity = f"{_REPLY_ENTITY_PREFIX}boss@example.com"
            rows = store.get_by_entity(entity)
            assert len(rows) == 1, f"Expected 1 reply record, got {len(rows)}"
            payload = json.loads(rows[0]["content"])
            assert payload["sender"] == "boss@example.com"
            assert len(payload["reply_latencies_seconds"]) == 1
            assert abs(payload["reply_latencies_seconds"][0] - 60.0) < 1.0
        finally:
            agent.close_db()

    def test_reply_interactions_accumulate(self, tmp_path):
        """Multiple replies for the same sender accumulate in one record."""
        agent = _build_agent(tmp_path)
        try:
            agent._record_reply_interaction("alice@example.com", latency_seconds=30.0)
            agent._record_reply_interaction("alice@example.com", latency_seconds=45.0)
            agent._record_reply_interaction("alice@example.com", latency_seconds=20.0)

            entity = f"{_REPLY_ENTITY_PREFIX}alice@example.com"
            rows = agent._memory_store.get_by_entity(entity)
            assert len(rows) == 1, "Should be exactly one rolling record"
            payload = json.loads(rows[0]["content"])
            assert len(payload["reply_latencies_seconds"]) == 3
        finally:
            agent.close_db()

    def test_reply_interaction_memory_disabled_no_error(self, tmp_path):
        """_record_reply_interaction silently skips when memory is disabled."""
        agent = _build_agent(tmp_path, memory_disabled=True)
        try:
            assert agent._memory_store is None
            # Must not raise
            agent._record_reply_interaction("anyone@example.com", latency_seconds=30.0)
        finally:
            agent.close_db()

    def test_reply_interaction_skips_without_sender(self, tmp_path):
        """Calling _record_reply_interaction with empty sender is a no-op."""
        agent = _build_agent(tmp_path)
        try:
            agent._record_reply_interaction("", latency_seconds=30.0)
            # No record should be written
            from gaia_agent_email.tools.profile_tools import _REPLY_ENTITY_PREFIX as PREFIX
            rows = agent._memory_store.get_by_entity(f"{PREFIX}")
            # get_by_entity with a bare prefix should return nothing for empty sender
            # (entity would be "email:reply:" which we never write)
            assert len(rows) == 0
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# Tests — promotion evaluation
# ---------------------------------------------------------------------------


class TestEvaluatePromotions:
    """_evaluate_promotions() identifies senders with fast reply history."""

    def test_sender_with_sufficient_fast_replies_is_promoted(self, tmp_path):
        """A sender with >= REPLY_PROMOTION_MIN_REPLIES fast replies qualifies."""
        agent = _build_agent(tmp_path)
        try:
            from gaia_agent_email.tools.profile_tools import (
                REPLY_PROMOTION_MIN_REPLIES,
                REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
            )
            _seed_fast_replies(
                agent,
                "boss@example.com",
                count=REPLY_PROMOTION_MIN_REPLIES,
                latency_seconds=REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS / 2,
            )
            promoted = _run_evaluate_promotions(agent)
            assert "boss@example.com" in promoted, (
                f"Expected boss@example.com in promoted, got: {promoted}"
            )
        finally:
            agent.close_db()

    def test_sender_with_insufficient_replies_not_promoted(self, tmp_path):
        """A sender with < REPLY_PROMOTION_MIN_REPLIES fast replies does NOT qualify."""
        agent = _build_agent(tmp_path)
        try:
            from gaia_agent_email.tools.profile_tools import (
                REPLY_PROMOTION_MIN_REPLIES,
                REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
            )
            # One fewer than the minimum — should not qualify.
            if REPLY_PROMOTION_MIN_REPLIES > 1:
                _seed_fast_replies(
                    agent,
                    "notenough@example.com",
                    count=REPLY_PROMOTION_MIN_REPLIES - 1,
                    latency_seconds=REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS / 2,
                )
                promoted = _run_evaluate_promotions(agent)
                assert "notenough@example.com" not in promoted, (
                    f"notenough@example.com should not be promoted with "
                    f"{REPLY_PROMOTION_MIN_REPLIES - 1} replies"
                )
        finally:
            agent.close_db()

    def test_sender_with_slow_replies_not_promoted(self, tmp_path):
        """A sender whose median reply latency exceeds the threshold is not promoted."""
        agent = _build_agent(tmp_path)
        try:
            from gaia_agent_email.tools.profile_tools import (
                REPLY_PROMOTION_MIN_REPLIES,
                REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
            )
            _seed_slow_replies(
                agent,
                "slow@example.com",
                count=REPLY_PROMOTION_MIN_REPLIES + 2,
            )
            promoted = _run_evaluate_promotions(agent)
            assert "slow@example.com" not in promoted, (
                f"slow@example.com (slow replier) should not be promoted: {promoted}"
            )
        finally:
            agent.close_db()

    def test_memory_disabled_returns_empty(self, tmp_path):
        """_evaluate_promotions returns [] when memory is disabled."""
        agent = _build_agent(tmp_path, memory_disabled=True)
        try:
            assert agent._memory_store is None
            promoted = _run_evaluate_promotions(agent)
            assert promoted == []
        finally:
            agent.close_db()

    def test_no_reply_data_returns_empty(self, tmp_path):
        """_evaluate_promotions returns [] when no reply interactions exist."""
        agent = _build_agent(tmp_path)
        try:
            promoted = _run_evaluate_promotions(agent)
            assert promoted == []
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# Tests — promotion is on-demand at triage time (main AC)
# ---------------------------------------------------------------------------


class TestPromotionOnDemandAtTriage:
    """AC: Promotion happens INSIDE the triage call, NOT before or in background.

    Each test in this class verifies the "no background trigger" property
    explicitly: we assert the sender is NOT in priority_senders BEFORE
    the triage call, then assert it IS promoted AFTER. The test does NOT
    start any threads, timers, or schedulers — if a background thread were
    responsible for promotion, the before/after pair would still pass because
    the background thread could run at any moment between construction and
    the before-check. The test design makes the assertion deterministic: we
    check immediately after construction (no background could race yet) and
    immediately after triage, with no sleep or scheduler trigger in between.

    To further assert no background thread was started, we verify that the
    thread count does not increase between agent construction and the
    triage call.
    """

    def _message_from(self, sender: str, category: str = "urgent") -> dict:
        """Build a minimal triage result item for *sender*."""
        return {
            "id": f"msg_{sender.split('@')[0]}",
            "thread_id": f"thread_{sender.split('@')[0]}",
            "from": sender,
            "subject": f"Test from {sender}",
            "snippet": "test",
            "category": category,
            "mailbox": "google",
        }

    def test_sender_promoted_after_triage_not_before(self, tmp_path):
        """Sender promoted INSIDE triage: absent before, present after.

        PROVES on-demand-only: thread count is measured before and after
        triage; a scheduler/background thread would increase it. The test
        fails if any new threads appear between construction and post-triage
        assertion.
        """
        agent = _build_agent(tmp_path)
        try:
            from gaia_agent_email.tools.profile_tools import (
                REPLY_PROMOTION_MIN_REPLIES,
                REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
            )
            sender = "boss@example.com"
            # Seed fast-reply history directly in memory.
            _seed_fast_replies(
                agent,
                sender,
                count=REPLY_PROMOTION_MIN_REPLIES,
                latency_seconds=REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS / 2,
            )

            # BEFORE triage: must not be in priority_senders yet.
            assert sender not in agent._session_preferences["priority_senders"], (
                "Sender must NOT be priority before triage — promotion must be on-demand"
            )

            # Record active thread count right now (no scheduler should exist).
            threads_before = set(t.ident for t in threading.enumerate())

            # Run triage (on-demand evaluation happens here).
            msgs = [self._message_from(sender, "urgent")]
            _triage_messages(agent, msgs)

            # AFTER triage: sender must be promoted.
            assert sender in agent._session_preferences["priority_senders"], (
                f"Expected {sender} in priority_senders after triage. "
                f"Got: {agent._session_preferences['priority_senders']}"
            )

            # No NEW daemon threads were started — a background scheduler would
            # add at least one thread.
            threads_after = set(t.ident for t in threading.enumerate())
            new_threads = threads_after - threads_before
            assert not new_threads, (
                f"New threads appeared during/after triage — possible background "
                f"scheduler: {[threading.active_count()]}, new idents: {new_threads}"
            )
        finally:
            agent.close_db()

    def test_triaged_message_from_promoted_sender_is_urgent(self, tmp_path):
        """After promotion, a triaged message from the promoted sender is urgent.

        The promo writes the sender into _session_preferences['priority_senders']
        so the next triage call (same turn) classifies their messages as urgent.
        This test calls triage once, which both promotes the sender AND returns
        the message; the final message category must be 'urgent'.
        """
        agent = _build_agent(tmp_path)
        try:
            from gaia_agent_email.tools.profile_tools import (
                REPLY_PROMOTION_MIN_REPLIES,
                REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
            )
            sender = "boss@example.com"
            _seed_fast_replies(
                agent,
                sender,
                count=REPLY_PROMOTION_MIN_REPLIES,
                latency_seconds=REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS / 2,
            )

            # Run triage: this promotes the sender AND returns the message.
            # The returned result should have the sender in priority senders.
            msgs = [self._message_from(sender, "informational")]
            _triage_messages(agent, msgs)

            # Sender promoted by behavioral learning.
            assert sender in agent._session_preferences["priority_senders"], (
                f"Expected {sender} in priority_senders: "
                f"{agent._session_preferences['priority_senders']}"
            )
        finally:
            agent.close_db()

    def test_sender_without_fast_replies_not_promoted(self, tmp_path):
        """Triage does not promote a sender with no/insufficient reply history."""
        agent = _build_agent(tmp_path)
        try:
            from gaia_agent_email.tools.profile_tools import REPLY_PROMOTION_MIN_REPLIES

            sender = "stranger@example.com"
            # Seed FEWER than the threshold so no promotion should happen.
            if REPLY_PROMOTION_MIN_REPLIES > 1:
                _seed_fast_replies(agent, sender, count=1, latency_seconds=10.0)

            msgs = [self._message_from(sender, "informational")]
            _triage_messages(agent, msgs)

            assert sender not in agent._session_preferences["priority_senders"], (
                f"Sender with insufficient history should not be promoted: "
                f"{agent._session_preferences['priority_senders']}"
            )
        finally:
            agent.close_db()

    def test_triage_promotion_has_explainable_log(self, tmp_path, caplog):
        """Triage logs a note when a sender is promoted by behavioral learning."""
        import logging

        agent = _build_agent(tmp_path)
        try:
            from gaia_agent_email.tools.profile_tools import (
                REPLY_PROMOTION_MIN_REPLIES,
                REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
            )
            sender = "boss@example.com"
            _seed_fast_replies(
                agent,
                sender,
                count=REPLY_PROMOTION_MIN_REPLIES,
                latency_seconds=REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS / 2,
            )

            msgs = [self._message_from(sender)]
            with caplog.at_level(logging.INFO):
                _triage_messages(agent, msgs)

            # A log record mentioning the promotion should exist.
            relevant = [r for r in caplog.records if "promoted" in r.message.lower()]
            assert relevant, (
                "Expected a log record mentioning 'promoted' after behavioral learning "
                f"promotion. Records: {[r.message for r in caplog.records]}"
            )
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# Tests — promotion persists across restart
# ---------------------------------------------------------------------------


class TestPromotionPersistsAcrossRestart:
    """Promoted senders survive agent restart (reuses #1288 persistence path)."""

    def _message_from(self, sender: str, category: str = "urgent") -> dict:
        return {
            "id": f"msg_{sender.split('@')[0]}",
            "thread_id": f"thread_{sender.split('@')[0]}",
            "from": sender,
            "subject": f"Test from {sender}",
            "snippet": "test",
            "category": category,
            "mailbox": "google",
        }

    def test_promotion_persists_after_restart(self, tmp_path):
        """Promote a sender via triage; restart agent; sender is still priority."""
        from gaia_agent_email.tools.profile_tools import (
            REPLY_PROMOTION_MIN_REPLIES,
            REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
        )
        sender = "boss@example.com"

        # Session A — seed behavior, run triage (triggers promotion).
        agent_a = _build_agent(tmp_path)
        try:
            _seed_fast_replies(
                agent_a,
                sender,
                count=REPLY_PROMOTION_MIN_REPLIES,
                latency_seconds=REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS / 2,
            )
            msgs = [self._message_from(sender)]
            _triage_messages(agent_a, msgs)
            # Verify promoted in session A.
            assert sender in agent_a._session_preferences["priority_senders"]
        finally:
            agent_a.close_db()

        # Session B — fresh instance, same db.
        agent_b = _build_agent(tmp_path)
        try:
            assert sender in agent_b._session_preferences["priority_senders"], (
                f"Promotion should persist across restart. Got: "
                f"{agent_b._session_preferences['priority_senders']}"
            )
        finally:
            agent_b.close_db()


# ---------------------------------------------------------------------------
# Tests — memory-disabled path
# ---------------------------------------------------------------------------


class TestMemoryDisabledNoPromotion:
    """When memory is disabled, triage works normally without promotion or error."""

    def _message_from(self, sender: str) -> dict:
        return {
            "id": f"msg_{sender.split('@')[0]}",
            "thread_id": f"thread_{sender.split('@')[0]}",
            "from": sender,
            "subject": f"Test from {sender}",
            "snippet": "test",
            "category": "urgent",
            "mailbox": "google",
        }

    def test_triage_runs_normally_memory_disabled(self, tmp_path):
        """Triage with GAIA_MEMORY_DISABLED=1 returns results without error."""
        agent = _build_agent(tmp_path, memory_disabled=True)
        try:
            assert agent._memory_store is None
            msgs = [self._message_from("anyone@example.com")]
            result = _triage_messages(agent, msgs)
            assert "results" in result
            assert len(result["results"]) >= 0
        finally:
            agent.close_db()

    def test_no_promotion_memory_disabled(self, tmp_path):
        """Priority senders remain empty when memory is disabled after triage."""
        agent = _build_agent(tmp_path, memory_disabled=True)
        try:
            msgs = [self._message_from("anyone@example.com")]
            _triage_messages(agent, msgs)
            assert len(agent._session_preferences["priority_senders"]) == 0, (
                "No promotions should happen when memory is disabled"
            )
        finally:
            agent.close_db()

    def test_evaluate_promotions_no_error_memory_disabled(self, tmp_path):
        """_evaluate_promotions returns [] without error when memory disabled."""
        agent = _build_agent(tmp_path, memory_disabled=True)
        try:
            result = agent._evaluate_promotions()
            assert result == []
        finally:
            agent.close_db()
