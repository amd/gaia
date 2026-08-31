# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Benchmark the proactive skill retriever against a labeled query set.

Run it against the real installed corpus (whatever ``gaia skill list`` shows)::

    python util/skill_retrieval_bench.py

or against the checked-in starter pack alone, so the numbers are reproducible on
a machine with a different ``~/.gaia/skills``::

    python util/skill_retrieval_bench.py --corpus hub/skills

Reports precision / recall / accuracy over the three outcomes the retriever can
produce, plus per-query scoring latency. ``--sweep`` grid-searches the three
constants that decide auto-load (``UNKNOWN_WEIGHT``, ``MIN_SCORE``, ``MARGIN``)
and prints the frontier, which is how they were picked rather than guessed.

``--include-claude`` indexes ``.claude/skills`` as well, reproducing the false
matches that excluding that root prevents.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from gaia.agents.base import skill_retriever as sr  # noqa: E402
from gaia.skills.manager import SkillManager  # noqa: E402

#: (query, expected skill name or None). ``None`` means "no skill should load".
#:
#: Positives are written the way a user actually asks — never naming the skill.
#: Negatives are the important half: a retriever that loads something for every
#: turn burns prompt budget and drags irrelevant instructions into the answer.
QUERIES: List[Tuple[str, Optional[str]]] = [
    # ── github-triage ────────────────────────────────────────────────────
    ("what's been going on in my github inbox the past few days?", "github-triage"),
    ("triage my github issues", "github-triage"),
    (
        "can you go through the amd/gaia backlog and tell me what to fix first?",
        "github-triage",
    ),
    ("any new github notifications I should look at?", "github-triage"),
    ("review the open issues on that repo and group the duplicates", "github-triage"),
    # The continuity follow-up from .claude/skills/testing-the-gaia-agent — a
    # bare number with no repo named. It is a positive, not a negative: the
    # skill that just triaged the backlog is exactly what should print an issue.
    ("cool, can you print issue 2975?", "github-triage"),
    # ── document-brief ───────────────────────────────────────────────────
    ("what does this contract say about termination?", "document-brief"),
    ("index the folder of specs and answer questions from it", "document-brief"),
    ("summarise this report and quote the source", "document-brief"),
    # ── data-explore ─────────────────────────────────────────────────────
    ("load this csv into a table and tell me the totals by region", "data-explore"),
    (
        "I have a spreadsheet export, can you query it properly instead of eyeballing",
        "data-explore",
    ),
    # ── research-report ──────────────────────────────────────────────────
    ("write me a cited report on the state of local LLM inference", "research-report"),
    ("do a competitive landscape scan of NPU vendors", "research-report"),
    # ── rss-digest ───────────────────────────────────────────────────────
    ("here's an atom feed url, what has it published lately?", "rss-digest"),
    # ── price-watch ──────────────────────────────────────────────────────
    ("watch this product page and tell me if the price drops", "price-watch"),
    # ── source-watch ─────────────────────────────────────────────────────
    (
        "monitor this url and only tell me about things you haven't reported",
        "source-watch",
    ),
    # ── recommendations ──────────────────────────────────────────────────
    ("what film should I watch tonight?", "recommendations"),
    # ── check-in ─────────────────────────────────────────────────────────
    ("good morning, what did I say I'd do yesterday?", "check-in"),
    # ── daily-brief ──────────────────────────────────────────────────────
    ("give me my morning briefing", "daily-brief"),
    # ── coding ───────────────────────────────────────────────────────────
    ("fix the failing test in the parser module", "coding"),
    ("refactor this function so it stops duplicating the retry logic", "coding"),
    # ── user-installed document skills (present only in ~/.gaia/skills) ───
    ("pull the tables out of this pdf for me", "pdf"),
    ("turn these numbers into a spreadsheet with a chart", "xlsx"),
    ("build me a slide deck from this outline", "pptx"),
    # ── negatives: nothing should load ───────────────────────────────────
    ("what is 17 times 23?", None),
    ("remember that my favourite colour is teal", None),
    ("hi", None),
    ("thanks, that's great", None),
    ("what's the capital of France?", None),
    ("explain the difference between a mutex and a semaphore", None),
    ("write me a haiku about winter", None),
    ("yes, go ahead", None),
    ("what time is it?", None),
    # Regression guards for the .claude/skills exclusion: with the developer
    # marketplace indexed, "contract" hits gaia-technical-presentation (it
    # documents "request/response contracts") and "function" hits
    # gaia-build-agent. Both are Claude Code skills for working ON this repo,
    # written for a different host — never an answer to a user's question.
    ("what is the contract for this API endpoint?", None),
    ("what does the function signature mean here?", None),
]


def load_corpus(
    corpus: Optional[str], *, include_claude: bool = False
) -> Dict[str, str]:
    """``{name: description}`` from a skills directory, or the live discovery set.

    ``include_claude`` defaults to False to mirror what the agent actually
    indexes — see ``SkillDiscovery`` in
    :mod:`gaia.agents.base.skill_discovery`. Pass ``--include-claude`` to
    reproduce the false matches that exclusion exists to prevent.
    """
    if corpus:
        root = Path(corpus)
        if not root.is_absolute():
            root = REPO_ROOT / root
        manager = SkillManager(
            agent_skill_dirs=[root],
            user_skills_root=root / "__none__",
            include_claude_roots=False,
        )
    else:
        manager = SkillManager(include_claude_roots=include_claude)
    return {
        name: (skill.description or "")
        for name, skill in manager.discover(force=True).items()
    }


def evaluate(
    retriever: sr.SkillRetriever,
    queries: List[Tuple[str, Optional[str]]],
    *,
    verbose: bool = False,
) -> dict:
    """Score the retriever over *queries*, returning the metric bundle."""
    known = set(retriever.names)
    rows = []
    latencies: List[float] = []
    for query, expected in queries:
        # A positive whose skill is not in this corpus is not a miss — skip it,
        # or a partial corpus would fake a recall failure.
        if expected is not None and expected not in known:
            continue
        start = time.perf_counter()
        decision = retriever.decide(query)
        latencies.append((time.perf_counter() - start) * 1000.0)
        rows.append((query, expected, decision))
        if verbose:
            top = decision.ranked[:3]
            print(
                f"  {decision.outcome:9} {str(decision.load or decision.shortlist or '-'):40}"
                f" want={str(expected):18} "
                + " ".join(f"{c.name}:{c.score:.2f}" for c in top)
            )

    positives = [r for r in rows if r[1] is not None]
    negatives = [r for r in rows if r[1] is None]

    auto_correct = sum(1 for _, exp, d in positives if d.load == exp)
    auto_wrong = sum(
        1 for _, exp, d in positives if d.load is not None and d.load != exp
    )
    shortlist_hit = sum(
        1 for _, exp, d in positives if d.load is None and exp in d.shortlist
    )
    missed = len(positives) - auto_correct - auto_wrong - shortlist_hit

    false_loads = [(q, d.load) for q, _, d in negatives if d.load is not None]
    quiet_negatives = sum(1 for _, _, d in negatives if d.outcome == "none")

    auto_attempts = auto_correct + auto_wrong + len(false_loads)
    return {
        "corpus_size": retriever.size,
        "positives": len(positives),
        "negatives": len(negatives),
        "auto_correct": auto_correct,
        "auto_wrong": auto_wrong,
        "shortlist_recovered": shortlist_hit,
        "missed_entirely": missed,
        "false_loads": false_loads,
        "quiet_negatives": quiet_negatives,
        "auto_precision": auto_correct / auto_attempts if auto_attempts else 1.0,
        "auto_recall": auto_correct / len(positives) if positives else 0.0,
        "recall_incl_shortlist": (
            (auto_correct + shortlist_hit) / len(positives) if positives else 0.0
        ),
        "negative_specificity": (
            quiet_negatives / len(negatives) if negatives else 1.0
        ),
        "latency_ms_mean": statistics.mean(latencies) if latencies else 0.0,
        "latency_ms_p95": (
            sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 3 else 0.0
        ),
    }


def sweep(retriever: sr.SkillRetriever, queries) -> None:
    """Grid-search the three constants that decide auto-load.

    Prints one row per point so the chosen values are visibly a maximum rather
    than a preference. ``score`` ranks the frontier: auto-recall matters, but a
    false load costs far more than a miss (it drags an irrelevant skill's
    instructions into an unrelated answer), so it is weighted accordingly.
    """
    saved = (sr.UNKNOWN_WEIGHT, sr.MIN_SCORE, sr.MARGIN)
    header = (
        f"\n{'UNKNOWN_W':>10} {'MIN_SCORE':>10} {'MARGIN':>7} "
        f"{'prec':>6} {'recall':>7} {'+short':>7} {'false':>6} {'score':>7}"
    )
    print(header)
    best = None
    for unknown_weight in (0.0, 0.3, 0.6, 0.9):
        for min_score in (0.20, 0.25, 0.30, 0.35, 0.40, 0.50):
            for margin in (1.0, 1.3, 1.6, 2.0, 2.5):
                sr.UNKNOWN_WEIGHT = unknown_weight
                sr.MIN_SCORE = min_score
                sr.MARGIN = margin
                m = evaluate(retriever, queries)
                rank = m["auto_recall"] - 2.0 * (
                    len(m["false_loads"]) / max(m["negatives"], 1)
                )
                print(
                    f"{unknown_weight:>10.2f} {min_score:>10.2f} {margin:>7.1f} "
                    f"{m['auto_precision']:>6.2f} {m['auto_recall']:>7.2f} "
                    f"{m['recall_incl_shortlist']:>7.2f} "
                    f"{len(m['false_loads']):>6} {rank:>7.3f}"
                )
                point = (unknown_weight, min_score, margin)
                if best is None or rank > best[0]:
                    best = (rank, point)
    sr.UNKNOWN_WEIGHT, sr.MIN_SCORE, sr.MARGIN = saved
    print(f"\nbest: UNKNOWN_WEIGHT/MIN_SCORE/MARGIN = {best[1]} (score {best[0]:.3f})")
    print(f"shipping: {saved}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus", help="Skills directory to index (default: live discovery)"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print every query's decision"
    )
    parser.add_argument(
        "--sweep", action="store_true", help="Grid-search the thresholds"
    )
    parser.add_argument("--json", action="store_true", help="Emit metrics as JSON")
    parser.add_argument(
        "--include-claude",
        action="store_true",
        help="Index .claude/skills too — reproduces the matches its exclusion prevents",
    )
    args = parser.parse_args()

    descriptions = load_corpus(args.corpus, include_claude=args.include_claude)
    retriever = sr.SkillRetriever()
    retriever.index_texts(descriptions)
    print(f"Indexed {retriever.size} skills: {', '.join(retriever.names)}\n")

    metrics = evaluate(retriever, QUERIES, verbose=args.verbose)
    if args.json:
        print(json.dumps(metrics, indent=2))
    else:
        for key, value in metrics.items():
            if key == "false_loads":
                if value:
                    print(f"{key:24} {len(value)}")
                    for query, name in value:
                        print(f"{'':26} {name} <- {query!r}")
                else:
                    print(f"{key:24} 0")
            elif isinstance(value, float):
                print(f"{key:24} {value:.3f}")
            else:
                print(f"{key:24} {value}")

    if args.sweep:
        sweep(retriever, QUERIES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
