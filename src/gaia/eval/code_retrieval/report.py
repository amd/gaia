# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The run's markdown report, and the comparison a gate decides on."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gaia.eval.code_retrieval.suites import FILE_KS, SYMBOL_KS

#: The quality numbers a gate compares, per set, for the semantic retriever.
GATED_METRICS = (
    *(f"file_recall@{k}" for k in FILE_KS),
    "file_mrr",
    *(f"symbol_recall@{k}" for k in SYMBOL_KS),
)


def _fmt(value: Optional[float]) -> str:
    return "–" if value is None else f"{value:.3f}"


def _rank(scores: Dict[str, Any]) -> str:
    rr = scores["files"]["rr"]
    return "miss" if rr == 0 else str(round(1 / rr))


def _quality(q: Dict[str, Any]) -> List[str]:
    out = ["## Retrieval quality", ""]
    out.append(
        "Query = the issue text; relevant = files and functions/classes the merged "
        "fix edits. Recall@k is the share of them in the top k; MRR is the mean of "
        "1/rank of the first relevant file. `lexical` is `git grep` for the issue's "
        "identifiers, ranked by rarity: the baseline semantic search must beat."
    )
    out += [
        "",
        "| Set | Queries | Retriever | File R@1 | File R@5 | File R@10 | File MRR "
        "| Symbol R@5 | Symbol R@10 | Symbol MRR |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, s in q["summary"].items():
        for retriever in ("semantic", "lexical"):
            m = s[retriever]
            out.append(
                f"| {name} | {s['queries']} ({s['queries_with_symbols']} w/ symbols) | "
                f"{retriever} | "
                + " | ".join(
                    _fmt(m[k])
                    for k in (
                        *(f"file_recall@{k}" for k in FILE_KS),
                        "file_mrr",
                        *(f"symbol_recall@{k}" for k in SYMBOL_KS),
                        "symbol_mrr",
                    )
                )
                + " |"
            )
    h2h = q["summary"]["all"]["semantic_vs_lexical_file_rr"]
    out += [
        "",
        f"Semantic ranked the first relevant file higher on {h2h['wins']} queries, "
        f"lower on {h2h['losses']}, the same on {h2h['ties']}.",
        "",
        "<details><summary>Per query</summary>",
        "",
        "| Query | Repo | Gold files | Semantic rank | Lexical rank | Index s (re-parsed files) |",
        "|---|---|---|---|---|---|",
    ]
    for r in q["queries"]:
        if "semantic" not in r:
            out.append(
                f"| `{r['id']}` | {r['repo']} | – | skipped: {r['skipped']} | | |"
            )
            continue
        out.append(
            f"| `{r['id']}` | {r['repo']} | {len(r['gold']['files'])} | "
            f"{_rank(r['semantic'])} | {_rank(r['lexical'])} | "
            f"{r['index']['seconds']} ({r['index']['files_reparsed']}) |"
        )
    out += ["", "</details>"]
    if q["excluded"]:
        repos = sorted({e["repo"] for e in q["excluded"]})
        out += [
            "",
            f"Left out of the sample for size: {len(q['excluded'])} draws from "
            f"{', '.join(repos)}.",
        ]
    return out


def _incremental(inc: Dict[str, Any]) -> List[str]:
    verdict = "correct" if inc["correct"] else "**DRIFTED from a fresh index**"
    return [
        "## Incremental re-index",
        "",
        f"{inc['repo']}: {inc['commits_replayed']} real commits replayed "
        f"({inc['from_commit'][:10]}..{inc['to_commit'][:10]}), re-indexing after each. "
        f"Result vs a fresh index of the last commit: {verdict}.",
        "",
        "| Full index | Step p50 | Step max | Stale chunks | Missing chunks | Min vector cosine "
        "| Probe top-10 identical |",
        "|---|---|---|---|---|---|---|",
        f"| {inc['first_index_seconds']} s | {inc['step_seconds_p50']} s | "
        f"{inc['step_seconds_max']} s | {inc['only_in_incremental']} | {inc['only_in_fresh']} | "
        f"{inc['min_vector_cosine']} | {_fmt(inc['probe_identical_top10'])} |",
    ]


def _robustness(rob: Dict[str, Any]) -> List[str]:
    out = [
        "## Robustness",
        "",
        f"{rob['passed']} passed, {rob['failed']} failed, {rob['not_run']} not run. "
        "A damage scenario fails when the index answers without error but with "
        "no or wrong results.",
        "",
        "| Scenario | Damage | Search | Re-index | Passed | Recovers |",
        "|---|---|---|---|---|---|",
    ]
    for s in rob["scenarios"]:
        out.append(
            f"| {s['name']} | {s['damage']} | {s['search']['outcome']} | "
            f"{s['reindex']['outcome']} | {'yes' if s['passed'] else '**no**'} | "
            f"{'yes' if s['recovers'] else 'no'} |"
        )
    out += ["", "| Hostile tree | Hazard | Result | Detail |", "|---|---|---|---|"]
    for t in rob["tree"]:
        status = t["status"] if t["status"] != "fail" else "**fail**"
        out.append(f"| {t['name']} | {t['hazard']} | {status} | {t['detail']} |")
    return out


def _scale(rows: List[Dict[str, Any]]) -> List[str]:
    out = [
        "## Scale",
        "",
        "Fresh index with the shipped defaults. Peak RSS is this process; the "
        "embedder runs inside Lemonade and is not counted.",
        "",
        "| Repo | Files | Lines | Chunks | Index time | Chunks/s | Peak RSS | Index size "
        "| Cold query | Query p50 | Query p95 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        capped = " (scan limit hit)" if r["discovery_truncated"] else ""
        out.append(
            f"| {r['repo']} | {r['files']:,}{capped} | {r['lines']:,} | {r['chunks']:,} | "
            f"{r['index_seconds'] / 60:.1f} min | {r['chunks_per_second']} | "
            f"{r['peak_rss_mb']:,.0f} MB | {r['index_bytes'] / 2**20:,.0f} MB | "
            f"{r['cold_query_ms']:,.0f} ms | {r['query_p50_ms']:,.0f} ms | "
            f"{r['query_p95_ms']:,.0f} ms |"
        )
    return out


def markdown(result: Dict[str, Any]) -> str:
    env = result["environment"]
    took = (
        f"{result['duration_s'] / 60:.0f} min."
        if "duration_s" in result
        else "Still running."
    )
    dirty = " (dirty)" if env["gaia_dirty"] else ""
    lines = [
        f"# Code retrieval benchmark: `{result['suite']}`",
        "",
        f"{env['cpu']}, {env['ram_gb']} GB, {env['os']}. Embedder "
        f"`{env['embedding_model']}` on Lemonade {env['lemonade_version']}. GAIA "
        f"{(env['gaia_commit'] or 'unknown')[:10]}{dirty}. {took}",
        "",
    ]
    if result.get("error"):
        lines += [f"**The run stopped early:** {result['error']}", ""]
    if result.get("quality"):
        lines += _quality(result["quality"]) + [""]
    if result.get("incremental"):
        lines += _incremental(result["incremental"]) + [""]
    if result.get("robustness"):
        lines += _robustness(result["robustness"]) + [""]
    if result.get("scale"):
        lines += _scale(result["scale"]) + [""]
    if result.get("comparison"):
        lines += comparison_markdown(result["comparison"]) + [""]
    return "\n".join(lines)


def compare(
    baseline: Dict[str, Any], current: Dict[str, Any], max_drop: float
) -> Dict[str, Any]:
    """Regressions of *current* against *baseline*; a gate fails on any."""
    regressions: List[str] = []
    deltas: List[Dict[str, Any]] = []
    if baseline.get("suite") != current.get("suite"):
        raise ValueError(
            f"baseline is suite {baseline.get('suite')!r}, this run is "
            f"{current.get('suite')!r}; compare runs of the same suite"
        )
    bq, cq = baseline.get("quality"), current.get("quality")
    if bq and cq:
        b_ids = sorted(r["id"] for r in bq["queries"])
        c_ids = sorted(r["id"] for r in cq["queries"])
        if b_ids != c_ids:
            raise ValueError(
                "the baseline scored different queries than this run "
                f"({len(b_ids)} vs {len(c_ids)}); its numbers are not comparable. "
                "Re-capture the baseline from a run of this suite."
            )
        for name, b in bq["summary"].items():
            c = cq["summary"][name]
            for metric in GATED_METRICS:
                bv, cv = b["semantic"][metric], c["semantic"][metric]
                if bv is None or cv is None:
                    continue
                deltas.append(
                    {"set": name, "metric": metric, "baseline": bv, "current": cv}
                )
                if cv < bv - max_drop:
                    regressions.append(
                        f"{name} semantic {metric} fell {bv:.3f} -> {cv:.3f} "
                        f"(more than {max_drop})"
                    )
    elif bq or cq:
        regressions.append("quality ran in only one of the two runs")
    ci = current.get("incremental")
    if ci is not None and not ci["correct"]:
        regressions.append("incremental re-index drifted from a fresh index")
    br, cr = baseline.get("robustness"), current.get("robustness")
    if br and cr:
        before = {s["name"]: s for s in br["scenarios"]}
        for s in cr["scenarios"]:
            b = before.get(s["name"])
            if b is None and not s["passed"]:
                regressions.append(f"robustness {s['name']}: new scenario fails")
            elif b is not None:
                for flag in ("passed", "recovers"):
                    if b[flag] and not s[flag]:
                        regressions.append(f"robustness {s['name']}: no longer {flag}")
        tree_before = {t["name"]: t["status"] for t in br["tree"]}
        for t in cr["tree"]:
            if tree_before.get(t["name"]) == "pass" and t["status"] != "pass":
                regressions.append(
                    f"hostile tree {t['name']}: {t['status']} ({t['detail']})"
                )
    bs = {r["repo"]: r for r in baseline.get("scale") or []}
    scale = []
    for r in current.get("scale") or []:
        b = bs.get(r["repo"])
        if b:
            scale.append(
                {
                    "repo": r["repo"],
                    "index_seconds": [b["index_seconds"], r["index_seconds"]],
                    "peak_rss_mb": [b["peak_rss_mb"], r["peak_rss_mb"]],
                    "query_p95_ms": [b["query_p95_ms"], r["query_p95_ms"]],
                }
            )
    return {
        "max_drop": max_drop,
        "regressions": regressions,
        "quality": deltas,
        "scale": scale,
    }


def comparison_markdown(cmp: Dict[str, Any]) -> List[str]:
    out = ["## Against the baseline", ""]
    if cmp["regressions"]:
        out += [f"**{len(cmp['regressions'])} regression(s):**", ""]
        out += [f"- {r}" for r in cmp["regressions"]]
    else:
        out.append(f"No regressions (quality tolerance {cmp['max_drop']}).")
    if cmp["quality"]:
        out += ["", "| Set | Metric | Baseline | This run |", "|---|---|---|---|"]
        out += [
            f"| {d['set']} | {d['metric']} | {d['baseline']:.3f} | {d['current']:.3f} |"
            for d in cmp["quality"]
        ]
    if cmp["scale"]:
        out += [
            "",
            "Scale is reported, not gated:",
            "",
            "| Repo | Index s | Peak RSS MB | Query p95 ms |",
            "|---|---|---|---|",
        ]
        out += [
            f"| {s['repo']} | {s['index_seconds'][0]} → {s['index_seconds'][1]} | "
            f"{s['peak_rss_mb'][0]} → {s['peak_rss_mb'][1]} | "
            f"{s['query_p95_ms'][0]} → {s['query_p95_ms'][1]} |"
            for s in cmp["scale"]
        ]
    return out
