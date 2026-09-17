# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Split the evaluation pool into one independently runnable set per use case.

The existing dataset is a single undifferentiated pool carrying a label from an
older taxonomy — one assigned per *session*, so a session that began as a code
review and ended as a release was tagged whichever came first. A single mixed
pool also gives a single mixed score, which cannot answer the question anyone
actually asks: *is the agent good at fixing bugs?*

This module re-labels every record with the per-task taxonomy in
:mod:`gaia.factory.harvest.use_cases` and writes one directory per use case, each
runnable on its own. Three things make the split more than a `GROUP BY`:

* **Episode-level classification.** A record is one decision point inside an
  episode; every record in an episode shares its instruction, so the label is
  decided once per episode from the instruction *and the behaviour of all its
  records*, then stamped. Classifying each record alone would let two moments of
  the same task disagree.
* **Contamination is removed, not relabelled.** 134 records across 23 episodes
  carry an instruction that no human wrote — orchestrator boilerplate injected
  into the same channel. They were being scored as though someone had asked.
* **Each set carries its own success criterion.** A use case that cannot state
  what a passing answer looks like should not be a use case, so the criterion
  ships beside the records rather than living in a reviewer's head.

The pool/oracle partition is preserved inside every use case, so held-out
discipline survives the split.

Nothing here writes into the repository: the corpus is private and the output
directory is passed in.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from gaia.factory.harvest import use_cases as uc
from gaia.factory.harvest.tasks import Task
from gaia.factory.harvest.tasks import classify as classify_activity
from gaia.factory.harvest.tasks import origin_of

#: Below this, a set is too small to produce a meaningful score on its own. Such
#: use cases are still written out — dropping them would hide real work — but the
#: manifest marks them so a scorecard can refuse to report a headline number.
MIN_RUNNABLE_RECORDS = 25


def load_records(dataset: Path) -> List[dict]:
    """Every record from both partitions, tagged with the partition it came from."""
    out: List[dict] = []
    for part in ("pool", "oracle"):
        path = dataset / part / "records.jsonl"
        if not path.exists():
            raise SystemExit(
                f"no records at {path}. Point --dataset at a built dataset "
                "directory containing pool/records.jsonl and oracle/records.jsonl."
            )
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    rec.setdefault("partition", part)
                    out.append(rec)
    return out


def _episode_task(episode_id: str, records: Sequence[dict]) -> Task:
    """Rebuild a :class:`Task` for one episode so the taxonomy can label it.

    The behaviour override needs the tool families the episode actually used —
    an instruction saying "research this" that only edited files is not research
    — so every record's calls are folded in, not just the first.
    """
    steps: List[dict] = []
    for rec in records:
        for call in (rec.get("action") or {}).get("calls") or []:
            steps.append(
                {
                    "tool": call.get("tool", "unknown"),
                    "family": call.get("family", "other"),
                    "ok": rec.get("outcome") != "error",
                }
            )
    instruction = records[0].get("episode_instruction") or ""
    task = Task(
        session_id=records[0].get("session_id", ""),
        prompt_index=0,
        ask=" ".join(str(instruction).split()),
        steps=steps,
        origin=origin_of(str(instruction)),
    )
    task.activity, task.domain = classify_activity(task)
    task.use_case = uc.classify(task)
    return task


def relabel(records: Sequence[dict]) -> Dict[str, object]:
    """Re-label every record by episode, dropping boilerplate-driven ones.

    Returns the surviving records plus the counts a manifest needs to be honest
    about what was removed.
    """
    by_episode: Dict[str, List[dict]] = defaultdict(list)
    for rec in records:
        by_episode[rec.get("episode_id", "")].append(rec)

    kept: List[dict] = []
    dropped = 0
    dropped_episodes = 0
    migration: Counter = Counter()

    for episode_id, group in by_episode.items():
        task = _episode_task(episode_id, group)
        if task.origin == "boilerplate":
            dropped += len(group)
            dropped_episodes += 1
            continue
        for rec in group:
            migration[(rec.get("use_case"), task.use_case)] += 1
            rec["use_case_legacy"] = rec.get("use_case")
            rec["use_case"] = task.use_case
            rec["track"] = (
                uc.BY_KEY[task.use_case].track if task.use_case in uc.BY_KEY else "—"
            )
            rec["activity"] = task.activity
            rec["domain"] = task.domain
            rec["instruction_origin"] = task.origin
            kept.append(rec)

    return {
        "records": kept,
        "dropped_records": dropped,
        "dropped_episodes": dropped_episodes,
        "migration": migration,
    }


def _use_case_readme(key: str, recs: Sequence[dict]) -> str:
    """The definition, the success criterion, and what is in this set."""
    spec = uc.BY_KEY.get(key)
    parts = Counter(r.get("partition") for r in recs)
    diff = Counter(r.get("difficulty") for r in recs)
    pol = Counter(r.get("grading_polarity") for r in recs)
    axes = Counter(a for r in recs for a in (r.get("capability_axes") or []))
    episodes = len({r.get("episode_id") for r in recs})
    legacy = Counter(r.get("use_case_legacy") for r in recs)

    label = spec.label if spec else key
    lines = [
        f"# {label}",
        "",
        f"`{key}`" + (f" · track `{spec.track}`" if spec else ""),
        "",
    ]
    if spec:
        lines += [
            "## What this use case is",
            "",
            spec.definition,
            "",
            "## What a passing answer looks like",
            "",
            spec.evaluates,
            "",
        ]
    lines += [
        "## What is in this set",
        "",
        f"| | |",
        f"|---|---|",
        f"| Decision points (records) | **{len(recs)}** |",
        f"| Distinct episodes | {episodes} |",
        f"| Pool / oracle | {parts.get('pool', 0)} / {parts.get('oracle', 0)} |",
        f"| Difficulty (easy/moderate/hard) | "
        f"{diff.get('easy', 0)} / {diff.get('moderate', 0)} / {diff.get('hard', 0)} |",
        f"| Graded against reference / away from it | "
        f"{pol.get('match_reference', 0)} / {pol.get('avoid_reference', 0)} |",
        "",
    ]
    if len(recs) < MIN_RUNNABLE_RECORDS:
        lines += [
            f"> **Too small to score on its own.** Under {MIN_RUNNABLE_RECORDS} records, a "
            "percentage here moves several points on a single item. Use it for "
            "spot-checks and roll it into its track for reporting.",
            "",
        ]
    cx = COMPLEXITY.get(key)
    if cx:
        lines += [
            "## How demanding this work is in practice",
            "",
            "Measured from the real tasks this use case was drawn from, not from "
            "the records below. Rank is ordinal — it orders the 23 use cases, it "
            "does not say one is twice another.",
            "",
            "| | |",
            "|---|---|",
            f"| Complexity rank | **{cx['complexity_rank']} of 23**"
            + ("" if cx["complexity_reliable"] else " (under 20 tasks — indicative)")
            + " |",
            f"| Steps in a typical / heavy task | {cx['median_steps']} / {cx['p90_steps']} |",
            f"| Tokens per task (estimate) | {cx['tokens_per_task']/1000:,.0f}K |",
            f"| Tool-call failure rate | {cx['failure_pct']:.1f}% |",
            f"| Delegations per task | {cx['delegation_per_task']:.2f} |",
            "",
        ]
    lines += [
        "## Capabilities these records exercise",
        "",
        "| capability | records |",
        "|---|---:|",
    ]
    lines += [f"| `{a}` | {n} |" for a, n in axes.most_common(10)]
    lines += [
        "",
        "## Where these came from under the previous taxonomy",
        "",
        "The old labels were assigned once per session, so they scatter. This is "
        "shown to make the re-labelling auditable rather than to endorse it.",
        "",
        "| previous label | records |",
        "|---|---:|",
    ]
    lines += [f"| `{k}` | {n} |" for k, n in legacy.most_common(8)]
    lines += [
        "",
        "## Running just this set",
        "",
        "```bash",
        f"# every record in this file shares the use case '{key}'",
        "wc -l records.jsonl",
        "```",
        "",
        "`records.jsonl` is one JSON object per line. Each is a single decision "
        "point: the instruction, the state the agent was in, the action it took, "
        "and the checks that action must satisfy. `partition` separates the "
        "development pool from the held-out oracle — do not tune against oracle "
        "records.",
        "",
    ]
    return "\n".join(lines)


def real_world_weights(corpus: Path) -> Tuple[Dict[str, float], Dict[str, dict]]:
    """Each use case's share of real tasks, for weighting an overall score.

    **Record count is not frequency.** The pool was sampled for coverage, so a
    use case with many records is not necessarily common. Scoring the dataset
    unweighted answers "how good is the agent on our sample"; weighting by these
    answers "how good is the agent on the work people actually do", which is the
    question that matters. Both are worth reporting — see the manifest.
    """
    from gaia.factory.harvest.tasks import segment

    traces = []
    with (corpus / "traces.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                traces.append(json.loads(line))
    tasks = [t for tr in traces for t in segment(tr)]
    uc.label_all(tasks)
    real = [t for t in tasks if t.is_work]
    counts = Counter(t.use_case for t in real)
    shares = {k: v / len(real) for k, v in counts.items()}
    return shares, uc.profile(real, traces)


#: Filled by ``main`` when a corpus is supplied. Keyed by use case.
COMPLEXITY: Dict[str, dict] = {}


def write_split(records: Sequence[dict], out: Path) -> Dict[str, dict]:
    """One directory per use case. Returns per-use-case stats for the manifest."""
    by_uc: Dict[str, List[dict]] = defaultdict(list)
    for rec in records:
        by_uc[rec["use_case"]].append(rec)

    stats: Dict[str, dict] = {}
    for key, recs in sorted(by_uc.items(), key=lambda kv: -len(kv[1])):
        spec = uc.BY_KEY.get(key)
        d = out / key
        d.mkdir(parents=True, exist_ok=True)
        with (d / "records.jsonl").open("w", encoding="utf-8") as fh:
            for rec in recs:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        (d / "README.md").write_text(_use_case_readme(key, recs), encoding="utf-8")

        parts = Counter(r.get("partition") for r in recs)
        stats[key] = {
            "label": spec.label if spec else key,
            "track": spec.track if spec else "—",
            "records": len(recs),
            "episodes": len({r.get("episode_id") for r in recs}),
            "pool": parts.get("pool", 0),
            "oracle": parts.get("oracle", 0),
            "runnable_alone": len(recs) >= MIN_RUNNABLE_RECORDS,
            "difficulty": dict(Counter(r.get("difficulty") for r in recs)),
        }
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="Built dataset directory containing pool/ and oracle/.",
    )
    ap.add_argument(
        "--out", type=Path, required=True, help="Where to write the per-use-case sets."
    )
    ap.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help=(
            "Harvest cache with traces.jsonl. Supplies each use case's share of "
            "real-world tasks so an overall score can be frequency-weighted."
        ),
    )
    args = ap.parse_args()

    records = load_records(args.dataset)
    result = relabel(records)
    kept = result["records"]
    args.out.mkdir(parents=True, exist_ok=True)

    # Canonical first, views second. One file is the source of truth so the
    # per-use-case directories cannot silently disagree with it.
    canonical = args.out / "records.jsonl"
    with canonical.open("w", encoding="utf-8") as fh:
        for rec in kept:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    digest = hashlib.sha256(canonical.read_bytes()).hexdigest()

    # The corpus is read before the views are written: each view's README prints
    # the measured complexity of its use case, which needs COMPLEXITY filled.
    weights = {}
    if args.corpus:
        weights, profiles = real_world_weights(args.corpus)
        COMPLEXITY.update({k: v for k, v in profiles.items() if "complexity_rank" in v})

    stats = write_split(kept, args.out / "by_use_case")

    if weights:
        for key, st in stats.items():
            st["real_world_share"] = round(weights.get(key, 0.0), 4)
            st["dataset_share"] = round(st["records"] / len(kept), 4)

    manifest = {
        "canonical": "records.jsonl",
        "canonical_sha256": digest,
        "views": "by_use_case/<use_case>/records.jsonl (generated from the canonical file)",
        "source_records": len(records),
        "kept_records": len(kept),
        "dropped_boilerplate_records": result["dropped_records"],
        "dropped_boilerplate_episodes": result["dropped_episodes"],
        "use_cases": len(stats),
        "runnable_alone": sum(1 for v in stats.values() if v["runnable_alone"]),
        "min_runnable_records": MIN_RUNNABLE_RECORDS,
        "weighted_scoring": (
            "Score each use case separately, then report three numbers: the "
            "unweighted mean across use cases, the mean weighted by "
            "real_world_share (how good on work people actually do), and the "
            "per-track rollup. Do not report a single pooled number - it is "
            "dominated by whichever use case happens to have the most records."
        ),
        "per_use_case": stats,
    }
    (args.out / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(
        f"{len(kept)} records across {len(stats)} use cases "
        f"({manifest['runnable_alone']} individually runnable); "
        f"dropped {result['dropped_records']} boilerplate records"
    )


if __name__ == "__main__":
    main()
