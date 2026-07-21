# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Earn-trust policy engine for autonomous email handling (#1483 / #1287).

Two cooperating pieces:

- :class:`TrustLedger` — pure functions over the agent's SQLite handle
  (mirrors ``action_store``/``schedule_store``): a per-``(action_type, scope)``
  tally of positive vs. negative outcomes. "scope" is a category
  (``category:PROMOTIONAL``) or a sender (``sender:news@x.com``). Trust is
  *earned* here — a scope becomes trusted only after enough correct decisions.

- :class:`TrustPolicy` — the decision layer. Given a candidate action it
  returns a :class:`TrustDecision` of ``auto`` | ``draft`` | ``suggest`` |
  ``confirm``. It reads the ledger, the user's explicit preferences, and the
  configured autonomy level.

Inviolable floor (the whole point of "check in on destructive things"):
tools in the agent's ``CONFIRMATION_REQUIRED_TOOLS`` — send, forward, permanent
delete, calendar RSVP, phishing quarantine — ALWAYS resolve to ``confirm``, at
every autonomy level, regardless of how much trust a scope has earned. The
policy layer can widen what runs silently; it can NEVER lower the floor. A
misconfigured level or a fully-trusted sender still cannot cause an unattended
send. ``tests/test_trust.py`` locks this in.

Reversible-first: only reversible actions (archive, label, mark-read, star) are
ever auto-executed, and every one is recorded in ``action_store`` with undo.
Reply composition is ``draft`` — the agent writes the reply but never sends it
unattended; sending stays on the floor.

Storage choice: the accuracy ledger is OPERATIONAL state (like ``action_store``)
so it lives in the agent's ``state.db`` via ``DatabaseMixin``, NOT in
MemoryStore — MemoryStore knowledge is subject to LLM consolidation, which must
never rewrite an audit tally. Spec §6.6 permission *grants* still live in
MemoryStore; this ledger is the evidence those grants are earned from.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional

# ---------------------------------------------------------------------------
# Autonomy levels — the earn-trust gradient
# ---------------------------------------------------------------------------

#: No autonomous activity. The heartbeat loop does not run. Default.
LEVEL_OFF = "off"
#: Propose everything, execute nothing autonomously (read-only + suggestions).
LEVEL_SUGGEST = "suggest"
#: The star mode: auto-execute reversible actions in trusted/approved scopes,
#: draft replies, suggest the rest. This is what "full autonomy mode" maps to.
LEVEL_EARN_TRUST = "earn_trust"
#: Auto-execute all reversible actions immediately; drafts still never send.
LEVEL_FULL = "full"

AUTONOMY_LEVELS = (LEVEL_OFF, LEVEL_SUGGEST, LEVEL_EARN_TRUST, LEVEL_FULL)

AutonomyLevel = Literal["off", "suggest", "earn_trust", "full"]

# ---------------------------------------------------------------------------
# Action taxonomy
# ---------------------------------------------------------------------------

#: Reversible mutations that MAY be auto-executed once a scope is trusted.
#: Each is recorded in ``action_store`` with an undo path. ``trash`` is
#: deliberately excluded — a soft-delete is reversible only inside the undo
#: window, so it stays a suggestion until the user opts it up explicitly.
# Names MUST match the ``action_type`` strings ``action_store`` records (so an
# undo of an autonomy action attributes to the same action_type the ledger
# learned under). See organize_tools/delete_tools ``record_action`` calls.
REVERSIBLE_AUTO_ACTIONS = frozenset(
    {
        "archive",
        "add_label",
        "add_star",
        "remove_star",
        "mark_read",
        "mark_unread",
    }
)

#: Reply/forward *composition*. These prepare content but never transmit — the
#: send is a separate floor tool. So drafting is safe to do autonomously even
#: though sending is not.
DRAFT_ACTIONS = frozenset({"draft_reply", "draft_forward"})

# Ledger outcome polarity.
OUTCOME_POSITIVE = "positive"
OUTCOME_NEGATIVE = "negative"

Decision = Literal["auto", "draft", "suggest", "confirm"]


@dataclass(frozen=True)
class TrustDecision:
    """The policy's verdict for one candidate action.

    ``action`` is the disposition; ``reason`` is a human-readable rationale
    surfaced in the activity feed and the planned ``gaia email autonomy`` output;
    ``confidence`` is the ledger trust score in ``[0, 1]`` (1.0 for the
    hard-coded floor and for explicit preferences).
    """

    action: Decision
    reason: str
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

EMAIL_TRUST_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS email_trust_ledger (
    action_type  TEXT NOT NULL,
    scope        TEXT NOT NULL,
    positive     INTEGER NOT NULL DEFAULT 0,
    negative     INTEGER NOT NULL DEFAULT 0,
    last_outcome TEXT,
    updated_at   REAL NOT NULL,
    PRIMARY KEY (action_type, scope)
);
"""

# Attribution index: maps each autonomously-executed action back to the
# ``(action_type, sender, category)`` scope it was decided under. When the user
# later undoes that action (a correction), :func:`lookup_autonomy_action`
# recovers the scope so the negative signal lands on the right ledger rows.
# One row per auto-executed action; ``resolved`` guards against a single undo
# being counted twice.
EMAIL_AUTONOMY_ACTIONS_DDL = """
CREATE TABLE IF NOT EXISTS email_autonomy_actions (
    action_id    TEXT PRIMARY KEY,
    action_type  TEXT NOT NULL,
    sender       TEXT,
    category     TEXT,
    created_at   REAL NOT NULL,
    resolved     INTEGER NOT NULL DEFAULT 0
);
"""


# Open-proposal ledger: one row per message the cycle has already proposed an
# action for and that has not yet been resolved. Without this, every heartbeat
# re-proposes the same still-in-inbox message and GoalStore accumulates a
# duplicate pending goal each fire. Keyed ``(message_id, action_type)``.
EMAIL_AUTONOMY_PROPOSALS_DDL = """
CREATE TABLE IF NOT EXISTS email_autonomy_proposals (
    message_id   TEXT NOT NULL,
    action_type  TEXT NOT NULL,
    created_at   REAL NOT NULL,
    resolved     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (message_id, action_type)
);
"""


def init_trust_schema(db) -> None:
    """Create the ledger + attribution tables if absent. Idempotent."""
    db.execute(EMAIL_TRUST_LEDGER_DDL)
    db.execute(EMAIL_AUTONOMY_ACTIONS_DDL)
    db.execute(EMAIL_AUTONOMY_PROPOSALS_DDL)


def has_open_proposal(db, *, message_id: str, action_type: str) -> bool:
    """True when an unresolved proposal already exists for this message+action.

    The re-proposal guard: a message the cycle proposed last fire and that the
    user has not acted on yet must not be proposed again.
    """
    row = db.query(
        "SELECT 1 FROM email_autonomy_proposals WHERE message_id = :m "
        "AND action_type = :a AND resolved = 0",
        {"m": message_id, "a": action_type},
        one=True,
    )
    return row is not None


def record_proposal(
    db,
    *,
    message_id: str,
    action_type: str,
    now: Optional[float] = None,
) -> None:
    """Mark that an action has been proposed for a message. Idempotent."""
    ts = time.time() if now is None else now
    db.query(
        "INSERT OR IGNORE INTO email_autonomy_proposals "
        "(message_id, action_type, created_at, resolved) VALUES (:m, :a, :ts, 0)",
        {"m": message_id, "a": action_type, "ts": ts},
    )


def resolve_proposal(db, *, message_id: str, action_type: str) -> None:
    """Clear the open-proposal guard for a message (acted on or superseded)."""
    db.update(
        "email_autonomy_proposals",
        {"resolved": 1},
        "message_id = :m AND action_type = :a",
        {"m": message_id, "a": action_type},
    )


def record_autonomy_action(
    db,
    *,
    action_id: str,
    action_type: str,
    sender: str = "",
    category: str = "",
    now: Optional[float] = None,
) -> None:
    """Index one auto-executed action so a later undo can be attributed.

    Idempotent on ``action_id`` (INSERT OR REPLACE) — re-recording the same
    action id overwrites rather than duplicating.
    """
    ts = time.time() if now is None else now
    # ``db.insert`` commits (query() does not); action_id is a fresh uuid PK so a
    # plain insert is safe — no REPLACE needed. Committing matters for the
    # scheduler's per-run agent, which closes its connection after the cycle: an
    # uncommitted index row would be lost and the later undo never attributed.
    db.insert(
        "email_autonomy_actions",
        {
            "action_id": action_id,
            "action_type": action_type,
            "sender": sender,
            "category": category,
            "created_at": ts,
            "resolved": 0,
        },
    )


def lookup_autonomy_action(db, *, action_id: str) -> Optional[Dict[str, Any]]:
    """Return the unresolved index row for an action id, or None.

    A row already marked ``resolved`` returns None so the same undo can't be
    scored twice.
    """
    return db.query(
        "SELECT action_id, action_type, sender, category FROM "
        "email_autonomy_actions WHERE action_id = :id AND resolved = 0",
        {"id": action_id},
        one=True,
    )


def mark_autonomy_action_resolved(db, *, action_id: str) -> None:
    """Flag an indexed action as resolved (its correction has been counted)."""
    db.update(
        "email_autonomy_actions",
        {"resolved": 1},
        "action_id = :id",
        {"id": action_id},
    )


# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------


def category_scope(category: str) -> str:
    """Scope key for a triage category, e.g. ``category:PROMOTIONAL``."""
    return f"category:{(category or '').strip().upper()}"


def sender_scope(sender: str) -> str:
    """Scope key for a sender address, e.g. ``sender:news@x.com``."""
    return f"sender:{(sender or '').strip().lower()}"


# ---------------------------------------------------------------------------
# TrustLedger — the earned-evidence tally
# ---------------------------------------------------------------------------


class TrustLedger:
    """Pure-function accessors over the ``email_trust_ledger`` table.

    Every method takes a ``DatabaseMixin``-typed ``db`` as its first argument
    and never reaches into the agent class — same discipline as
    ``action_store``. Instances hold only the trust thresholds so a
    :class:`TrustPolicy` can share one configured ledger.
    """

    def __init__(self, *, min_samples: int = 5, threshold: float = 0.85) -> None:
        if min_samples < 1:
            raise ValueError(
                f"TrustLedger min_samples must be >= 1, got {min_samples!r}"
            )
        if not 0.0 < threshold <= 1.0:
            raise ValueError(
                f"TrustLedger threshold must be in (0, 1], got {threshold!r}"
            )
        self.min_samples = min_samples
        self.threshold = threshold

    @staticmethod
    def record_outcome(
        db,
        *,
        action_type: str,
        scope: str,
        positive: bool,
        now: Optional[float] = None,
    ) -> None:
        """Increment the positive or negative tally for one scope.

        Upsert: create the row on first sight, otherwise bump the counter.
        A single decision that the user accepted / left standing is positive;
        one they rejected / undid / edited is negative.
        """
        ts = time.time() if now is None else now
        outcome = OUTCOME_POSITIVE if positive else OUTCOME_NEGATIVE
        # Atomic upsert (single statement) so two concurrent first-writes to the
        # same (action_type, scope) — the session agent and a scheduler-built
        # agent share one on-disk DB — can't both INSERT and collide on the PK.
        # Wrapped in a transaction so the write commits (query() alone does not).
        with db.transaction():
            db.query(
                "INSERT INTO email_trust_ledger "
                "(action_type, scope, positive, negative, last_outcome, updated_at) "
                "VALUES (:a, :s, :pos, :neg, :outcome, :ts) "
                "ON CONFLICT(action_type, scope) DO UPDATE SET "
                "positive = positive + :pos, negative = negative + :neg, "
                "last_outcome = :outcome, updated_at = :ts",
                {
                    "a": action_type,
                    "s": scope,
                    "pos": 1 if positive else 0,
                    "neg": 0 if positive else 1,
                    "outcome": outcome,
                    "ts": ts,
                },
            )

    @staticmethod
    def get_stats(db, *, action_type: str, scope: str) -> Dict[str, Any]:
        """Return ``{positive, negative, total, score}`` for a scope.

        ``score`` is ``positive / total`` (0.0 when there is no evidence yet).
        """
        row = db.query(
            "SELECT positive, negative FROM email_trust_ledger "
            "WHERE action_type = :a AND scope = :s",
            {"a": action_type, "s": scope},
            one=True,
        )
        pos = int(row["positive"]) if row else 0
        neg = int(row["negative"]) if row else 0
        total = pos + neg
        score = (pos / total) if total else 0.0
        return {"positive": pos, "negative": neg, "total": total, "score": score}

    def is_trusted(self, db, *, action_type: str, scope: str) -> bool:
        """True when a scope has earned enough correct outcomes to auto-run.

        Requires BOTH a minimum sample count (so a single lucky call can't
        unlock autonomy) AND an accuracy at/above the threshold.
        """
        stats = self.get_stats(db, action_type=action_type, scope=scope)
        return stats["total"] >= self.min_samples and stats["score"] >= self.threshold

    @staticmethod
    def list_ledger(db) -> List[Dict[str, Any]]:
        """Every ledger row, most-recently-updated first (for the CLI/UI)."""
        return db.query(
            "SELECT action_type, scope, positive, negative, last_outcome, "
            "updated_at FROM email_trust_ledger ORDER BY updated_at DESC"
        )


# ---------------------------------------------------------------------------
# TrustPolicy — the decision layer
# ---------------------------------------------------------------------------


class TrustPolicy:
    """Decide the disposition of a candidate autonomous action.

    Construct with the configured autonomy level, the ledger, and the agent's
    inviolable confirm-floor set. :meth:`decide` returns a
    :class:`TrustDecision`.
    """

    def __init__(
        self,
        *,
        level: str,
        ledger: TrustLedger,
        confirm_floor: frozenset,
    ) -> None:
        if level not in AUTONOMY_LEVELS:
            raise ValueError(
                f"TrustPolicy level must be one of {AUTONOMY_LEVELS}, got {level!r}"
            )
        self.level = level
        self.ledger = ledger
        self.confirm_floor = frozenset(confirm_floor)

    @property
    def enabled(self) -> bool:
        """False only at :data:`LEVEL_OFF` — the loop should not run at all."""
        return self.level != LEVEL_OFF

    def _explicitly_preferred(
        self,
        *,
        action_type: str,
        category: str,
        sender: str,
        preferences: Optional[Mapping[str, Any]],
    ) -> bool:
        """True when the user's own preferences already sanction this action.

        An explicit preference is a direct instruction, so it grants autonomy
        immediately without waiting on the ledger:

        - ``archive`` of a low-priority sender or a category defaulted to
          ``archive`` (from ``preference_tools``).
        """
        if not preferences:
            return False
        if action_type == "archive":
            low = preferences.get("low_priority_senders") or ()
            if sender and sender.strip().lower() in {str(s).lower() for s in low}:
                return True
            defaults = preferences.get("category_defaults") or {}
            cat = (category or "").strip().upper()
            if str(defaults.get(cat, "")).lower() == "archive":
                return True
        return False

    def decide(
        self,
        *,
        tool: str,
        action_type: str,
        category: str = "",
        sender: str = "",
        db: Any = None,
        preferences: Optional[Mapping[str, Any]] = None,
    ) -> TrustDecision:
        """Return the disposition for one candidate action.

        ``tool`` is the tool name (checked against the floor); ``action_type``
        is the taxonomy key (``archive``, ``draft_reply``, …).
        """
        # 1. Inviolable floor — no level, trust score, or preference lowers it.
        if tool in self.confirm_floor:
            return TrustDecision(
                "confirm",
                reason=f"{tool} is destructive/irreversible — always requires "
                "your confirmation",
                confidence=1.0,
            )

        # 2. Loop disabled.
        if self.level == LEVEL_OFF:
            return TrustDecision("suggest", reason="autonomy is off", confidence=0.0)

        # 3. Reply composition is always a draft — safe to write, never to send.
        if action_type in DRAFT_ACTIONS:
            return TrustDecision(
                "draft",
                reason="reply drafted for your review; sending needs confirmation",
                confidence=0.0,
            )

        # 4. Only reversible actions are candidates for auto-execution.
        if action_type not in REVERSIBLE_AUTO_ACTIONS:
            return TrustDecision(
                "suggest",
                reason=f"{action_type} is not an auto-eligible reversible action",
                confidence=0.0,
            )

        # 5. suggest level never auto-executes.
        if self.level == LEVEL_SUGGEST:
            return TrustDecision(
                "suggest", reason="autonomy level is suggest-only", confidence=0.0
            )

        # 6. full level auto-executes every reversible action.
        if self.level == LEVEL_FULL:
            return TrustDecision(
                "auto",
                reason="autonomy level is full — reversible action auto-executed",
                confidence=1.0,
            )

        # 7. earn_trust: auto only when explicitly preferred OR ledger-proven.
        scope_sender = sender_scope(sender) if sender else ""
        scope_cat = category_scope(category) if category else ""

        if self._explicitly_preferred(
            action_type=action_type,
            category=category,
            sender=sender,
            preferences=preferences,
        ):
            return TrustDecision(
                "auto",
                reason="you set an explicit preference for this",
                confidence=1.0,
            )

        if db is not None:
            for scope in (scope_sender, scope_cat):
                if not scope:
                    continue
                if self.ledger.is_trusted(db, action_type=action_type, scope=scope):
                    stats = self.ledger.get_stats(
                        db, action_type=action_type, scope=scope
                    )
                    return TrustDecision(
                        "auto",
                        reason=(
                            f"proven on {scope} "
                            f"({stats['positive']}/{stats['total']} correct)"
                        ),
                        confidence=stats["score"],
                    )

        # Not yet trusted — propose and learn from the answer.
        best = 0.0
        if db is not None:
            for scope in (scope_sender, scope_cat):
                if scope:
                    best = max(
                        best,
                        self.ledger.get_stats(db, action_type=action_type, scope=scope)[
                            "score"
                        ],
                    )
        return TrustDecision(
            "suggest",
            reason="not yet proven for this sender/category — learning from your "
            "choice",
            confidence=best,
        )
