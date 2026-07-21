# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Autonomous observe->decide->act cycle tests (#1115 / #557).

Drives ``EmailTriageAgent._run_email_autonomy_cycle`` against a real
``FakeGmailBackend`` with the LLM classifier disabled (``agent.chat = None``)
so the heuristic path runs hermetically — no Lemonade.

The earn-trust progression is the headline: a promotional sender is only
*proposed* for archive until the trust ledger proves the agent right enough
times, after which the same sender is archived silently. And at no autonomy
level does the cycle ever perform a send/delete — there is no such candidate,
and the floor would block it anyway (locked by test_trust.py).
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.trust import (  # noqa: E402
    LEVEL_EARN_TRUST,
    LEVEL_FULL,
    LEVEL_OFF,
    LEVEL_SUGGEST,
    TrustLedger,
    sender_scope,
)

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402


def _fake_embed(text, *args, **kwargs):
    return np.zeros(768, dtype=np.float32)


class _MinimalCalendarBackend:
    def list_events(self, *a, **k):
        return {"events": []}


def _promo_message(message_id: str, sender: str) -> dict:
    """A message the heuristic classifies confidently as PROMOTIONAL.

    The ``CATEGORY_PROMOTIONS`` label is the mechanical promotional signal, so
    no LLM classifier is needed to reach a confident category.
    """
    internal_ms = int(time.time() * 1000)
    return {
        "id": message_id,
        "threadId": f"thread_{message_id}",
        "labelIds": ["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"],
        "internalDate": str(internal_ms),
        "snippet": "50% off everything this weekend",
        "payload": {
            "headers": [
                {"name": "From", "value": f"Deals <{sender}>"},
                {"name": "Subject", "value": "Weekend sale inside"},
                {"name": "Message-ID", "value": f"<{message_id}@x.com>"},
                {"name": "Date", "value": "Mon, 12 Jun 2026 10:00:00 +0000"},
            ],
        },
    }


def _urgent_message(message_id: str, sender: str) -> dict:
    internal_ms = int(time.time() * 1000)
    return {
        "id": message_id,
        "threadId": f"thread_{message_id}",
        "labelIds": ["INBOX", "UNREAD", "IMPORTANT"],
        "internalDate": str(internal_ms),
        "snippet": "URGENT: production is down, need you now",
        "payload": {
            "headers": [
                {"name": "From", "value": f"Boss <{sender}>"},
                {"name": "Subject", "value": "URGENT: prod down, action required ASAP"},
                {"name": "Message-ID", "value": f"<{message_id}@x.com>"},
                {"name": "Date", "value": "Mon, 12 Jun 2026 10:00:00 +0000"},
            ],
        },
    }


def _build_agent(tmp_path: Path, messages, *, level: str, **cfg_kw) -> EmailTriageAgent:
    backend = FakeGmailBackend(user_email="me@example.com")
    for msg in messages:
        backend.add_message(msg)

    cfg = EmailAgentConfig(
        gmail_backend=backend,
        calendar_backend=_MinimalCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        memory_db_path=str(tmp_path / "memory.db"),
        silent_mode=True,
        debug=False,
        autonomy_level=level,
        **cfg_kw,
    )

    with (
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
        patch(
            "gaia.agents.base.memory.MemoryMixin._get_embedder",
            return_value=MagicMock(),
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin._embed_text", side_effect=_fake_embed
        ),
        patch(
            "gaia.agents.base.memory.MemoryMixin._backfill_embeddings", return_value=0
        ),
        patch("gaia.agents.base.memory.MemoryMixin._rebuild_faiss_index"),
        patch("gaia.agents.base.memory.MemoryMixin.init_system_context"),
    ):
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    # Heuristic-only: disable the LLM classifier path entirely.
    agent.chat = None
    return agent


# ---------------------------------------------------------------------------
# Level gating
# ---------------------------------------------------------------------------


def test_off_level_does_nothing(tmp_path):
    agent = _build_agent(
        tmp_path, [_promo_message("m1", "deals@shop.com")], level=LEVEL_OFF
    )
    report = agent._run_email_autonomy_cycle()
    assert report["executed"] == []
    assert report["proposals"] == []


def test_suggest_level_proposes_never_executes(tmp_path):
    agent = _build_agent(
        tmp_path, [_promo_message("m1", "deals@shop.com")], level=LEVEL_SUGGEST
    )
    report = agent._run_email_autonomy_cycle()
    assert report["executed"] == []
    assert len(report["proposals"]) == 1
    assert report["proposals"][0].action_class == "other"


# ---------------------------------------------------------------------------
# earn-trust progression — the headline behavior
# ---------------------------------------------------------------------------


def test_earn_trust_proposes_when_cold(tmp_path):
    agent = _build_agent(
        tmp_path,
        [_promo_message("m1", "deals@shop.com")],
        level=LEVEL_EARN_TRUST,
        autonomy_trust_min_samples=3,
    )
    report = agent._run_email_autonomy_cycle()
    assert report["executed"] == []
    assert len(report["proposals"]) == 1


def test_earn_trust_auto_archives_once_proven(tmp_path):
    sender = "deals@shop.com"
    agent = _build_agent(
        tmp_path,
        [_promo_message("m1", sender)],
        level=LEVEL_EARN_TRUST,
        autonomy_trust_min_samples=3,
    )
    # Seed the ledger so the sender scope is already trusted for archiving.
    scope = sender_scope(sender)
    for _ in range(3):
        TrustLedger.record_outcome(
            agent, action_type="archive", scope=scope, positive=True
        )

    report = agent._run_email_autonomy_cycle()
    assert len(report["executed"]) == 1
    entry = report["executed"][0]
    assert entry["action"] == "archive"
    assert entry["message_id"] == "m1"
    assert "action_id" in entry  # undo handle recorded
    assert report["proposals"] == []
    # The message actually left the inbox.
    assert "INBOX" not in agent._gmail.get_message("m1").get("labelIds", [])


def test_full_level_archives_immediately(tmp_path):
    agent = _build_agent(
        tmp_path, [_promo_message("m1", "deals@shop.com")], level=LEVEL_FULL
    )
    report = agent._run_email_autonomy_cycle()
    assert len(report["executed"]) == 1
    assert report["proposals"] == []


# ---------------------------------------------------------------------------
# Safety — important mail is never auto-touched
# ---------------------------------------------------------------------------


def test_urgent_message_never_auto_actioned_even_at_full(tmp_path):
    agent = _build_agent(
        tmp_path, [_urgent_message("u1", "boss@company.com")], level=LEVEL_FULL
    )
    report = agent._run_email_autonomy_cycle()
    # Urgent mail is not an auto candidate: nothing executed, nothing proposed.
    assert report["executed"] == []
    assert report["proposals"] == []
    assert report["skipped"] == 1
    # Still in the inbox, untouched.
    assert "INBOX" in agent._gmail.get_message("u1").get("labelIds", [])


def test_mixed_inbox_splits_correctly(tmp_path):
    agent = _build_agent(
        tmp_path,
        [
            _promo_message("p1", "deals@shop.com"),
            _urgent_message("u1", "boss@company.com"),
        ],
        level=LEVEL_FULL,
    )
    report = agent._run_email_autonomy_cycle()
    executed_ids = {e["message_id"] for e in report["executed"]}
    assert executed_ids == {"p1"}
    assert report["skipped"] == 1


# ---------------------------------------------------------------------------
# Hook + driver contract
# ---------------------------------------------------------------------------


def test_on_heartbeat_returns_proposal_objects(tmp_path):
    agent = _build_agent(
        tmp_path, [_promo_message("m1", "deals@shop.com")], level=LEVEL_SUGGEST
    )
    proposals = agent.on_heartbeat()
    assert len(proposals) == 1
    assert hasattr(proposals[0], "to_dict")


def test_run_autonomy_cycle_persists_and_serializes(tmp_path):
    agent = _build_agent(
        tmp_path, [_promo_message("m1", "deals@shop.com")], level=LEVEL_SUGGEST
    )
    with patch.object(agent, "propose") as mock_propose:
        report = agent.run_autonomy_cycle()
    mock_propose.assert_called_once()
    # Proposals are returned in serializable dict form, not raw objects.
    assert isinstance(report["proposals"][0], dict)
    assert "action" in report["proposals"][0]


# ---------------------------------------------------------------------------
# The learning loop — trust grows from feedback, shrinks from corrections
# ---------------------------------------------------------------------------


def test_record_outcome_writes_both_scopes(tmp_path):
    agent = _build_agent(tmp_path, [], level=LEVEL_EARN_TRUST)
    agent.record_autonomy_outcome(
        action_type="archive",
        positive=True,
        sender="news@x.com",
        category="PROMOTIONAL",
    )
    from gaia_agent_email.trust import TrustLedger, category_scope

    sender_stats = TrustLedger.get_stats(
        agent, action_type="archive", scope=sender_scope("news@x.com")
    )
    cat_stats = TrustLedger.get_stats(
        agent, action_type="archive", scope=category_scope("PROMOTIONAL")
    )
    assert sender_stats["positive"] == 1
    assert cat_stats["positive"] == 1


def test_closed_loop_feedback_earns_autonomy(tmp_path):
    """Cold sender is proposed; after enough positive feedback via the public
    funnel, the very next cycle archives it silently. The full loop, no seeded
    ledger rows."""
    sender = "deals@shop.com"
    agent = _build_agent(
        tmp_path,
        [_promo_message("m1", sender)],
        level=LEVEL_EARN_TRUST,
        autonomy_trust_min_samples=3,
    )

    # Cold: proposed, not executed.
    first = agent._run_email_autonomy_cycle()
    assert first["executed"] == []
    assert len(first["proposals"]) == 1

    # The user accepts three suggestions for this sender (the real earn path).
    for _ in range(3):
        agent.record_autonomy_outcome(
            action_type="archive", positive=True, sender=sender, category="PROMOTIONAL"
        )

    # Re-seed the same message (it was only proposed, never archived) and rerun.
    agent._gmail.add_message(_promo_message("m2", sender))
    second = agent._run_email_autonomy_cycle()
    executed_ids = {e["message_id"] for e in second["executed"]}
    assert "m2" in executed_ids


def test_correction_capture_pulls_trust_back(tmp_path):
    """An auto-archive the user undoes records a negative and re-closes the gate."""
    sender = "deals@shop.com"
    agent = _build_agent(
        tmp_path,
        [_promo_message("m1", sender)],
        level=LEVEL_FULL,  # full auto-executes immediately, so we get an action_id
        autonomy_trust_min_samples=3,
    )
    report = agent._run_email_autonomy_cycle()
    action_id = report["executed"][0]["action_id"]

    # User undoes it → correction captured as a negative for the scope.
    assert agent.note_action_undone(action_id) is True

    from gaia_agent_email.trust import TrustLedger

    stats = TrustLedger.get_stats(
        agent, action_type="archive", scope=sender_scope(sender)
    )
    assert stats["negative"] == 1

    # Idempotent: the same undo can't be counted twice.
    assert agent.note_action_undone(action_id) is False


def test_note_action_undone_ignores_non_autonomy_ids(tmp_path):
    agent = _build_agent(tmp_path, [], level=LEVEL_EARN_TRUST)
    assert agent.note_action_undone("not-an-autonomy-action") is False


def test_undo_tool_captures_correction_end_to_end(tmp_path):
    """Undoing an auto-archive through the real undo_archive_batch tool restores
    the message AND records the correction — the production learning path."""
    import json

    from gaia.agents.base.tools import _TOOL_REGISTRY

    sender = "deals@shop.com"
    agent = _build_agent(
        tmp_path, [_promo_message("m1", sender)], level=LEVEL_FULL
    )
    report = agent._run_email_autonomy_cycle()
    action_id = report["executed"][0]["action_id"]
    row = agent.query(
        "SELECT batch_id FROM email_actions WHERE action_id = :a",
        {"a": action_id},
        one=True,
    )
    batch_id = row["batch_id"]
    assert batch_id, "autonomy archive must carry a batch_id so it is undoable"

    entry = _TOOL_REGISTRY.get("undo_archive_batch")
    assert entry is not None
    json.loads(entry["function"](batch_id))  # invoke as the agent would

    # Message restored to the inbox.
    assert "INBOX" in agent._gmail.get_message("m1").get("labelIds", [])
    # Correction captured as a negative for the scope.
    from gaia_agent_email.trust import TrustLedger

    stats = TrustLedger.get_stats(
        agent, action_type="archive", scope=sender_scope(sender)
    )
    assert stats["negative"] == 1


# ---------------------------------------------------------------------------
# Inspectable status — autonomy is never a black box
# ---------------------------------------------------------------------------


def test_autonomy_status_reports_level_and_ledger(tmp_path):
    agent = _build_agent(
        tmp_path, [], level=LEVEL_EARN_TRUST, autonomy_trust_min_samples=3
    )
    # Below the bar, then over it.
    for _ in range(2):
        agent.record_autonomy_outcome(
            action_type="archive", positive=True, sender="a@x.com"
        )
    for _ in range(3):
        agent.record_autonomy_outcome(
            action_type="archive", positive=True, category="PROMOTIONAL"
        )

    status = agent.autonomy_status()
    assert status["level"] == LEVEL_EARN_TRUST
    assert status["enabled"] is True
    by_scope = {s["scope"]: s for s in status["scopes"]}
    assert by_scope[sender_scope("a@x.com")]["trusted"] is False  # only 2 samples
    from gaia_agent_email.trust import category_scope

    assert by_scope[category_scope("PROMOTIONAL")]["trusted"] is True  # 3 samples
    assert status["trusted_scope_count"] == 1
