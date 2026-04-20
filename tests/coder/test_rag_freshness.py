# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for :mod:`gaia.coder.rag_freshness` (§6.9).

No real RAG backend, no real ``git``. Every external call is stubbed.
"""

from __future__ import annotations

import datetime as dt
import subprocess
from typing import Dict

import pytest

from gaia.coder.rag_freshness import (
    CitationStaleError,
    CorpusContract,
    CorpusStatus,
    FreshnessContract,
    RagStatusReport,
    StaleIndexError,
    WatchdogAlert,
    check_citation_valid,
    ensure_fresh_or_raise,
    rag_rebuild,
    rag_refresh,
    rag_status,
    reindex_watchdog,
    verdict_for,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_minus_hours(h: float) -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=h)


def _status(
    name: str, *, hours_old: float = 0.5, docs: int = 100
) -> CorpusStatus:
    return CorpusStatus(
        name=name,
        last_indexed_at=_now_minus_hours(hours_old),
        document_count=docs,
        pending_reindex=False,
    )


# ---------------------------------------------------------------------------
# Contract construction
# ---------------------------------------------------------------------------


def test_default_contract_matches_spec():
    c = FreshnessContract.default()
    names = [corpus.name for corpus in c.corpora]
    # §6.9 table — five corpora.
    assert names == [
        "source_tree",
        "pr_descriptions",
        "issues",
        "adrs_plans",
        "claude_agents_md",
    ]
    assert c.watchdog_hours == 36.0


def test_by_name_raises_on_unknown():
    c = FreshnessContract.default()
    with pytest.raises(KeyError, match="unknown corpus"):
        c.by_name("does-not-exist")


# ---------------------------------------------------------------------------
# verdict_for
# ---------------------------------------------------------------------------


def test_verdict_fresh():
    contract = FreshnessContract.default().by_name("source_tree")
    status = _status("source_tree", hours_old=1)
    v = verdict_for(status, contract)
    assert v.fresh is True
    assert v.age_hours is not None and v.age_hours < 2


def test_verdict_stale_past_threshold():
    contract = FreshnessContract.default().by_name("source_tree")
    status = _status("source_tree", hours_old=20)
    v = verdict_for(status, contract)
    assert v.fresh is False
    assert "threshold" in v.reason


def test_verdict_never_indexed_is_stale():
    contract = FreshnessContract.default().by_name("source_tree")
    status = CorpusStatus(
        name="source_tree",
        last_indexed_at=None,
        document_count=0,
        pending_reindex=False,
    )
    v = verdict_for(status, contract)
    assert v.fresh is False
    assert "never" in v.reason


# ---------------------------------------------------------------------------
# StaleIndexError — §6.9 fail-loudly
# ---------------------------------------------------------------------------


def test_ensure_fresh_passes_on_fresh():
    contract = FreshnessContract.default().by_name("issues")
    ensure_fresh_or_raise(_status("issues", hours_old=0.1), contract)


def test_ensure_fresh_raises_on_stale():
    contract = FreshnessContract.default().by_name("issues")
    stale = _status("issues", hours_old=48)
    with pytest.raises(StaleIndexError) as exc:
        ensure_fresh_or_raise(stale, contract)
    assert exc.value.corpus == "issues"
    assert exc.value.age_hours > 12


def test_stale_index_error_message_points_at_refresh():
    contract = FreshnessContract.default().by_name("issues")
    try:
        ensure_fresh_or_raise(_status("issues", hours_old=48), contract)
    except StaleIndexError as e:
        assert "gaia-coder rag refresh" in str(e)


# ---------------------------------------------------------------------------
# reindex_watchdog
# ---------------------------------------------------------------------------


def _provider(statuses: Dict[str, CorpusStatus]):
    def _get():
        return statuses

    return _get


def test_watchdog_quiet_when_all_fresh():
    contract = FreshnessContract.default()
    provider = _provider(
        {c.name: _status(c.name, hours_old=5) for c in contract.corpora}
    )
    alert = reindex_watchdog(contract, provider)
    assert alert.fired is False
    assert alert.severity == "info"


def test_watchdog_fires_at_40h():
    contract = FreshnessContract.default()
    statuses = {c.name: _status(c.name, hours_old=5) for c in contract.corpora}
    statuses["source_tree"] = _status("source_tree", hours_old=40)
    alert = reindex_watchdog(contract, _provider(statuses))
    assert alert.fired is True
    assert alert.severity == "critical"
    assert "source_tree" in alert.affected_corpora


def test_watchdog_fires_for_never_indexed_corpus():
    contract = FreshnessContract.default()
    statuses = {c.name: _status(c.name, hours_old=5) for c in contract.corpora}
    statuses["issues"] = CorpusStatus(
        name="issues",
        last_indexed_at=None,
        document_count=0,
        pending_reindex=False,
    )
    alert = reindex_watchdog(contract, _provider(statuses))
    assert alert.fired is True
    assert "issues" in alert.affected_corpora


def test_watchdog_fires_when_corpus_missing_entirely():
    """A provider that doesn't return a row for a known corpus trips the watchdog."""
    contract = FreshnessContract.default()
    # Only three of five corpora present.
    statuses = {
        c.name: _status(c.name, hours_old=5)
        for c in contract.corpora[:3]
    }
    alert = reindex_watchdog(contract, _provider(statuses))
    assert alert.fired is True
    # The two missing corpora are reported.
    affected = set(alert.affected_corpora)
    assert affected == {"adrs_plans", "claude_agents_md"}


# ---------------------------------------------------------------------------
# check_citation_valid — Pass 3 architectural check (§6.9 rule 2)
# ---------------------------------------------------------------------------


def test_check_citation_valid_on_existing_file(tmp_path):
    captured: list = []

    def _runner(argv):
        captured.append(list(argv))
        return ""  # git cat-file -e is silent on success

    assert (
        check_citation_valid(
            "README.md",
            git_ref="HEAD",
            repo_root=str(tmp_path),
            runner=_runner,
        )
        is True
    )
    # Verify the argv shape matches what the docstring promises.
    assert captured == [
        ["git", "-C", str(tmp_path), "cat-file", "-e", "HEAD:README.md"]
    ]


def test_check_citation_raises_on_deleted_file(tmp_path):
    def _runner(argv):
        from gaia.coder.rag_freshness import _GitRunError

        raise _GitRunError("git exited 128: does not exist in HEAD")

    with pytest.raises(CitationStaleError, match="Pass 3"):
        check_citation_valid(
            "ghost.py",
            git_ref="HEAD",
            repo_root=str(tmp_path),
            runner=_runner,
        )


def test_check_citation_supports_ref_other_than_head(tmp_path):
    captured: list = []

    def _runner(argv):
        captured.append(argv)
        return ""

    check_citation_valid(
        "src/gaia/cli.py",
        git_ref="coder~3",
        repo_root=str(tmp_path),
        runner=_runner,
    )
    assert captured[0][-1] == "coder~3:src/gaia/cli.py"


# ---------------------------------------------------------------------------
# rag_status — aggregator
# ---------------------------------------------------------------------------


def test_rag_status_assembles_report():
    contract = FreshnessContract.default()
    provider = _provider(
        {c.name: _status(c.name, hours_old=1) for c in contract.corpora}
    )
    report = rag_status(contract, provider)
    assert isinstance(report, RagStatusReport)
    assert len(report.corpora) == 5
    assert len(report.verdicts) == 5
    assert report.any_stale is False
    assert report.watchdog.fired is False


def test_rag_status_any_stale_reflects_verdicts():
    contract = FreshnessContract.default()
    statuses = {c.name: _status(c.name, hours_old=1) for c in contract.corpora}
    statuses["pr_descriptions"] = _status("pr_descriptions", hours_old=50)
    report = rag_status(contract, _provider(statuses))
    assert report.any_stale is True
    stale_names = [v.corpus for v in report.verdicts if not v.fresh]
    assert stale_names == ["pr_descriptions"]


def test_rag_status_to_dict_json_serialisable():
    import json

    contract = FreshnessContract.default()
    provider = _provider(
        {c.name: _status(c.name, hours_old=1) for c in contract.corpora}
    )
    report = rag_status(contract, provider)
    # Must be JSON-serialisable — CLI/EM-inbox ships as JSON.
    json.dumps(report.to_dict())


# ---------------------------------------------------------------------------
# rag_refresh / rag_rebuild
# ---------------------------------------------------------------------------


def test_rag_refresh_all_corpora():
    contract = FreshnessContract.default()
    calls: list = []

    def _runner(name, mode):
        calls.append((name, mode))
        return {"ok": True}

    out = rag_refresh(contract, _runner)
    assert set(out) == {c.name for c in contract.corpora}
    assert {m for _, m in calls} == {"refresh"}


def test_rag_refresh_single_corpus():
    contract = FreshnessContract.default()
    calls: list = []

    def _runner(name, mode):
        calls.append((name, mode))
        return {"ok": True}

    rag_refresh(contract, _runner, corpus="issues")
    assert calls == [("issues", "refresh")]


def test_rag_refresh_unknown_corpus_raises():
    contract = FreshnessContract.default()
    with pytest.raises(KeyError, match="unknown corpus"):
        rag_refresh(contract, lambda n, m: {}, corpus="nonsense")


def test_rag_rebuild_passes_rebuild_mode():
    contract = FreshnessContract.default()
    calls: list = []

    def _runner(name, mode):
        calls.append((name, mode))
        return {}

    rag_rebuild(contract, _runner, corpus="source_tree")
    assert calls == [("source_tree", "rebuild")]
