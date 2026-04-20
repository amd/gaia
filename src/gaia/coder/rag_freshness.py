# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``rag_freshness`` — the §6.9 freshness contract.

The RAG index is load-bearing for :data:`PROJECT_MAP.md` (§6.5), the
``adr_decisions`` memory topic (§6.8), and the ``localise`` / ``explore``
states. A *stale* index is strictly worse than *no* index — the agent will
confidently cite something that no longer exists. This module exists so
that failure mode cannot happen quietly.

Four responsibilities:

1. :class:`FreshnessContract` — declarative config for each of the five
   §6.9 corpora (source tree / PR descriptions / issues / ADRs / CLAUDE.md).
   The contract plus the live per-corpus status produces the freshness
   verdict the loop queries at plan time.
2. :func:`reindex_watchdog` — surfaces a ``critical``-severity EM-inbox
   message if no reindex has succeeded in 36 h. One of the few automatic
   ``critical`` messages in the system (§6.9).
3. :func:`check_citation_valid` — Pass 3 architectural check that a cited
   file still exists in the working tree at the given git ref. Matches
   §6.9 rule 2 ("cites a file that was indexed but has been deleted/
   renamed").
4. :func:`rag_status` / :func:`rag_refresh` / :func:`rag_rebuild` — the
   Python API behind the ``gaia-coder rag {status|refresh|rebuild}``
   commands. CLI wiring lands in the unification follow-up per the task
   contract; here we just expose the callable API.
"""

from __future__ import annotations

import datetime as dt
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


Trigger = Literal["fs_watch", "webhook", "cron"]
Cadence = Literal["hourly", "daily", "weekly"]


@dataclass(frozen=True)
class CorpusContract:
    """Reindex rules for one corpus.

    Mirrors one row of the §6.9 table. ``triggers`` determine when an
    *incremental* reindex fires; ``cadence`` is the upper-bound rebuild
    cycle regardless of triggers.
    """

    name: str
    source: str  # human-readable description of what's indexed
    triggers: Tuple[Trigger, ...]
    cadence: Cadence
    max_age_hours: float = 12.0  # §6.9 default — stale beyond 12h


@dataclass(frozen=True)
class FreshnessContract:
    """Full §6.9 contract — one :class:`CorpusContract` per corpus."""

    corpora: Tuple[CorpusContract, ...]
    watchdog_hours: float = 36.0
    """Watchdog fires ``critical`` if no reindex in this many hours (§6.9)."""

    def by_name(self, name: str) -> CorpusContract:
        for c in self.corpora:
            if c.name == name:
                return c
        raise KeyError(f"unknown corpus {name!r}; known: {[c.name for c in self.corpora]}")

    @classmethod
    def default(cls) -> "FreshnessContract":
        """The canonical §6.9 contract for the five bound-repo corpora."""
        return cls(
            corpora=(
                CorpusContract(
                    name="source_tree",
                    source="git ls-files at main HEAD",
                    triggers=("fs_watch",),
                    cadence="weekly",
                ),
                CorpusContract(
                    name="pr_descriptions",
                    source="gh api pulls?state=all",
                    triggers=("webhook",),
                    cadence="weekly",
                ),
                CorpusContract(
                    name="issues",
                    source="gh api issues?state=all",
                    triggers=("webhook",),
                    cadence="weekly",
                ),
                CorpusContract(
                    name="adrs_plans",
                    source="docs/plans/*.mdx, docs/spec/*.mdx",
                    triggers=("fs_watch",),
                    cadence="weekly",
                ),
                CorpusContract(
                    name="claude_agents_md",
                    source="CLAUDE.md + AGENTS.md at repo root",
                    triggers=("fs_watch",),
                    cadence="weekly",
                ),
            ),
        )


# ---------------------------------------------------------------------------
# Live status — what corpora report when asked
# ---------------------------------------------------------------------------


@dataclass
class CorpusStatus:
    """One per-corpus row in :func:`rag_status`."""

    name: str
    last_indexed_at: Optional[dt.datetime]  # UTC
    document_count: int
    pending_reindex: bool

    @property
    def age_seconds(self) -> Optional[float]:
        if self.last_indexed_at is None:
            return None
        now = dt.datetime.now(dt.timezone.utc)
        return (now - self.last_indexed_at).total_seconds()

    @property
    def age_hours(self) -> Optional[float]:
        age = self.age_seconds
        return None if age is None else age / 3600.0


@dataclass
class FreshnessVerdict:
    """Aggregated freshness for one corpus (or a query result)."""

    corpus: str
    fresh: bool
    age_hours: Optional[float]
    reason: str


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class StaleIndexError(RuntimeError):
    """Raised when a RAG query returns matches older than
    :attr:`CorpusContract.max_age_hours`.

    Matches §6.9 "fail loudly": the pipeline never returns "matches" when
    the index is known stale beyond the threshold.
    """

    def __init__(self, corpus: str, age_hours: float, threshold_hours: float) -> None:
        self.corpus = corpus
        self.age_hours = age_hours
        self.threshold_hours = threshold_hours
        super().__init__(
            f"RAG index for corpus {corpus!r} is stale: "
            f"age={age_hours:.1f}h > threshold={threshold_hours:.1f}h. "
            "Run `gaia-coder rag refresh` to re-sync."
        )


class CitationStaleError(RuntimeError):
    """Raised when :func:`check_citation_valid` detects a cited path that
    no longer exists in the working tree (§6.9 rule 2)."""


# ---------------------------------------------------------------------------
# Corpus-status provider protocol
# ---------------------------------------------------------------------------


#: A callable that returns the current :class:`CorpusStatus` for each
#: corpus name. Real implementations query the RAG backend; tests pass a
#: stub. Decoupled so this module has zero RAG-backend dependencies.
StatusProvider = Callable[[], Dict[str, CorpusStatus]]


# ---------------------------------------------------------------------------
# Freshness verdict — per-corpus and per-query
# ---------------------------------------------------------------------------


def verdict_for(
    status: CorpusStatus, contract: CorpusContract
) -> FreshnessVerdict:
    """Compare a live :class:`CorpusStatus` against its :class:`CorpusContract`."""
    if status.last_indexed_at is None:
        return FreshnessVerdict(
            corpus=status.name,
            fresh=False,
            age_hours=None,
            reason=f"corpus {status.name!r} has never been indexed",
        )
    age_h = status.age_hours
    assert age_h is not None  # last_indexed_at is set → age is defined
    if age_h > contract.max_age_hours:
        return FreshnessVerdict(
            corpus=status.name,
            fresh=False,
            age_hours=age_h,
            reason=(
                f"corpus {status.name!r} is {age_h:.1f}h old; "
                f"threshold is {contract.max_age_hours:.1f}h"
            ),
        )
    return FreshnessVerdict(
        corpus=status.name,
        fresh=True,
        age_hours=age_h,
        reason=f"corpus {status.name!r} last indexed {age_h:.1f}h ago",
    )


def ensure_fresh_or_raise(
    status: CorpusStatus, contract: CorpusContract
) -> None:
    """Raise :class:`StaleIndexError` if ``status`` violates ``contract``.

    Used by the RAG query wrapper as the hard gate before surfacing any
    matches — silent degradation to stale results is prohibited (§6.9).
    """
    v = verdict_for(status, contract)
    if not v.fresh:
        raise StaleIndexError(
            corpus=status.name,
            age_hours=v.age_hours or float("inf"),
            threshold_hours=contract.max_age_hours,
        )


# ---------------------------------------------------------------------------
# Watchdog — critical EM-inbox message at 36h
# ---------------------------------------------------------------------------


@dataclass
class WatchdogAlert:
    """Outcome of :func:`reindex_watchdog`.

    If ``fired`` is ``True``, the caller forwards ``message`` to the EM
    inbox with severity ``critical`` (§4.5).
    """

    fired: bool
    severity: Literal["info", "critical"]
    message: str
    affected_corpora: Tuple[str, ...]


def reindex_watchdog(
    contract: FreshnessContract, provider: StatusProvider
) -> WatchdogAlert:
    """Surface a ``critical`` inbox message if any corpus is past the watchdog.

    The watchdog is anchored at :attr:`FreshnessContract.watchdog_hours`
    (default 36h). A corpus that has *never* indexed also trips the
    watchdog because that is the first-run-broken failure mode.
    """
    statuses = provider()
    affected: List[str] = []
    for corpus in contract.corpora:
        s = statuses.get(corpus.name)
        if s is None:
            affected.append(corpus.name)
            continue
        age_h = s.age_hours
        if age_h is None or age_h > contract.watchdog_hours:
            affected.append(corpus.name)
    if not affected:
        return WatchdogAlert(
            fired=False,
            severity="info",
            message="All corpora indexed within watchdog window.",
            affected_corpora=(),
        )
    corpora_list = ", ".join(affected)
    return WatchdogAlert(
        fired=True,
        severity="critical",
        message=(
            f"RAG index has not refreshed in {contract.watchdog_hours:.0f}h "
            f"for corpora: {corpora_list}. "
            "Check the `gaia-coder rag status` diagnosis."
        ),
        affected_corpora=tuple(affected),
    )


# ---------------------------------------------------------------------------
# Citation check — Pass 3 architectural gate (§6.9 rule 2)
# ---------------------------------------------------------------------------


def check_citation_valid(
    path: str,
    git_ref: str = "HEAD",
    *,
    repo_root: Optional[str | Path] = None,
    runner: Optional[Callable[[List[str]], str]] = None,
) -> bool:
    """Return True iff ``path`` exists at ``git_ref`` in ``repo_root``.

    Uses ``git cat-file -e <ref>:<path>`` — fast and avoids checkout. A
    non-zero exit means the path does not exist at that ref and raises
    :class:`CitationStaleError`.

    Args:
        path: Repo-relative POSIX path.
        git_ref: Ref to check against (default HEAD).
        repo_root: Repo root (default CWD).
        runner: Subprocess runner for tests. Defaults to direct
            :mod:`subprocess` call.
    """
    run = runner or _default_git_runner
    root = Path(repo_root) if repo_root else Path.cwd()
    try:
        run(
            [
                "git",
                "-C",
                str(root),
                "cat-file",
                "-e",
                f"{git_ref}:{path}",
            ]
        )
    except _GitRunError as e:
        raise CitationStaleError(
            f"citation to {path!r} at ref {git_ref!r} is invalid: "
            f"{e}. Pass 3 architectural check (§6.9) fails."
        ) from e
    return True


class _GitRunError(RuntimeError):
    """Internal — raised by :func:`_default_git_runner` on non-zero exit."""


def _default_git_runner(argv: List[str]) -> str:
    """Invoke ``git`` via :mod:`subprocess`, returning stdout on success."""
    completed = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise _GitRunError(
            f"git exited {completed.returncode}: {' '.join(argv)} "
            f"(stderr: {completed.stderr.strip()})"
        )
    return completed.stdout


# ---------------------------------------------------------------------------
# rag status / refresh / rebuild (§6.9)
# ---------------------------------------------------------------------------


@dataclass
class RagStatusReport:
    """Structured result of :func:`rag_status`."""

    corpora: Tuple[CorpusStatus, ...]
    verdicts: Tuple[FreshnessVerdict, ...]
    watchdog: WatchdogAlert

    @property
    def any_stale(self) -> bool:
        return any(not v.fresh for v in self.verdicts)

    def to_dict(self) -> dict:
        return {
            "corpora": [
                {
                    "name": c.name,
                    "document_count": c.document_count,
                    "pending_reindex": c.pending_reindex,
                    "last_indexed_at": (
                        c.last_indexed_at.isoformat()
                        if c.last_indexed_at
                        else None
                    ),
                    "age_hours": c.age_hours,
                }
                for c in self.corpora
            ],
            "verdicts": [
                {
                    "corpus": v.corpus,
                    "fresh": v.fresh,
                    "age_hours": v.age_hours,
                    "reason": v.reason,
                }
                for v in self.verdicts
            ],
            "watchdog": {
                "fired": self.watchdog.fired,
                "severity": self.watchdog.severity,
                "message": self.watchdog.message,
                "affected_corpora": list(self.watchdog.affected_corpora),
            },
            "any_stale": self.any_stale,
        }


def rag_status(
    contract: FreshnessContract, provider: StatusProvider
) -> RagStatusReport:
    """Collect per-corpus status + freshness verdicts + watchdog alert.

    Python-level API behind ``gaia-coder rag status``. The CLI wrapper
    renders this to a human-readable table; the agent consumes the
    structured report directly.
    """
    statuses = provider()
    corpora_list: List[CorpusStatus] = []
    verdicts: List[FreshnessVerdict] = []
    for corpus in contract.corpora:
        s = statuses.get(
            corpus.name,
            CorpusStatus(
                name=corpus.name,
                last_indexed_at=None,
                document_count=0,
                pending_reindex=False,
            ),
        )
        corpora_list.append(s)
        verdicts.append(verdict_for(s, corpus))
    watchdog = reindex_watchdog(contract, provider)
    return RagStatusReport(
        corpora=tuple(corpora_list),
        verdicts=tuple(verdicts),
        watchdog=watchdog,
    )


#: Reindex backend — callable taking (corpus_name, mode) and returning a
#: per-corpus result dict. ``mode`` is "refresh" (incremental) or
#: "rebuild" (full). Decoupled from the RAG backend for testability.
ReindexRunner = Callable[[str, Literal["refresh", "rebuild"]], Dict[str, Any]]


def rag_refresh(
    contract: FreshnessContract,
    runner: ReindexRunner,
    *,
    corpus: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Incremental reindex for ``corpus`` (or all corpora if omitted).

    Returns a ``{corpus_name: per-corpus result}`` map. Raises on unknown
    corpus name — never silently skips.
    """
    names = [corpus] if corpus else [c.name for c in contract.corpora]
    if corpus and corpus not in {c.name for c in contract.corpora}:
        raise KeyError(
            f"unknown corpus {corpus!r}; known: {[c.name for c in contract.corpora]}"
        )
    return {name: runner(name, "refresh") for name in names}


def rag_rebuild(
    contract: FreshnessContract,
    runner: ReindexRunner,
    *,
    corpus: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Full rebuild for ``corpus`` (or all corpora if omitted).

    Same contract as :func:`rag_refresh` but passes ``mode="rebuild"`` so
    the backend discards any existing index rather than updating in
    place. Use sparingly — rebuilds can be expensive.
    """
    names = [corpus] if corpus else [c.name for c in contract.corpora]
    if corpus and corpus not in {c.name for c in contract.corpora}:
        raise KeyError(
            f"unknown corpus {corpus!r}; known: {[c.name for c in contract.corpora]}"
        )
    return {name: runner(name, "rebuild") for name in names}


__all__ = [
    "Cadence",
    "CitationStaleError",
    "CorpusContract",
    "CorpusStatus",
    "FreshnessContract",
    "FreshnessVerdict",
    "RagStatusReport",
    "ReindexRunner",
    "StaleIndexError",
    "StatusProvider",
    "Trigger",
    "WatchdogAlert",
    "check_citation_valid",
    "ensure_fresh_or_raise",
    "rag_rebuild",
    "rag_refresh",
    "rag_status",
    "reindex_watchdog",
    "verdict_for",
]
