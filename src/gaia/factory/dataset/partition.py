# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Split sessions into a held-out oracle and a development pool.

An LLM that writes the agent *and* its test data *and* grades the result certifies
nothing.  Mining a scenario from session X and evaluating a harness tuned on
session X measures memorization, so the split has to exist before anything is
generated and has to be auditable afterwards.

The rule is ``docs/plans/claude-session-harvest.md``'s:
``int(sha256(session_id)[:8], 16) % 100 < 30`` selects the oracle.  Deterministic,
recomputable by anyone holding the session list, and independent of build order.

**What it does not do**, measured rather than assumed: it does not stop a *work
item* (one branch in one repository) from straddling the split.  On this corpus
58% of sessions share a work item with a session on the other side.  The obvious
fix — assign whole work items — is unusable here because one work item is 17% of
the corpus and spans 12 use-cases, so honouring it destroys per-use-case balance
in both partitions.  Instead every record carries its ``work_item`` key and
:func:`measure_contamination` quantifies the leak, so a consumer who wants the
stricter split can build it from shipped fields.
"""

import hashlib
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

ORACLE = "oracle"
POOL = "pool"

#: Percent of sessions routed to the held-out oracle.
ORACLE_SHARE = 30

#: Branch names that identify no particular unit of work.  A session on detached
#: HEAD or on ``main`` shares nothing with the next one, so grouping them would
#: invent a work item that does not exist.
_ANONYMOUS_BRANCHES = frozenset({"", "HEAD", "main", "master", "detached"})


def session_digest(session_id: str) -> str:
    """The full hex digest; the audit file records it so the split is checkable."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def assign(session_id: str) -> str:
    """``oracle`` or ``pool`` for one session id."""
    return (
        ORACLE if int(session_digest(session_id)[:8], 16) % 100 < ORACLE_SHARE else POOL
    )


def work_item(project: str, git_branch: Optional[str], session_id: str) -> str:
    """A stable key for "the same piece of work", for contamination accounting."""
    branch = (git_branch or "").strip()
    if branch in _ANONYMOUS_BRANCHES:
        return "sess:" + session_id
    return "wi:" + project + "\x00" + branch


@dataclass
class PartitionAudit:
    """Recomputable evidence of how the split fell out."""

    entries: List[Dict[str, Any]]
    oracle_sessions: Set[str]
    pool_sessions: Set[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": "int(sha256(session_id)[:8], 16) % 100 < 30 -> oracle",
            "oracle_share_target_pct": ORACLE_SHARE,
            "oracle_sessions": len(self.oracle_sessions),
            "pool_sessions": len(self.pool_sessions),
            "oracle_share_actual_pct": round(
                100.0
                * len(self.oracle_sessions)
                / max(len(self.oracle_sessions) + len(self.pool_sessions), 1),
                2,
            ),
            "entries": self.entries,
        }


def build_audit(session_ids: Iterable[str]) -> PartitionAudit:
    entries: List[Dict[str, Any]] = []
    oracle: Set[str] = set()
    pool: Set[str] = set()
    for sid in sorted(session_ids):
        digest = session_digest(sid)
        bucket = int(digest[:8], 16) % 100
        side = ORACLE if bucket < ORACLE_SHARE else POOL
        (oracle if side == ORACLE else pool).add(sid)
        entries.append(
            {"session_id": sid, "digest": digest, "bucket": bucket, "partition": side}
        )
    return PartitionAudit(entries=entries, oracle_sessions=oracle, pool_sessions=pool)


def measure_contamination(
    records: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Any], Set[str]]:
    """Quantify what leaks across the split, and name the records that do.

    Two independent measurements, because they answer different questions:

    * **Action overlap** — an ``(tool, arg_hash)`` pair present on both sides is a
      literal answer a tuned harness could have memorised.  Identity hashes the
      full argument object; keying on one argument would merge different edits to
      one file into a fake repeat and overstate this ~40x.
    * **Work-item overlap** — sessions on both sides sharing a branch.  Nothing
      is memorised verbatim, but the codebase is familiar.

    Returns the report and the set of ``record_id`` values whose action appears on
    the other side, so they can be flagged per record.
    """
    oracle_actions: Dict[Tuple[str, str], List[str]] = {}
    pool_actions: Set[Tuple[str, str]] = set()
    oracle_items: Dict[str, Set[str]] = {}
    pool_items: Dict[str, Set[str]] = {}

    for rec in records:
        side = rec["partition"]
        item = rec["work_item"]
        (oracle_items if side == ORACLE else pool_items).setdefault(item, set()).add(
            rec["session_id"]
        )
        for call in rec["action"]["calls"]:
            key = (call["tool"], call["arg_hash"])
            if side == ORACLE:
                oracle_actions.setdefault(key, []).append(rec["record_id"])
            else:
                pool_actions.add(key)

    overlapping = set(oracle_actions) & pool_actions
    flagged: Set[str] = set()
    for key in overlapping:
        flagged.update(oracle_actions[key])

    shared_items = set(oracle_items) & set(pool_items)
    sessions_in_shared = sum(
        len(oracle_items[i]) + len(pool_items[i]) for i in shared_items
    )
    all_sessions = {r["session_id"] for r in records}

    report = {
        "action_overlap": {
            "oracle_distinct_actions": len(oracle_actions),
            "pool_distinct_actions": len(pool_actions),
            "overlapping_actions": len(overlapping),
            "pct_of_oracle_actions": round(
                100.0 * len(overlapping) / max(len(oracle_actions), 1), 2
            ),
            "oracle_records_flagged": len(flagged),
        },
        "work_item_overlap": {
            "work_items_spanning_partitions": len(shared_items),
            "sessions_in_spanning_items": sessions_in_shared,
            "pct_of_sessions": round(
                100.0 * sessions_in_shared / max(len(all_sessions), 1), 2
            ),
        },
        "interpretation": (
            "Action overlap governs whether a tuned harness can have memorised a "
            "literal answer, and is the number to quote for a step-level dataset. "
            "Work-item overlap governs repository familiarity, which is real and "
            "unfixable here without destroying per-use-case balance. Neither makes "
            "this a release gate: an auto-mined oracle is a regression detector, "
            "not a human-curated held-out set."
        ),
    }
    return report, flagged
