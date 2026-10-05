# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Retrieval quality: does the index find the code a real fix changed?

For each query the repository is moved to the issue's base commit and
re-indexed (incrementally: one working tree per repository, so only files that
differ from the previous base are re-embedded). The issue text goes to
``CodeIndexSDK.search`` and to the ``git grep`` baseline; both rankings are
scored against the gold patch's files and symbols.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from gaia.eval.code_retrieval import instances
from gaia.eval.code_retrieval.common import (
    corpus,
    make_sdk,
    parse_only_chunks,
    posix,
    timed,
)
from gaia.eval.code_retrieval.gold import gold_from_patch
from gaia.eval.code_retrieval.lexical import LexicalSearch
from gaia.eval.code_retrieval.metrics import dedupe, mean, score_query
from gaia.eval.code_retrieval.repos import (
    RepoPin,
    checkout_at,
    commit_time,
    read_text,
    slug,
)
from gaia.eval.code_retrieval.suites import FILE_KS, SEARCH_DEPTH, SYMBOL_KS, Suite

Progress = Callable[[str], None]
RETRIEVERS = ("semantic", "lexical")


def checkout_dir(work_root: Path, repo: str) -> Path:
    return work_root / "retrieval" / "checkouts" / slug(repo)


def select_queries(
    suite: Suite, work_root: Path, progress: Progress
) -> tuple[List[instances.Query], List[Dict[str, Any]]]:
    """The suite's queries, and the sampled ones left out (with why)."""
    chosen: List[instances.Query] = []
    excluded: List[Dict[str, Any]] = []
    if suite.verified_ids:
        chosen += instances.verified(suite.verified_ids, work_root)
    sizes: Dict[str, int] = {}

    def fits(q: instances.Query) -> bool:
        if suite.max_repo_chunks is None:
            return True
        if q.repo not in sizes:
            wd = checkout_dir(work_root, q.repo)
            checkout_at(RepoPin(slug(q.repo), q.url, q.base_commit), wd, work_root)
            sizes[q.repo] = parse_only_chunks(wd)
            progress(
                f"  sized {q.repo} at {q.base_commit[:10]}: {sizes[q.repo]} chunks"
            )
        if sizes[q.repo] > suite.max_repo_chunks:
            excluded.append(
                {
                    "id": q.id,
                    "repo": q.repo,
                    "chunks": sizes[q.repo],
                    "why": f"repository over {suite.max_repo_chunks} chunks",
                }
            )
            return False
        return True

    def draw(order: Sequence[str], n: int, load) -> List[instances.Query]:
        # Seeded order over the whole pool; oversize repositories are skipped
        # until the sample is full, so the draw stays reproducible.
        picked: List[instances.Query] = []
        for instance_id in order:
            if len(picked) == n:
                break
            q = load(instance_id)
            if fits(q):
                picked.append(q)
        if len(picked) < n:
            raise instances.InstanceError(
                f"only {len(picked)} of {n} sampled instances fit the "
                f"{suite.max_repo_chunks}-chunk repository limit"
            )
        return sorted(picked, key=lambda q: q.id)

    if suite.verified_sample:
        order = instances.seeded_order(
            instances.swebench.all_instance_ids(), suite.seed
        )
        # One dataset read for the whole split, not one per candidate.
        pool_v = {q.id: q for q in instances.verified(order, work_root)}
        chosen += draw(order, suite.verified_sample, pool_v.__getitem__)
    if suite.rebench_sample:
        pool = instances.rebench_pool(suite.rebench_splits, work_root)
        chosen += draw(
            instances.seeded_order(list(pool), suite.seed),
            suite.rebench_sample,
            pool.__getitem__,
        )
    return chosen, excluded


def _semantic(sdk, text: str, lex: LexicalSearch):
    results, seconds = timed(sdk.search, text, top_k=SEARCH_DEPTH)
    files = dedupe(posix(r.chunk.file_path) for r in results)
    keys = (
        lex.chunk_symbol(
            posix(r.chunk.file_path), r.chunk.symbol_name, r.chunk.start_line
        )
        for r in results
    )
    return files, dedupe(k for k in keys if k is not None), seconds


def _score(files, symbols, gold) -> Dict[str, Any]:
    out: Dict[str, Any] = {"files": score_query(files, gold.files, FILE_KS)}
    if gold.symbols:
        out["symbols"] = score_query(symbols, gold.symbols, SYMBOL_KS)
    return out


def run_query(
    q: instances.Query, work_root: Path, index_root: Path, progress: Progress
) -> Dict[str, Any]:
    wd = checkout_dir(work_root, q.repo)
    checkout_at(RepoPin(slug(q.repo), q.url, q.base_commit), wd, work_root)
    sdk = make_sdk(wd, index_root)
    built, index_s = timed(sdk.index_repository)
    files_in_index = corpus(sdk)
    gold = gold_from_patch(q.patch, lambda p: read_text(wd, p))
    # Score only what the index can hold: a fix that also edits CHANGES.rst
    # should not cap recall for a file type no code search returns.
    unindexed = [f for f in gold.files if f not in files_in_index]
    gold.files = [f for f in gold.files if f in files_in_index]
    gold.symbols = [s for s in gold.symbols if s[0] in files_in_index]
    record: Dict[str, Any] = {
        "id": q.id,
        "source": q.source,
        "repo": q.repo,
        "base_commit": q.base_commit,
        "created_at": q.created_at,
        "query_chars": len(q.text),
        "gold": {
            "files": gold.files,
            "symbols": [f"{p}::{n}" for p, n in gold.symbols],
            "created_files": gold.created,
            "not_in_index": unindexed,
        },
        "index": {
            "seconds": round(index_s, 2),
            "files_reparsed": built.files_indexed,
            "chunks": built.chunks_created,
            "chunks_dropped": built.chunks_dropped,
        },
    }
    if not gold.files:
        record["skipped"] = (
            "no file the fix edits is in the index (it only creates files, or "
            "edits file types the index does not hold)"
        )
        return record

    lex = LexicalSearch(wd, files_in_index)
    files, symbols, seconds = _semantic(sdk, q.text, lex)
    record["semantic"] = {
        "seconds": round(seconds, 3),
        "top_files": files[:10],
        "top_symbols": [f"{p}::{n}" for p, n in symbols[:10]],
        **_score(files, symbols, gold),
    }
    (lfiles, lsymbols, used), lseconds = timed(lex.rank, q.text)
    record["lexical"] = {
        "seconds": round(lseconds, 3),
        "identifiers": used,
        "top_files": lfiles[:10],
        "top_symbols": [f"{p}::{n}" for p, n in lsymbols[:10]],
        **_score(lfiles, lsymbols, gold),
    }
    progress(
        f"  {q.id}: semantic R@10={record['semantic']['files']['recall@10']:.2f} "
        f"lexical R@10={record['lexical']['files']['recall@10']:.2f} "
        f"(index {index_s:.0f}s, {built.files_indexed} files re-parsed)"
    )
    return record


def summarize(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Mean metrics per retriever, plus head-to-head on file reciprocal rank."""
    scored = [r for r in records if "semantic" in r]
    out: Dict[str, Any] = {"queries": len(scored)}
    with_symbols = [r for r in scored if "symbols" in r["semantic"]]
    out["queries_with_symbols"] = len(with_symbols)
    for name in RETRIEVERS:
        file_rows = [r[name]["files"] for r in scored]
        sym_rows = [r[name]["symbols"] for r in with_symbols]
        stats = {f"file_recall@{k}": mean(file_rows, f"recall@{k}") for k in FILE_KS}
        stats["file_mrr"] = mean(file_rows, "rr")
        for k in SYMBOL_KS:
            stats[f"symbol_recall@{k}"] = mean(sym_rows, f"recall@{k}")
        stats["symbol_mrr"] = mean(sym_rows, "rr")
        out[name] = {
            k: None if math.isnan(v) else round(v, 4) for k, v in stats.items()
        }
    wins = sum(
        1 for r in scored if r["semantic"]["files"]["rr"] > r["lexical"]["files"]["rr"]
    )
    losses = sum(
        1 for r in scored if r["semantic"]["files"]["rr"] < r["lexical"]["files"]["rr"]
    )
    out["semantic_vs_lexical_file_rr"] = {
        "wins": wins,
        "losses": losses,
        "ties": len(scored) - wins - losses,
    }
    return out


def run(
    suite: Suite, work_root: Path, index_root: Path, progress: Progress
) -> Optional[Dict[str, Any]]:
    queries, excluded = select_queries(suite, work_root, progress)
    if not queries:
        return None
    progress(
        f"[quality] {len(queries)} queries over {len({q.repo for q in queries})} repos"
    )
    # Per repository, oldest base first: each re-index then only re-embeds what
    # changed since the previous base.
    order: Dict[str, int] = {}
    for q in queries:
        wd = checkout_dir(work_root, q.repo)
        checkout_at(RepoPin(slug(q.repo), q.url, q.base_commit), wd, work_root)
        order[q.id] = commit_time(wd, q.base_commit)
    queries = sorted(queries, key=lambda q: (q.repo, order[q.id], q.id))
    records = [run_query(q, work_root, index_root, progress) for q in queries]
    sources = sorted({r["source"] for r in records})
    return {
        "queries": records,
        "excluded": excluded,
        "summary": {
            "all": summarize(records),
            **{s: summarize([r for r in records if r["source"] == s]) for s in sources},
        },
    }
