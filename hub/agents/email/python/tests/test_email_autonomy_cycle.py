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


def _security_important_message(
    message_id: str, sender: str = "no-reply@accounts.google.com"
) -> dict:
    """A provider-IMPORTANT message from an account-security sender that triage
    (mis)classifies PROMOTIONAL — the #2426 auto-archive trap.

    The ``IMPORTANT`` + ``CATEGORY_PROMOTIONS`` label pair makes the heuristic
    confidently PROMOTIONAL (an archive candidate) with no LLM, while the
    ``IMPORTANT`` label and the account-security sender are exactly what the
    guard must refuse to auto-archive. Subject/snippet are deliberately benign
    (no urgent/commitment/phishing keywords) so the confident-PROMOTIONAL
    short-circuit is not vetoed.
    """
    internal_ms = int(time.time() * 1000)
    return {
        "id": message_id,
        "threadId": f"thread_{message_id}",
        "labelIds": ["INBOX", "UNREAD", "IMPORTANT", "CATEGORY_PROMOTIONS"],
        "internalDate": str(internal_ms),
        "snippet": "Here is a summary of recent activity on your account.",
        "payload": {
            "headers": [
                {"name": "From", "value": f"Google <{sender}>"},
                {"name": "Subject", "value": "Your Google Account: monthly summary"},
                {"name": "Message-ID", "value": f"<{message_id}@accounts.google.com>"},
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


def test_full_cycle_never_auto_archives_important_security_message(tmp_path):
    """#2426 (AC-1/2/3): at ``full``, a provider-IMPORTANT message from an
    account-security sender that triage mis-labels PROMOTIONAL must be PROPOSED,
    not auto-archived — while ordinary promo clutter still auto-archives."""
    agent = _build_agent(
        tmp_path,
        [
            _promo_message("p1", "deals@shop.com"),
            _security_important_message("s1"),
        ],
        level=LEVEL_FULL,
    )
    report = agent._run_email_autonomy_cycle()

    executed_ids = {e["message_id"] for e in report["executed"]}
    assert executed_ids == {"p1"}, "ordinary promo should still auto-archive"
    assert "s1" not in executed_ids, "IMPORTANT security mail must NOT auto-archive"

    proposed = " ".join(p.action for p in report["proposals"])
    assert "s1" in proposed, "the IMPORTANT security message must be proposed"

    # AC-3: it survived in the inbox.
    assert "INBOX" in agent._gmail.get_message("s1").get("labelIds", [])


def test_full_cycle_security_sender_without_important_still_proposed(tmp_path):
    """#2426 (AC-2 standalone): an account-security sender is never auto-archived
    unattended even without the provider IMPORTANT label."""
    msg = _security_important_message("s2")
    # Drop the IMPORTANT label — the security sender alone must still gate it.
    msg["labelIds"] = ["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"]
    agent = _build_agent(tmp_path, [msg], level=LEVEL_FULL)
    report = agent._run_email_autonomy_cycle()

    assert report["executed"] == []
    assert len(report["proposals"]) == 1
    assert "INBOX" in agent._gmail.get_message("s2").get("labelIds", [])


def test_triage_row_carries_label_ids(tmp_path):
    """The autonomy guard reads the provider IMPORTANT flag off ``row['label_ids']``.
    Lock the plumbing so a future triage edit can't silently drop it and disable
    the guard."""
    agent = _build_agent(
        tmp_path,
        [_promo_message("p1", "deals@shop.com"), _security_important_message("s1")],
        level=LEVEL_OFF,
    )
    triage = agent._triage_all_backends(max_messages=10)
    rows = triage["results"]
    assert rows, "expected triage rows"
    for row in rows:
        assert isinstance(row.get("label_ids"), list), row
    by_id = {r["id"]: r for r in rows}
    assert "IMPORTANT" in by_id["s1"]["label_ids"]


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


def test_cold_message_proposed_once_not_re_proposed_each_cycle(tmp_path):
    """Re-running the cycle on the same still-in-inbox message must NOT pile up a
    duplicate proposal every fire — the headless-timer spam guard."""
    agent = _build_agent(
        tmp_path,
        [_promo_message("m1", "deals@shop.com")],
        level=LEVEL_EARN_TRUST,
        autonomy_trust_min_samples=3,
    )
    first = agent._run_email_autonomy_cycle()
    assert len(first["proposals"]) == 1
    assert first["already_proposed"] == 0

    # Same message still in the inbox → second cycle proposes nothing new.
    second = agent._run_email_autonomy_cycle()
    assert second["proposals"] == []
    assert second["already_proposed"] == 1


def test_run_autonomy_cycle_does_not_duplicate_goals(tmp_path):
    """End-to-end: two driver runs create exactly one GoalStore proposal."""
    agent = _build_agent(
        tmp_path,
        [_promo_message("m1", "deals@shop.com")],
        level=LEVEL_EARN_TRUST,
        autonomy_trust_min_samples=3,
    )
    with patch.object(agent, "propose") as mock_propose:
        agent.run_autonomy_cycle()
        agent.run_autonomy_cycle()
    assert mock_propose.call_count == 1


def test_undo_tool_captures_correction_end_to_end(tmp_path):
    """Undoing an auto-archive through the real undo_archive_batch tool restores
    the message AND records the correction — the production learning path."""
    import json

    from gaia.agents.base.tools import _TOOL_REGISTRY

    sender = "deals@shop.com"
    agent = _build_agent(tmp_path, [_promo_message("m1", sender)], level=LEVEL_FULL)
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


# ---------------------------------------------------------------------------
# Proactive simulation harness (#1508) — multi-cycle convergence + invariants
# ---------------------------------------------------------------------------


class TestAutonomySimulation:
    """Simulate an inbox over many heartbeats and assert the earn-trust engine
    converges the way the design promises, and that the safety invariants hold
    across the whole run — not just a single decision."""

    def _promos_from(self, sender, n, start=0):
        return [_promo_message(f"p{start + i}", sender) for i in range(n)]

    def test_converges_from_proposing_to_acting_with_positive_feedback(self, tmp_path):
        sender = "deals@shop.com"
        agent = _build_agent(
            tmp_path,
            [],
            level=LEVEL_EARN_TRUST,
            autonomy_trust_min_samples=3,
        )

        # Cycle 1-3: cold → proposes; the "user" accepts each suggestion.
        proposed_cycles = 0
        for c in range(3):
            agent._gmail.add_message(_promo_message(f"a{c}", sender))
            report = agent._run_email_autonomy_cycle()
            if report["proposals"]:
                proposed_cycles += 1
                # User accepts the suggestion → positive feedback.
                agent.record_autonomy_outcome(
                    action_type="archive",
                    positive=True,
                    sender=sender,
                    category="PROMOTIONAL",
                )
        assert proposed_cycles >= 1  # it asked before it earned trust

        # Now trusted: a fresh message from the same sender is auto-archived.
        agent._gmail.add_message(_promo_message("final", sender))
        final = agent._run_email_autonomy_cycle()
        assert any(e["message_id"] == "final" for e in final["executed"])

    def test_cycle_only_auto_executes_reversible_actions(self, tmp_path):
        """Whatever the inbox, an autonomy cycle only ever executes reversible
        actions — never a floor tool. (The floor's confirm-gate itself is locked
        in test_trust.py; this guards the candidate map that feeds the cycle.)"""
        from gaia_agent_email.agent import EmailTriageAgent
        from gaia_agent_email.trust import REVERSIBLE_AUTO_ACTIONS

        floor = EmailTriageAgent.CONFIRMATION_REQUIRED_TOOLS
        sender = "deals@shop.com"
        msgs = self._promos_from(sender, 5) + [
            _urgent_message("u1", "boss@x.com"),
            _urgent_message("u2", "vip@x.com"),
        ]
        agent = _build_agent(tmp_path, msgs, level=LEVEL_FULL)
        report = agent._run_email_autonomy_cycle()
        assert report["executed"], "expected the promos to be archived"
        for entry in report["executed"]:
            assert entry["action"] in REVERSIBLE_AUTO_ACTIONS
            assert entry["action"] not in floor

    def test_correction_demotes_a_previously_trusted_scope(self, tmp_path):
        sender = "deals@shop.com"
        agent = _build_agent(
            tmp_path,
            [],
            level=LEVEL_EARN_TRUST,
            autonomy_trust_min_samples=3,
            autonomy_trust_threshold=0.85,
        )
        # Earn trust (3/3), then two corrections drag accuracy below the bar.
        for _ in range(3):
            agent.record_autonomy_outcome(
                action_type="archive", positive=True, sender=sender
            )
        status_before = {s["scope"]: s for s in agent.autonomy_status()["scopes"]}
        assert status_before[sender_scope(sender)]["trusted"] is True

        for _ in range(2):
            agent.record_autonomy_outcome(
                action_type="archive", positive=False, sender=sender
            )
        # 3/5 = 0.6 < 0.85 → no longer trusted; the agent goes back to asking.
        agent._gmail.add_message(_promo_message("m1", sender))
        report = agent._run_email_autonomy_cycle()
        assert report["executed"] == []
        assert len(report["proposals"]) == 1
