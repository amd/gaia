# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Earn-trust policy engine tests (#1483 / #1287).

Covers the trust ledger math and the decision layer, with special emphasis on
the one invariant that must never break: the destructive/irreversible
confirm-floor ALWAYS resolves to ``confirm`` — at every autonomy level, even
for a fully-trusted sender. An autonomous email agent that can be talked into
an unattended send is a non-starter; these tests are that guardrail.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gaia_agent_email")

from gaia_agent_email import trust  # noqa: E402
from gaia_agent_email.trust import (  # noqa: E402
    LEVEL_EARN_TRUST,
    LEVEL_FULL,
    LEVEL_OFF,
    LEVEL_SUGGEST,
    TrustLedger,
    TrustPolicy,
    category_scope,
    sender_scope,
)

from gaia.database.mixin import DatabaseMixin  # noqa: E402


class _DB(DatabaseMixin):
    pass


@pytest.fixture
def db():
    d = _DB()
    d.init_db(":memory:")
    trust.init_trust_schema(d)
    return d


# The email agent's real floor. Kept as a literal here so a change to the
# agent's CONFIRMATION_REQUIRED_TOOLS that accidentally *shrinks* the floor is
# caught by the parity test below rather than silently loosening these cases.
FLOOR = frozenset(
    {
        "send_draft",
        "send_now",
        "schedule_send",
        "forward_message",
        "permanent_delete",
        "accept_invite",
        "decline_invite",
        "create_event_from_email",
        "quarantine_phishing_message",
    }
)


def _policy(level, db_min=5, threshold=0.85):
    ledger = TrustLedger(min_samples=db_min, threshold=threshold)
    return TrustPolicy(level=level, ledger=ledger, confirm_floor=FLOOR)


# ---------------------------------------------------------------------------
# TrustLedger
# ---------------------------------------------------------------------------


def test_record_outcome_creates_then_increments(db):
    scope = sender_scope("news@x.com")
    TrustLedger.record_outcome(db, action_type="archive", scope=scope, positive=True)
    stats = TrustLedger.get_stats(db, action_type="archive", scope=scope)
    assert stats == {"positive": 1, "negative": 0, "total": 1, "score": 1.0}

    TrustLedger.record_outcome(db, action_type="archive", scope=scope, positive=True)
    TrustLedger.record_outcome(db, action_type="archive", scope=scope, positive=False)
    stats = TrustLedger.get_stats(db, action_type="archive", scope=scope)
    assert stats["positive"] == 2
    assert stats["negative"] == 1
    assert stats["total"] == 3
    assert stats["score"] == pytest.approx(2 / 3)


def test_get_stats_empty_scope_is_zero(db):
    stats = TrustLedger.get_stats(db, action_type="archive", scope="sender:none@x")
    assert stats == {"positive": 0, "negative": 0, "total": 0, "score": 0.0}


def test_is_trusted_requires_both_samples_and_accuracy(db):
    ledger = TrustLedger(min_samples=5, threshold=0.85)
    scope = category_scope("PROMOTIONAL")

    # 4/4 correct: perfect accuracy but below the sample floor → not trusted.
    for _ in range(4):
        ledger.record_outcome(db, action_type="archive", scope=scope, positive=True)
    assert not ledger.is_trusted(db, action_type="archive", scope=scope)

    # 5th correct crosses the sample floor at 100% → trusted.
    ledger.record_outcome(db, action_type="archive", scope=scope, positive=True)
    assert ledger.is_trusted(db, action_type="archive", scope=scope)


def test_is_trusted_denied_below_threshold(db):
    ledger = TrustLedger(min_samples=5, threshold=0.85)
    scope = category_scope("FYI")
    # 8 correct, 2 wrong → 0.8 accuracy, enough samples but below 0.85.
    for _ in range(8):
        ledger.record_outcome(db, action_type="archive", scope=scope, positive=True)
    for _ in range(2):
        ledger.record_outcome(db, action_type="archive", scope=scope, positive=False)
    assert not ledger.is_trusted(db, action_type="archive", scope=scope)


def test_ledger_rejects_bad_thresholds():
    with pytest.raises(ValueError):
        TrustLedger(min_samples=0)
    with pytest.raises(ValueError):
        TrustLedger(threshold=0.0)
    with pytest.raises(ValueError):
        TrustLedger(threshold=1.5)


def test_list_ledger_returns_rows(db):
    TrustLedger.record_outcome(
        db, action_type="archive", scope=sender_scope("a@x"), positive=True
    )
    TrustLedger.record_outcome(
        db, action_type="add_label", scope=category_scope("FYI"), positive=False
    )
    rows = TrustLedger.list_ledger(db)
    assert len(rows) == 2
    assert {r["action_type"] for r in rows} == {"archive", "add_label"}


# ---------------------------------------------------------------------------
# The inviolable floor — the invariant that matters most
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "level", [LEVEL_OFF, LEVEL_SUGGEST, LEVEL_EARN_TRUST, LEVEL_FULL]
)
@pytest.mark.parametrize("tool", sorted(FLOOR))
def test_floor_always_confirms_at_every_level(db, level, tool):
    """No level ever lowers the destructive floor to auto/draft/suggest."""
    policy = _policy(level)
    decision = policy.decide(tool=tool, action_type="send", db=db)
    assert decision.action == "confirm"
    assert decision.confidence == 1.0


def test_floor_confirms_even_for_fully_trusted_sender(db):
    """A sender proven 100/100 on archives still cannot trigger an auto-send."""
    ledger = TrustLedger(min_samples=5, threshold=0.85)
    scope = sender_scope("boss@company.com")
    for _ in range(100):
        ledger.record_outcome(db, action_type="archive", scope=scope, positive=True)
    policy = TrustPolicy(level=LEVEL_FULL, ledger=ledger, confirm_floor=FLOOR)
    decision = policy.decide(
        tool="send_now",
        action_type="send",
        sender="boss@company.com",
        db=db,
    )
    assert decision.action == "confirm"


# ---------------------------------------------------------------------------
# Level behavior
# ---------------------------------------------------------------------------


def test_off_level_disables_loop(db):
    policy = _policy(LEVEL_OFF)
    assert policy.enabled is False
    d = policy.decide(tool="archive_message", action_type="archive", db=db)
    assert d.action == "suggest"


def test_draft_actions_always_draft_never_send(db):
    for level in (LEVEL_SUGGEST, LEVEL_EARN_TRUST, LEVEL_FULL):
        policy = _policy(level)
        d = policy.decide(tool="draft_reply", action_type="draft_reply", db=db)
        assert d.action == "draft", level


def test_non_reversible_action_suggests(db):
    policy = _policy(LEVEL_FULL)
    d = policy.decide(tool="trash_message", action_type="trash", db=db)
    assert d.action == "suggest"


def test_suggest_level_never_auto(db):
    policy = _policy(LEVEL_SUGGEST)
    d = policy.decide(tool="archive_message", action_type="archive", db=db)
    assert d.action == "suggest"


def test_full_level_auto_executes_reversible(db):
    policy = _policy(LEVEL_FULL)
    d = policy.decide(tool="archive_message", action_type="archive", db=db)
    assert d.action == "auto"
    assert d.confidence == 1.0


# ---------------------------------------------------------------------------
# earn_trust — the star mode
# ---------------------------------------------------------------------------


def test_earn_trust_suggests_until_proven_then_auto(db):
    policy = _policy(LEVEL_EARN_TRUST, db_min=5, threshold=0.85)
    scope = sender_scope("news@x.com")

    # Cold: no evidence → suggest.
    d = policy.decide(
        tool="archive_message", action_type="archive", sender="news@x.com", db=db
    )
    assert d.action == "suggest"

    # Feed 5 positive outcomes → crosses the trust bar → auto.
    for _ in range(5):
        TrustLedger.record_outcome(
            db, action_type="archive", scope=scope, positive=True
        )
    d = policy.decide(
        tool="archive_message", action_type="archive", sender="news@x.com", db=db
    )
    assert d.action == "auto"
    assert d.confidence == pytest.approx(1.0)
    assert "5/5" in d.reason


def test_earn_trust_category_scope_grants_auto(db):
    policy = _policy(LEVEL_EARN_TRUST, db_min=3, threshold=0.85)
    scope = category_scope("PROMOTIONAL")
    for _ in range(3):
        TrustLedger.record_outcome(
            db, action_type="archive", scope=scope, positive=True
        )
    d = policy.decide(
        tool="archive_message",
        action_type="archive",
        category="PROMOTIONAL",
        sender="unseen@x.com",
        db=db,
    )
    assert d.action == "auto"


def test_earn_trust_explicit_low_priority_sender_auto(db):
    policy = _policy(LEVEL_EARN_TRUST)
    prefs = {"low_priority_senders": {"newsletter@stripe.com"}, "category_defaults": {}}
    d = policy.decide(
        tool="archive_message",
        action_type="archive",
        sender="newsletter@stripe.com",
        db=db,
        preferences=prefs,
    )
    assert d.action == "auto"
    assert "preference" in d.reason


def test_earn_trust_explicit_category_default_auto(db):
    policy = _policy(LEVEL_EARN_TRUST)
    prefs = {"low_priority_senders": set(), "category_defaults": {"FYI": "archive"}}
    d = policy.decide(
        tool="archive_message",
        action_type="archive",
        category="FYI",
        sender="anyone@x.com",
        db=db,
        preferences=prefs,
    )
    assert d.action == "auto"


def test_earn_trust_negative_evidence_keeps_suggesting(db):
    policy = _policy(LEVEL_EARN_TRUST, db_min=5, threshold=0.85)
    scope = sender_scope("mixed@x.com")
    for _ in range(5):
        TrustLedger.record_outcome(
            db, action_type="archive", scope=scope, positive=True
        )
    for _ in range(3):
        TrustLedger.record_outcome(
            db, action_type="archive", scope=scope, positive=False
        )
    # 5/8 = 0.625 accuracy < 0.85 → still suggests, carrying the current score.
    d = policy.decide(
        tool="archive_message", action_type="archive", sender="mixed@x.com", db=db
    )
    assert d.action == "suggest"
    assert d.confidence == pytest.approx(5 / 8)


def test_policy_rejects_unknown_level():
    with pytest.raises(ValueError):
        TrustPolicy(level="turbo", ledger=TrustLedger(), confirm_floor=FLOOR)


# ---------------------------------------------------------------------------
# Parity: the test FLOOR must match the agent's real confirm-floor
# ---------------------------------------------------------------------------


def test_floor_matches_agent_confirmation_required_tools():
    """If the agent's floor changes, this test forces this file to change too —
    so the floor can never silently shrink out from under the policy tests."""
    from gaia_agent_email.agent import EmailTriageAgent

    assert set(FLOOR) == set(EmailTriageAgent.CONFIRMATION_REQUIRED_TOOLS)
