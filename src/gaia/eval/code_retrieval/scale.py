# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Scale and freshness: what indexing costs, and whether re-indexing stays right.

:func:`measure` indexes one repository from scratch with the shipped defaults
and records wall time, this process's peak resident memory (embedding itself
runs in the Lemonade server, which is not counted), on-disk index size, and
search latency cold (fresh process state: index load plus first query) and
warm (p50/p95 over :data:`~gaia.eval.code_retrieval.suites.LATENCY_QUERIES`).

:func:`replay` moves a working tree through real commits, re-indexing after
each, then builds a fresh index at the last commit and checks the two hold the
same chunks with the same vectors. An incremental index that drifted from a
fresh one would keep serving stale or missing code with no error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List

from gaia.eval.code_retrieval.common import (
    RssSampler,
    cache_path,
    dir_bytes,
    make_sdk,
    posix,
    read_metadata,
    timed,
    top_keys,
)
from gaia.eval.code_retrieval.metrics import percentile
from gaia.eval.code_retrieval.repos import (
    RepoPin,
    changed_files,
    checkout_at,
    commit_subject,
    first_parent_chain,
    git,
)
from gaia.eval.code_retrieval.suites import LATENCY_QUERIES, Incremental

Progress = Callable[[str], None]
#: Two vectors for the same chunk text closer than this are the same embedding.
SAME_VECTOR_COSINE = 0.999


def _lines(repo: Path, files: List[str]) -> int:
    total = 0
    for rel in files:
        with open(repo / rel, "rb") as fh:
            total += sum(1 for _ in fh)
    return total


def measure(
    pin: RepoPin, work_root: Path, index_root: Path, progress: Progress
) -> Dict[str, Any]:
    wd = work_root / "retrieval" / "checkouts" / f"{pin.name}-scale"
    checkout_at(pin, wd, work_root)
    sdk = make_sdk(wd, index_root / "scale" / pin.name)
    sdk.clear_index()
    progress(f"  {pin.name}: indexing from scratch at {pin.commit[:10]} ...")
    with RssSampler() as rss:
        built, seconds = timed(sdk.index_repository)
    meta = read_metadata(sdk)
    files = list(meta["file_hashes"])
    # pylint: disable=protected-access
    discovered, truncated = sdk._discover_files()
    cold_sdk = make_sdk(wd, index_root / "scale" / pin.name)
    _, cold = timed(cold_sdk.search, LATENCY_QUERIES[0], top_k=10)
    warm = [timed(cold_sdk.search, q, top_k=10)[1] for q in LATENCY_QUERIES]
    record = {
        "repo": pin.name,
        "url": pin.url,
        "commit": pin.commit,
        "files": len(files),
        "lines": _lines(wd, files),
        "chunks": built.chunks_created,
        "chunks_dropped": built.chunks_dropped,
        "files_discovered": len(discovered),
        # A scan limit (max_files / max_walk_entries) cut discovery short.
        "discovery_truncated": truncated,
        "index_seconds": round(seconds, 1),
        "chunks_per_second": round(built.chunks_created / seconds, 2),
        "peak_rss_mb": round(rss.peak_bytes / 2**20, 1),
        "rss_growth_mb": round((rss.peak_bytes - rss.start_bytes) / 2**20, 1),
        "index_bytes": dir_bytes(cache_path(sdk)),
        "cold_query_ms": round(cold * 1000, 1),
        "query_p50_ms": round(percentile(warm, 50) * 1000, 1),
        "query_p95_ms": round(percentile(warm, 95) * 1000, 1),
    }
    progress(
        f"  {pin.name}: {record['chunks']} chunks in {record['index_seconds']}s, "
        f"peak RSS {record['peak_rss_mb']} MB, index {record['index_bytes'] / 2**20:.0f} MB, "
        f"query p50 {record['query_p50_ms']} ms / p95 {record['query_p95_ms']} ms"
    )
    return record


def _vectors(sdk) -> Dict[tuple, List[Any]]:
    """``(path, start, end, content) -> [vector, ...]`` for every chunk in the index."""
    import faiss

    meta = read_metadata(sdk)
    index = faiss.read_index(str(cache_path(sdk) / "index.faiss"))
    if index.ntotal != len(meta["chunks"]):
        raise RuntimeError(
            f"index at {cache_path(sdk)} has {index.ntotal} vectors for "
            f"{len(meta['chunks'])} chunks"
        )
    vecs = index.reconstruct_n(0, index.ntotal)
    out: Dict[tuple, List[Any]] = {}
    for chunk, vec in zip(meta["chunks"], vecs):
        key = (
            posix(chunk["file_path"]),
            chunk["start_line"],
            chunk["end_line"],
            chunk["content"],
        )
        out.setdefault(key, []).append(vec)
    return out


def _compare(inc_sdk, fresh_sdk) -> Dict[str, Any]:
    import numpy as np

    inc, fresh = _vectors(inc_sdk), _vectors(fresh_sdk)
    only_inc = sum(len(v) for k, v in inc.items() if k not in fresh)
    only_fresh = sum(len(v) for k, v in fresh.items() if k not in inc)
    count_mismatch = sum(
        1 for k in inc.keys() & fresh.keys() if len(inc[k]) != len(fresh[k])
    )
    cosines = []
    for key in inc.keys() & fresh.keys():
        for a, b in zip(inc[key], fresh[key]):
            cosines.append(
                float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
            )
    return {
        "chunks_incremental": sum(len(v) for v in inc.values()),
        "chunks_fresh": sum(len(v) for v in fresh.values()),
        "only_in_incremental": only_inc,
        "only_in_fresh": only_fresh,
        "duplicate_count_mismatches": count_mismatch,
        "min_vector_cosine": round(min(cosines), 6) if cosines else None,
    }


def replay(
    spec: Incremental, work_root: Path, index_root: Path, progress: Progress
) -> Dict[str, Any]:
    pin = spec.repo
    wd = work_root / "retrieval" / "checkouts" / f"{pin.name}-replay"
    checkout_at(pin, wd, work_root)
    chain = first_parent_chain(wd, pin.commit, spec.commits)

    def move_to(sha: str) -> None:
        git(["checkout", "--quiet", "--force", "--detach", sha], cwd=wd)
        git(["clean", "-ffdxq"], cwd=wd)

    inc = make_sdk(wd, index_root / "replay-incremental")
    inc.clear_index()
    move_to(chain[0])
    progress(
        f"[incremental] {pin.name}: full index at {chain[0][:10]}, then {len(chain) - 1} commits"
    )
    first, first_s = timed(inc.index_repository)
    steps = []
    for prev, sha in zip(chain, chain[1:]):
        move_to(sha)
        changed = changed_files(wd, prev, sha)
        built, seconds = timed(inc.index_repository)
        steps.append(
            {
                "commit": sha,
                "subject": commit_subject(wd, sha),
                "files_changed": len(changed),
                "files_reparsed": built.files_indexed,
                "chunks": built.chunks_created,
                "seconds": round(seconds, 2),
            }
        )
    fresh = make_sdk(wd, index_root / "replay-fresh")
    fresh.clear_index()
    _, fresh_s = timed(fresh.index_repository)
    comparison = _compare(inc, fresh)
    probes = [s["subject"] for s in steps if s["subject"]]
    same = overlap = 0.0
    for text in probes:
        a = top_keys(inc.search(text, top_k=10))
        b = top_keys(fresh.search(text, top_k=10))
        same += a == b
        overlap += len({tuple(x) for x in a} & {tuple(x) for x in b}) / max(len(b), 1)
    correct = (
        comparison["only_in_incremental"] == 0
        and comparison["only_in_fresh"] == 0
        and comparison["duplicate_count_mismatches"] == 0
        and (comparison["min_vector_cosine"] or 0) >= SAME_VECTOR_COSINE
    )
    step_secs = [s["seconds"] for s in steps]
    result = {
        "repo": pin.name,
        "url": pin.url,
        "from_commit": chain[0],
        "to_commit": chain[-1],
        "commits_replayed": len(steps),
        "first_index_seconds": round(first_s, 1),
        "first_index_chunks": first.chunks_created,
        "fresh_index_seconds": round(fresh_s, 1),
        "step_seconds_p50": round(percentile(step_secs, 50), 2),
        "step_seconds_max": round(max(step_secs), 2),
        "steps": steps,
        **comparison,
        "probe_queries": len(probes),
        "probe_identical_top10": round(same / len(probes), 4) if probes else None,
        "probe_mean_overlap@10": round(overlap / len(probes), 4) if probes else None,
        "correct": correct,
    }
    progress(
        f"[incremental] {'CORRECT' if correct else 'DRIFTED'}: "
        f"{comparison['only_in_incremental']} stale / {comparison['only_in_fresh']} missing "
        f"chunks, min cosine {comparison['min_vector_cosine']}; step p50 "
        f"{result['step_seconds_p50']}s vs fresh {result['fresh_index_seconds']}s"
    )
    return result
