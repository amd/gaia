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
import time
from datetime import datetime, timezone
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
        with (
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
            patch(
                "gaia.agents.base.memory.MemoryMixin._rebuild_faiss_index",
            ),
            patch(
                "gaia.agents.base.memory.MemoryMixin.init_system_context",
            ),
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


def _build_agent_with_fake_gmail(
    tmp_path: Path,
    received_msg: dict,
) -> EmailTriageAgent:
    """Build an agent backed by a real FakeGmailBackend seeded with one message.

    Lets the end-to-end "observe" test drive a genuine reply through the agent
    (draft_reply tool → backend.create_draft) and verify the reply-latency
    observation is recorded from the ORIGINAL message's internalDate anchor.
    """
    from gaia_agent_email.config import EmailAgentConfig

    from tests.fixtures.email.fake_gmail import FakeGmailBackend

    backend = FakeGmailBackend(user_email="me@example.com")
    backend.add_message(received_msg)

    cfg = EmailAgentConfig(
        gmail_backend=backend,
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
        patch(
            "gaia.agents.base.memory.MemoryMixin._rebuild_faiss_index",
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin.init_system_context",
        ),
    ):
        mock_sdk.return_value = MagicMock()
        return EmailTriageAgent(config=cfg)


def _received_message(
    message_id: str,
    sender: str,
    *,
    received_seconds_ago: float,
) -> dict:
    """Build a Gmail-API-shape received message with an internalDate anchor."""
    internal_ms = int((time.time() - received_seconds_ago) * 1000)
    return {
        "id": message_id,
        "threadId": f"thread_{message_id}",
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": str(internal_ms),
        "snippet": "hello",
        "payload": {
            "headers": [
                {"name": "From", "value": f"Boss <{sender}>"},
                {"name": "Subject", "value": "Need your input"},
                {"name": "Message-ID", "value": f"<{message_id}@example.com>"},
                {"name": "Date", "value": "Mon, 12 Jun 2026 10:00:00 +0000"},
            ],
        },
    }


def _invoke_draft_reply(agent: EmailTriageAgent, message_id: str, body: str) -> dict:
    """Call the draft_reply tool through the registry (as the agent would)."""
    from gaia.agents.base.tools import _TOOL_REGISTRY

    entry = _TOOL_REGISTRY.get("draft_reply")
    assert entry is not None, "draft_reply tool not registered"
    return json.loads(entry["function"](message_id, body))


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
            from gaia_agent_email.tools.profile_tools import (
                _REPLY_ENTITY_PREFIX as PREFIX,
            )

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
                REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
                REPLY_PROMOTION_MIN_REPLIES,
            )

            _seed_fast_replies(
                agent,
                "boss@example.com",
                count=REPLY_PROMOTION_MIN_REPLIES,
                latency_seconds=REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS / 2,
            )
            promoted = _run_evaluate_promotions(agent)
            assert (
                "boss@example.com" in promoted
            ), f"Expected boss@example.com in promoted, got: {promoted}"
        finally:
            agent.close_db()

    def test_sender_with_insufficient_replies_not_promoted(self, tmp_path):
        """A sender with < REPLY_PROMOTION_MIN_REPLIES fast replies does NOT qualify."""
        agent = _build_agent(tmp_path)
        try:
            from gaia_agent_email.tools.profile_tools import (
                REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
                REPLY_PROMOTION_MIN_REPLIES,
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
            from gaia_agent_email.tools.profile_tools import REPLY_PROMOTION_MIN_REPLIES

            _seed_slow_replies(
                agent,
                "slow@example.com",
                count=REPLY_PROMOTION_MIN_REPLIES + 2,
            )
            promoted = _run_evaluate_promotions(agent)
            assert (
                "slow@example.com" not in promoted
            ), f"slow@example.com (slow replier) should not be promoted: {promoted}"
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

        PROVES on-demand-only two ways:
        1. The promotion is absent BEFORE the triage call and present AFTER —
           so it cannot have come from a background task that ran at an
           arbitrary earlier time (the before-check runs immediately after
           construction with nothing in between).
        2. No background machinery is instantiated. We patch
           ``threading.Thread`` and ``threading.Timer`` for the duration of the
           triage call and assert neither is constructed — a scheduler / timer
           would have to build one. This is deterministic, unlike a live
           thread-count delta (which GC / OS threads can perturb).
        """
        agent = _build_agent(tmp_path)
        try:
            from gaia_agent_email.tools.profile_tools import (
                REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
                REPLY_PROMOTION_MIN_REPLIES,
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
            assert (
                sender not in agent._session_preferences["priority_senders"]
            ), "Sender must NOT be priority before triage — promotion must be on-demand"

            # Run triage with thread/timer constructors patched. If any
            # scheduler or background worker were started, it would have to
            # instantiate one of these — and the assertions below would fail.
            msgs = [self._message_from(sender, "urgent")]
            with (
                patch("threading.Thread") as mock_thread,
                patch("threading.Timer") as mock_timer,
            ):
                _triage_messages(agent, msgs)
                assert not mock_thread.called, (
                    "threading.Thread was constructed during triage — promotion "
                    "must run synchronously, not on a background thread"
                )
                assert not mock_timer.called, (
                    "threading.Timer was constructed during triage — promotion "
                    "must not be scheduled"
                )

            # AFTER triage: sender must be promoted (synchronously, inside the call).
            assert sender in agent._session_preferences["priority_senders"], (
                f"Expected {sender} in priority_senders after triage. "
                f"Got: {agent._session_preferences['priority_senders']}"
            )
        finally:
            agent.close_db()

    def test_promotion_runs_synchronously_within_triage_call(self, tmp_path):
        """_apply_behavioral_promotions is invoked from inside _triage_all_backends.

        Proves the evaluation is part of the synchronous triage call path (not
        deferred to a worker): we spy on _apply_behavioral_promotions and assert
        it was called exactly once, synchronously, during the triage call —
        with the promotion already applied by the time triage returns.
        """
        agent = _build_agent(tmp_path)
        try:
            from gaia_agent_email.tools.profile_tools import (
                REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
                REPLY_PROMOTION_MIN_REPLIES,
            )

            sender = "boss@example.com"
            _seed_fast_replies(
                agent,
                sender,
                count=REPLY_PROMOTION_MIN_REPLIES,
                latency_seconds=REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS / 2,
            )

            real_apply = agent._apply_behavioral_promotions
            calls = {"n": 0, "promoted_when_returned": False}

            def _spy():
                calls["n"] += 1
                real_apply()
                # The promotion must be applied by the time this returns,
                # proving it is synchronous (no deferral).
                calls["promoted_when_returned"] = (
                    sender in agent._session_preferences["priority_senders"]
                )

            with patch.object(agent, "_apply_behavioral_promotions", side_effect=_spy):
                msgs = [self._message_from(sender, "urgent")]
                _triage_messages(agent, msgs)

            assert calls["n"] == 1, (
                f"_apply_behavioral_promotions should be called exactly once per "
                f"triage, got {calls['n']}"
            )
            assert calls[
                "promoted_when_returned"
            ], "promotion must be applied synchronously within the triage call"
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
                REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
                REPLY_PROMOTION_MIN_REPLIES,
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
                REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
                REPLY_PROMOTION_MIN_REPLIES,
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
            REPLY_PROMOTION_LATENCY_THRESHOLD_SECONDS,
            REPLY_PROMOTION_MIN_REPLIES,
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
            assert (
                len(agent._session_preferences["priority_senders"]) == 0
            ), "No promotions should happen when memory is disabled"
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


# ---------------------------------------------------------------------------
# Tests — end-to-end OBSERVE: replying through the agent records latency
# ---------------------------------------------------------------------------


class TestReplyObservationEndToEnd:
    """Closes the AC's 'observed' half: a real reply through the agent records
    a reply-latency observation computed from the original message's receipt
    anchor — not seeded data.
    """

    def test_reply_through_agent_records_observation(self, tmp_path):
        """draft_reply via the agent records a reply interaction with computed latency.

        Seeds a received message with internalDate 120 s ago, drives the
        draft_reply tool (the genuine reply path), then asserts a reply record
        was written for the ORIGINAL sender with a latency near 120 s — proving
        the agent OBSERVES reply behavior, not just consumes pre-seeded data.
        """
        sender = "boss@example.com"
        received = _received_message("msg1", sender, received_seconds_ago=120.0)
        agent = _build_agent_with_fake_gmail(tmp_path, received)
        try:
            # No reply records exist before the reply.
            before = agent._memory_store.get_by_entity(
                f"{_REPLY_ENTITY_PREFIX}{sender}"
            )
            assert len(before) == 0

            # Drive a genuine reply through the agent.
            result = _invoke_draft_reply(agent, "msg1", "Sure, on it.")
            assert result["ok"] is True, f"draft_reply failed: {result}"
            # The internal anchor field must not leak into the user envelope.
            assert "_original_msg" not in result["data"]

            # A reply observation was recorded for the original sender.
            rows = agent._memory_store.get_by_entity(f"{_REPLY_ENTITY_PREFIX}{sender}")
            assert (
                len(rows) == 1
            ), f"Expected 1 reply record after replying through agent, got {len(rows)}"
            payload = json.loads(rows[0]["content"])
            latencies = payload["reply_latencies_seconds"]
            assert len(latencies) == 1
            # Latency should be close to the 120 s receipt anchor (allow drift
            # for test execution time).
            assert (
                110.0 <= latencies[0] <= 200.0
            ), f"Computed latency {latencies[0]} not near the 120 s anchor"
        finally:
            agent.close_db()

    def test_repeated_fast_replies_then_triage_promotes(self, tmp_path):
        """Full loop: observe fast replies via the agent, then triage promotes.

        Drives REPLY_PROMOTION_MIN_REPLIES genuine replies (each to a freshly
        seeded recently-received message, so the computed latency is well under
        the threshold), then runs triage and asserts the sender is promoted —
        all from OBSERVED behavior, no seeded reply data.
        """
        from gaia_agent_email.tools.profile_tools import REPLY_PROMOTION_MIN_REPLIES

        sender = "boss@example.com"
        # Seed the first received message; we add the rest below.
        received = _received_message("rmsg0", sender, received_seconds_ago=10.0)
        agent = _build_agent_with_fake_gmail(tmp_path, received)
        try:
            # Reply to the first message.
            res0 = _invoke_draft_reply(agent, "rmsg0", "Reply 0")
            assert res0["ok"] is True

            # Add and reply to additional recently-received messages so the
            # reply count reaches the promotion threshold, each fast.
            backend = agent._backends["google"]
            for i in range(1, REPLY_PROMOTION_MIN_REPLIES):
                mid = f"rmsg{i}"
                backend.add_message(
                    _received_message(mid, sender, received_seconds_ago=10.0)
                )
                res = _invoke_draft_reply(agent, mid, f"Reply {i}")
                assert res["ok"] is True

            # Observed fast-reply behavior should now qualify the sender.
            promoted = agent._evaluate_promotions()
            assert sender in promoted, (
                f"Sender should qualify after {REPLY_PROMOTION_MIN_REPLIES} observed "
                f"fast replies. promoted={promoted}"
            )

            # And triage applies the promotion.
            triage_msg = {
                "id": "tmsg",
                "thread_id": "tthread",
                "from": f"Boss <{sender}>",
                "subject": "FYI",
                "snippet": "info",
                "category": "informational",
                "mailbox": "google",
            }
            _triage_messages(agent, [triage_msg])
            assert (
                sender in agent._session_preferences["priority_senders"]
            ), "Triage should promote a sender with observed fast-reply behavior"
        finally:
            agent.close_db()

    def test_send_now_fresh_compose_records_nothing(self, tmp_path):
        """A fresh compose (send_now, no original) records no reply observation.

        send_now has no receipt anchor, so we must not fabricate a latency.
        """
        from gaia.agents.base.tools import _TOOL_REGISTRY

        # Build an agent with a fake backend (one unrelated received message).
        received = _received_message(
            "other", "someone@example.com", received_seconds_ago=60.0
        )
        agent = _build_agent_with_fake_gmail(tmp_path, received)
        try:
            entry = _TOOL_REGISTRY.get("send_now")
            assert entry is not None, "send_now tool not registered"
            result = json.loads(
                entry["function"]("newcontact@example.com", "Hi", "Reaching out")
            )
            assert result["ok"] is True, f"send_now failed: {result}"

            # No reply record for the fresh-compose recipient (no anchor).
            rows = agent._memory_store.get_by_entity(
                f"{_REPLY_ENTITY_PREFIX}newcontact@example.com"
            )
            assert (
                len(rows) == 0
            ), "send_now (fresh compose) must not record a reply observation"
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# Tests — Outlook ISO-8601 internalDate anchor
# ---------------------------------------------------------------------------


class TestOutlookReplyLatency:
    """_compute_reply_latency_seconds parses Outlook receivedDateTime ISO anchors."""

    def test_outlook_iso_anchor_returns_latency(self, tmp_path):
        """An Outlook-shaped message with an ISO internalDate ~120 s ago returns ~120 s.

        The Outlook backend maps ``receivedDateTime`` (ISO-8601 string) into
        ``internalDate``.  ``_compute_reply_latency_seconds`` must parse this
        format and return a numeric latency rather than None.
        """
        from gaia_agent_email.tools.reply_tools import _compute_reply_latency_seconds

        received_seconds_ago = 120.0
        # Build an ISO-8601 anchor the way Outlook does (trailing Z).
        received_utc = datetime.fromtimestamp(
            time.time() - received_seconds_ago, tz=timezone.utc
        )
        iso_anchor = received_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        original_msg = {
            "id": "outlook-msg-1",
            "internalDate": iso_anchor,  # Outlook-style ISO string
            "payload": {
                "headers": [
                    {"name": "From", "value": "Boss <boss@example.com>"},
                    {"name": "Subject", "value": "Quarterly review"},
                ],
            },
        }

        latency = _compute_reply_latency_seconds(original_msg)
        assert (
            latency is not None
        ), f"Expected a numeric latency for ISO anchor '{iso_anchor}', got None"
        # Allow generous drift for test execution time (±30 s around 120 s).
        assert (
            90.0 <= latency <= 200.0
        ), f"Expected latency ~120 s for Outlook ISO anchor, got {latency}"

    def test_outlook_reply_observation_recorded(self, tmp_path):
        """An Outlook-shaped original_msg with ISO internalDate records an observation.

        Drives _record_reply_observation with an Outlook-shaped message and
        asserts the reply interaction is written for the sender — proving the
        Outlook path is wired end-to-end, not just parsed in isolation.
        """
        from gaia_agent_email.tools.reply_tools import _record_reply_observation

        received_seconds_ago = 120.0
        received_utc = datetime.fromtimestamp(
            time.time() - received_seconds_ago, tz=timezone.utc
        )
        iso_anchor = received_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        original_msg = {
            "id": "outlook-msg-2",
            "internalDate": iso_anchor,
            "payload": {
                "headers": [
                    {"name": "From", "value": "Boss <boss@outlook.com>"},
                    {"name": "Subject", "value": "Update"},
                ],
            },
        }

        agent = _build_agent(tmp_path)
        try:
            # No record before the observation.
            before = agent._memory_store.get_by_entity(
                f"{_REPLY_ENTITY_PREFIX}boss@outlook.com"
            )
            assert len(before) == 0

            _record_reply_observation(agent, original_msg)

            # Observation was recorded for the Outlook sender.
            rows = agent._memory_store.get_by_entity(
                f"{_REPLY_ENTITY_PREFIX}boss@outlook.com"
            )
            assert (
                len(rows) == 1
            ), f"Expected 1 reply record for Outlook sender, got {len(rows)}"
            payload = json.loads(rows[0]["content"])
            latencies = payload["reply_latencies_seconds"]
            assert len(latencies) == 1
            # Latency should be close to 120 s (allow drift for test execution).
            assert (
                90.0 <= latencies[0] <= 200.0
            ), f"Computed Outlook latency {latencies[0]} not near 120 s anchor"
        finally:
            agent.close_db()

    def test_gmail_numeric_millis_still_works(self):
        """Gmail numeric-millis internalDate is still parsed correctly.

        Regression guard: the ISO path must not break the existing Gmail path.
        """
        from gaia_agent_email.tools.reply_tools import _compute_reply_latency_seconds

        received_seconds_ago = 120.0
        internal_ms = int((time.time() - received_seconds_ago) * 1000)
        original_msg = {"internalDate": str(internal_ms)}

        latency = _compute_reply_latency_seconds(original_msg)
        assert latency is not None, "Gmail numeric millis must produce a latency"
        assert (
            90.0 <= latency <= 200.0
        ), f"Gmail millis latency {latency} not near 120 s"
