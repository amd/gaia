# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Generate the dataset README from the manifest, so the two cannot disagree.

A hand-written README describing counts drifts the first time the set is
rebuilt. This derives every number from ``MANIFEST.json`` and the records
themselves.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List

from gaia.factory.harvest import use_cases as uc


def _coverage_rows(per: Dict[str, dict]) -> List[str]:
    """Use cases ranked by how badly the set's mix differs from real work."""
    rows = []
    for key, v in per.items():
        ds, real = v.get("dataset_share", 0.0), v.get("real_world_share", 0.0)
        if not real and not ds:
            continue
        # Ratio of representation. Guard the zero so an uncovered-but-common
        # use case sorts to the top rather than dividing by zero.
        ratio = (ds / real) if real else float("inf")
        rows.append((key, v, ds, real, ratio))
    rows.sort(key=lambda r: r[4])
    return rows


def build(data: Path) -> str:
    manifest = json.loads((data / "MANIFEST.json").read_text(encoding="utf-8"))
    per = manifest["per_use_case"]
    # Iterate the handle rather than splitlines(): str.splitlines() also breaks
    # on U+2028/U+2029, which JSON allows unescaped inside a string, so a record
    # containing one would be split into two invalid fragments.
    # Parse the canonical file even though the counts come from the manifest:
    # a README describing records that no longer load is worse than no README.
    with (data / "records.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                json.loads(line)
    tracks: Counter = Counter()
    track_recs: Counter = Counter()
    for k, v in per.items():
        tracks[v["track"]] += 1
        track_recs[v["track"]] += v["records"]

    L: List[str] = []
    A = L.append

    A("# Agent evaluation sets, one per use case")
    A("")
    A(
        f"**{manifest['kept_records']:,} decision points across {manifest['use_cases']} "
        f"use cases**, each with its own definition, its own success criterion, and its "
        f"own pool/oracle split — so every kind of work can be scored on its own instead "
        f"of disappearing into one blended number."
    )
    A("")
    A(
        "**Private. Do not commit any of this to a repository.** The records are derived "
        "from real working sessions."
    )
    A("")
    A("---")
    A("")

    # ------------------------------------------------------------------ what
    A("## 1. What a record is")
    A("")
    A(
        "One record is a single **decision point**: a moment where the agent had to "
        "choose what to do next. It carries the instruction that started the episode, "
        "the state the agent was in, the action it actually took, and the checks that "
        "action has to satisfy. Scoring asks a simple question — *given this situation, "
        "was that a good move?*"
    )
    A("")
    A("| field | what it holds |")
    A("|---|---|")
    for f, d in [
        ("`episode_instruction`", "The request that started the whole task."),
        ("`goal`", "What the agent was trying to achieve at this point."),
        ("`state`, `observation`", "What it knew and had just seen."),
        ("`action`", "The tool call(s) it made. `width` > 1 means a parallel call."),
        ("`reference_checks`", "Mechanical checks the action must satisfy."),
        ("`difficulty`", "`easy` / `moderate` / `hard`."),
        (
            "`grading_polarity`",
            "`match_reference` = do this. `avoid_reference` = the reference was a mistake; a good answer differs.",
        ),
        ("`capability_axes`", "Which abilities this moment exercises."),
        ("`use_case`, `track`", "The classification this split is built on."),
        ("`partition`", "`pool` for development, `oracle` for held-out scoring."),
        ("`use_case_legacy`", "The previous label, kept so re-labelling is auditable."),
    ]:
        A(f"| {f} | {d} |")
    A("")
    A(
        "> **`avoid_reference` matters.** Some recorded actions were mistakes. Those "
        "records are graded inverted — matching what actually happened is the wrong "
        "answer. A scorer that ignores `grading_polarity` will reward the agent for "
        "reproducing known errors."
    )
    A("")

    # ------------------------------------------------------------------ layout
    A("## 2. How it is laid out")
    A("")
    A("```")
    A("records.jsonl              <- canonical. every record. the source of truth.")
    A("MANIFEST.json              <- counts, checksums, scoring guidance")
    A("by_use_case/")
    A("  bug_fix/")
    A("    records.jsonl          <- a view, generated from the canonical file")
    A("    README.md              <- definition + success criterion + what is inside")
    A("  code_review/")
    A("  ...")
    A("```")
    A("")
    A(
        "The per-use-case directories are **views**, regenerated from the canonical "
        "file. Edit the canonical file and rebuild; never edit a view directly, or the "
        "two will disagree and nothing will tell you."
    )
    A("")
    A('For anything that is not simply "one use case", query instead of copying files:')
    A("")
    A("```bash")
    A("# what can I filter on?")
    A("python -m gaia.factory.dataset.select --data .")
    A("")
    A("# every hard build-track record, development pool only")
    A("python -m gaia.factory.dataset.select --data . \\")
    A("    --track build --difficulty hard --partition pool --out slice.jsonl")
    A("")
    A("# two use cases at once")
    A(
        "python -m gaia.factory.dataset.select --data . --use-case bug_fix --use-case refactor"
    )
    A("```")
    A("")

    # ------------------------------------------------------------------ sets
    A("## 3. The sets")
    A("")
    A(
        f"Grouped into {len(tracks)} tracks. **records** is decision points; **episodes** "
        f"is distinct tasks they came from — the second number is the one that governs "
        f"how much you can conclude (see §5)."
    )
    A("")
    for track, tdef in uc.TRACKS.items():
        members = {k: v for k, v in per.items() if v["track"] == track}
        if not members:
            continue
        A(f"### `{track}` — {track_recs[track]:,} records")
        A("")
        A(tdef)
        A("")
        A("| use case | records | episodes | pool / oracle | scorable alone |")
        A("|---|---:|---:|---:|:---:|")
        for k, v in sorted(members.items(), key=lambda kv: -kv[1]["records"]):
            mark = "yes" if v["runnable_alone"] else "**no**"
            A(
                f"| [{v['label']}](by_use_case/{k}/) | {v['records']} | {v['episodes']} "
                f"| {v['pool']} / {v['oracle']} | {mark} |"
            )
        A("")
    other = {k: v for k, v in per.items() if v["track"] == "—"}
    if other:
        A("### Unclassified")
        A("")
        A(
            "Records whose instruction matched no use-case pattern. Kept rather than "
            "deleted: the size of this bucket is how you judge whether the taxonomy "
            "covers the work. Exclude it from scoring."
        )
        A("")
        for k, v in other.items():
            A(f"- `{k}` — {v['records']} records across {v['episodes']} episodes")
        A("")

    # ------------------------------------------------------------------ scoring
    A("## 4. How to score this")
    A("")
    A("**Report three numbers, not one.**")
    A("")
    A("| number | what it answers |")
    A("|---|---|")
    A(
        "| Per use case | Where is the agent strong and weak? The reason this split exists. |"
    )
    A(
        "| Unweighted mean across use cases | How broad is the capability? Treats a rare use case as equal to a common one. |"
    )
    A(
        "| **Frequency-weighted mean** | **How good is it on the work people actually do?** Weight each use case by `real_world_share`. |"
    )
    A("")
    A(
        "A single pooled score over `records.jsonl` is the one number **not** to report. "
        "It is dominated by whichever use case happens to have the most records, and as "
        "§5 shows, that is not the same as the most important."
    )
    A("")

    # ------------------------------------------------------------------ limits
    A("## 5. Read this before quoting a score")
    A("")
    A("### The set is not proportional to real work")
    A("")
    A(
        "Records were sampled for coverage, not to mirror reality. The gap is large "
        "enough to change conclusions. **dataset %** is this set; **real %** is the "
        "measured share of real tasks."
    )
    A("")
    A("| use case | dataset % | real % | representation |")
    A("|---|---:|---:|---|")
    rows = _coverage_rows(per)
    for key, v, ds, real, ratio in rows[:6]:
        note = f"**{real/ds:.0f}x under**" if ds and real > ds else "**barely covered**"
        A(f"| {v['label']} | {100*ds:.1f}% | {100*real:.1f}% | {note} |")
    A("| … | | | |")
    for key, v, ds, real, ratio in rows[-4:]:
        if real and ds > real:
            A(
                f"| {v['label']} | {100*ds:.1f}% | {100*real:.1f}% | {ds/real:.0f}x over |"
            )
    A("")
    A(
        "The worst cases are the most common work: **question answering is 16.8% of "
        "real tasks and 1.8% of this set**, and **CI failure triage is 3.7% of real "
        "work with 2 records**. Meanwhile release-and-packaging and document-audit are "
        "several times over-represented. Frequency weighting corrects the score; it "
        "does not manufacture the missing records. Closing the gaps needs new sampling."
    )
    A("")
    A("### Records within an episode are not independent")
    A("")
    A(
        "Several use cases draw many records from few episodes — a set with 291 records "
        "from 36 episodes is closer to 36 independent samples than 291, because moments "
        "inside one task share its context, its repository and its mistakes. **Treat the "
        "episode count as the effective sample size** when judging whether a difference "
        "between two runs is real."
    )
    A("")
    A("### Other limits")
    A("")
    A("| limit | what it means |")
    A("|---|---|")
    A(
        f"| Boilerplate removed, not scored | {manifest['dropped_boilerplate_records']} "
        f"records from {manifest['dropped_boilerplate_episodes']} episodes were driven by "
        "orchestrator messages no human wrote. They were previously scored as if someone "
        "had asked. |"
    )
    A(
        "| Labels are keyword patterns | Deterministic and reproducible, but blunt. A "
        "compound request gets one label, most-specific-verb-wins. `use_case_legacy` is "
        "retained so any re-label can be audited. |"
    )
    A(
        f"| {manifest['use_cases'] - manifest['runnable_alone']} sets are too small | Under "
        f"{manifest['min_runnable_records']} records a single item moves the percentage "
        "several points. Roll these into their track for reporting. |"
    )
    A(
        "| One reference answer per moment | Often several actions are equally good. A "
        "scorer that demands the recorded one will under-credit valid alternatives. |"
    )
    A(
        "| Single corpus, single operator | The distribution reflects one working "
        "context. What each use case *requires* generalises better than how often it "
        "occurs. |"
    )
    A("")

    # ------------------------------------------------------------------ rebuild
    A("## 6. Rebuilding")
    A("")
    A("```bash")
    A("python -m gaia.factory.dataset.usecase_split \\")
    A("    --dataset <built dataset with pool/ and oracle/> \\")
    A("    --out <this directory> \\")
    A("    --corpus <harvest cache with traces.jsonl>")
    A("")
    A("python -m gaia.factory.dataset.split_report --data <this directory>")
    A("```")
    A("")
    A(
        "`--corpus` supplies the real-world frequencies. Without it the sets still "
        "build, but the weighted score cannot be computed and the coverage table above "
        "will be absent — which is the whole point of it, so pass it."
    )
    A("")
    A(f"Canonical file checksum: `{manifest['canonical_sha256'][:16]}…`")
    A("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, required=True)
    args = ap.parse_args()
    out = args.data / "README.md"
    out.write_text(build(args.data), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
