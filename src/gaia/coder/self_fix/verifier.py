# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Post-merge verification (§7.4 step 10).

Triggered by the ``PR-merged-where-author=self`` EventBridge event; here we
expose the synchronous verifier that:

1. re-runs the regression test on the merged SHA,
2. transitions the feedback row to ``verified``,
3. writes a :data:`memory.db` entry in the ``failure_patterns`` topic so the
   same symptom is recognised next time,
4. writes a companion ``review_patterns`` entry that tags which fix-class
   landed cleanly — Pass 6 (§8) consults this topic at future reviews.

The memory writer is loosely coupled to ``gaia.coder.stores.memory`` — that
module landed in the Phase-2 stores task, so we import it directly. The
feedback writer uses ``gaia.coder.stores.feedback``.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from gaia.coder.stores import feedback as feedback_store
from gaia.coder.stores import memory as memory_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationResult:
    """Return value of :func:`verify_on_merge`."""

    feedback_id: str
    merged_sha: str
    regression_test_path: str
    regression_returncode: int
    verified: bool
    failure_pattern_id: Optional[str] = None
    review_pattern_id: Optional[str] = None
    notes: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_git(
    args: Sequence[str], *, cwd: Path, check: bool = True
) -> subprocess.CompletedProcess:
    completed = subprocess.run(  # pylint: disable=subprocess-run-check
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: rc={completed.returncode} "
            f"stderr={completed.stderr!r}"
        )
    return completed


def _run_regression_test(
    repo_root: Path, regression_test_path: str
) -> subprocess.CompletedProcess:
    return subprocess.run(  # pylint: disable=subprocess-run-check
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-header",
            "--no-summary",
            regression_test_path,
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )


def _append_note(existing_notes_json: str, entry: Mapping[str, Any]) -> str:
    """Append a note to the JSON array stored in ``feedback.notes_json``.

    Corrupted / wrong-type content raises — the audit trail is canonical
    and silently replacing it with ``[]`` would hide a regression.
    Cf. #825 auto-review. Mirrors :func:`loop_driver._append_notes`.
    """
    if not existing_notes_json:
        parsed: list = []
    else:
        try:
            parsed = json.loads(existing_notes_json)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"feedback notes_json is corrupted and cannot be extended: {exc}"
            ) from exc
        if not isinstance(parsed, list):
            raise ValueError(
                f"feedback notes_json must be a JSON array, got "
                f"{type(parsed).__name__}"
            )
    parsed.append(dict(entry))
    return json.dumps(parsed)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def verify_on_merge(
    merged_sha: str,
    feedback_id: str,
    regression_test_path: str,
    *,
    repo_root: Path,
    feedback_db_path: Path,
    memory_db_path: Path,
    checkout_merged: bool = True,
) -> VerificationResult:
    """Re-run the regression test on ``merged_sha`` and persist outcomes.

    Writes two memory rows on success:

    * a ``failure_patterns`` row with the feedback id and a pattern blob of
      the classifier's root cause — so ``introspect_memory('failure_patterns', …)``
      can surface it on the next similar feedback,
    * a ``review_patterns`` row marking which fix-class landed cleanly, per
      §8 Pass 6's use of prior review patterns.

    Raises:
        FileNotFoundError: if the regression test file does not exist under
            ``repo_root``. Fail-loudly; §7.4 step 5 requires the test to
            exist on the merged branch.
        RuntimeError: if the regression test fails on ``merged_sha``. A
            green merge with a red regression test means something regressed
            between merge-time review and post-merge verification, which
            must page the EM rather than silently close the feedback record.
    """
    repo_root = Path(repo_root).resolve()
    test_abs = repo_root / regression_test_path
    if not test_abs.exists():
        raise FileNotFoundError(
            f"verify_on_merge: regression test {regression_test_path!r} "
            f"does not exist under {repo_root!s}"
        )

    restore_to: Optional[str] = None
    if checkout_merged:
        restore_to = _run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root
        ).stdout.strip()
        _run_git(["checkout", merged_sha], cwd=repo_root)

    try:
        rc_run = _run_regression_test(repo_root, regression_test_path)
    finally:
        if checkout_merged and restore_to:
            _run_git(["checkout", restore_to], cwd=repo_root, check=False)

    if rc_run.returncode != 0:
        # Fail-loudly — a red regression test post-merge is a correctness
        # alarm, not a soft warning.
        raise RuntimeError(
            f"verify_on_merge: regression test {regression_test_path!r} "
            f"FAILED on merged SHA {merged_sha} (rc={rc_run.returncode}). "
            "Stdout:\n"
            + (rc_run.stdout or "(empty)")
            + "\nStderr:\n"
            + (rc_run.stderr or "(empty)")
        )

    # --- 1. Transition feedback row to 'verified' --------------------------
    conn = feedback_store.open_store(feedback_db_path)
    try:
        row = feedback_store.get_row(conn, feedback_id)
        if row is None:
            raise ValueError(
                f"verify_on_merge: feedback {feedback_id!r} not found in "
                f"{feedback_db_path!s}"
            )
        new_notes = _append_note(
            row.notes_json,
            {
                "at": _now_iso(),
                "transition": "fix-pr-open → verified",
                "merged_sha": merged_sha,
                "regression_test_path": regression_test_path,
            },
        )
        feedback_store.update_row(
            conn,
            feedback_id,
            {
                "state": "verified",
                "regression_test_path": regression_test_path,
                "notes_json": new_notes,
            },
        )
    finally:
        conn.close()

    # --- 2. Write memory records ------------------------------------------
    fix_class = row.fix_class or "unknown"
    root_cause = row.root_cause or ""
    mem_conn = memory_store.open_store(memory_db_path)
    failure_id = str(uuid.uuid4())
    review_id = str(uuid.uuid4())
    try:
        memory_store.insert_row(
            mem_conn,
            memory_store.MemoryRow(
                id=failure_id,
                topic="failure_patterns",
                created_at=_now_iso(),
                source_kind="feedback",
                source_id=feedback_id,
                payload_json=json.dumps(
                    {
                        "fix_class": fix_class,
                        "root_cause": root_cause,
                        "feedback_body": row.body,
                        "regression_test_path": regression_test_path,
                        "merged_sha": merged_sha,
                    }
                ),
                embedding_key=f"failure:{feedback_id}",
                confidence=85,
            ),
        )
        memory_store.insert_row(
            mem_conn,
            memory_store.MemoryRow(
                id=review_id,
                topic="review_patterns",
                created_at=_now_iso(),
                source_kind="feedback",
                source_id=feedback_id,
                payload_json=json.dumps(
                    {
                        "fix_class": fix_class,
                        "outcome": "verified",
                        "merged_sha": merged_sha,
                    }
                ),
                embedding_key=f"review:{feedback_id}",
                confidence=80,
            ),
        )
    finally:
        mem_conn.close()

    logger.info(
        "verify_on_merge: feedback %s verified on %s; wrote memory rows "
        "failure=%s review=%s",
        feedback_id,
        merged_sha,
        failure_id,
        review_id,
    )
    return VerificationResult(
        feedback_id=feedback_id,
        merged_sha=merged_sha,
        regression_test_path=regression_test_path,
        regression_returncode=rc_run.returncode,
        verified=True,
        failure_pattern_id=failure_id,
        review_pattern_id=review_id,
        notes=(rc_run.stdout or "").strip()[:400],
    )


__all__ = [
    "VerificationResult",
    "verify_on_merge",
]
