# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Write the task catalogue: what the suite tests, and how each task is decided.

Generated from the suite itself, never typed. A hand-maintained list of tasks
drifts the moment someone edits a verifier, and a catalogue that disagrees with
the benchmark is worse than none — it describes a benchmark nobody is running.

Usage::

    python -m gaia.factory.tasks.catalogue --out TASKS.md
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import List

from .enterprise import FAMILIES, LEVEL_BY_KEY, family_of
from .suite import TASKS
from .suite_extra import NOT_COVERED

#: What each track is for, in the corpus taxonomy's terms. Stated here so the
#: catalogue is readable by someone who has never seen that taxonomy.
TRACK_MEANING = {
    "build": "Producing or changing software — new capability, defect repair, "
    "restructuring, dependency moves.",
    "verify": "Establishing whether something is correct — reviewing a change, "
    "auditing a document, writing tests. Produces findings, not features.",
    "operate": "Moving work through the system and keeping it running — CI, "
    "releases, repository mechanics, machine setup.",
    "content": "Producing prose for people to read — documents, notes, "
    "reference material.",
    "analyse": "Turning a body of data into an interpretation, or moving it "
    "between shapes.",
    "decide": "Working out what should happen next — planning, triage, "
    "answering questions.",
    "agent": "Work on the agent system itself: its prompts, skills, tools and "
    "configuration.",
}


def build() -> str:
    L: List[str] = []
    A = L.append
    by_track = defaultdict(list)
    for t in TASKS:
        by_track[t.track].append(t)
    use_cases = Counter(t.use_case for t in TASKS)

    A("# Task catalogue")
    A("")
    A(
        f"**{len(TASKS)} tasks across {len(by_track)} tracks and "
        f"{len(use_cases)} use cases.** Generated from the suite — if this "
        "disagrees with what runs, the generator is broken, not the list."
    )
    A("")
    A(
        "Each task creates a real workspace, hands the agent a request in a "
        "user's words, lets the real agent loop run to completion, and then "
        "runs a command that decides whether the work was done. **The "
        "pass/fail column is that command, not an opinion.** The rubric is "
        "separate and only ever judges work that already passed."
    )
    A("")
    A(
        "Every verifier in this list is itself tested in both directions by "
        "`tests/unit/factory/test_task_suite.py`: it must reject the untouched "
        "starting state, and accept a hand-written correct solution. A task "
        "that fails either check cannot score anyone."
    )
    A("")

    # ------------------------------------------------------------- coverage
    A("## Coverage at a glance")
    A("")
    A("| track | tasks | use cases |")
    A("|---|---:|---|")
    for track in sorted(by_track, key=lambda t: -len(by_track[t])):
        ucs = sorted({t.use_case for t in by_track[track]})
        A(f"| **{track}** | {len(by_track[track])} | {', '.join(ucs)} |")
    A("")
    for track in sorted(by_track):
        A(f"- **{track}** — {TRACK_MEANING.get(track, '')}")
    A("")

    # ------------------------------------------------- the enterprise lens
    A("## The same tasks, grouped by what a wrong answer costs")
    A("")
    A(
        "Tracks group by *activity* — useful for measuring an agent, useless "
        "for deciding what to let it do unsupervised. This second grouping asks "
        "the operator's question instead, and it is the one that determines how "
        "much supervision each task needs."
    )
    A("")
    A("| family | tasks | default supervision | why |")
    A("|---|---:|---|---|")
    for fam in FAMILIES:
        mine = [t for t in TASKS if family_of(t.use_case) == fam.key]
        A(
            f"| **{fam.name}** | {len(mine)} "
            f"| {LEVEL_BY_KEY[fam.autonomy].name} "
            f"| {fam.blast_radius.replace('**', '')} |"
        )
    A("")
    A("Every task, by family:")
    A("")
    A("| task | use case | family | reversible | machine-checkable |")
    A("|---|---|---|---|---|")
    for fam in FAMILIES:
        for task in sorted(
            (t for t in TASKS if family_of(t.use_case) == fam.key),
            key=lambda t: t.key,
        ):
            A(
                f"| `{task.key}` | {task.use_case} | {fam.name} "
                f"| {'yes' if fam.reversible else '**no**'} "
                f"| {'no — judged' if task.judged_only else 'yes'} |"
            )
    A("")
    A(
        "_**Reversible** asks whether a mistake can be undone by re-running; "
        "where it cannot, no pass rate makes unattended operation safe. "
        "**Machine-checkable** asks whether a command can separate success from "
        "failure — where it cannot, the agent has no way to know it succeeded "
        "either, so a human has to read the result._"
    )
    A("")

    # ------------------------------------------------------------ the tasks
    for track in sorted(by_track, key=lambda t: -len(by_track[t])):
        A(f"## {track}")
        A("")
        A(f"_{TRACK_MEANING.get(track, '')}_")
        A("")
        for task in sorted(by_track[track], key=lambda t: t.key):
            A(f"### `{task.key}`")
            A("")
            A(f"**Use case:** {task.use_case}")
            A("")
            A(f"**The agent is asked:** {task.prompt}")
            A("")
            start = ", ".join(f"`{f}`" for f in sorted(task.setup))
            A(f"**Starting workspace:** {start or '(empty)'}")
            if task.git_init:
                A("")
                A(
                    "The workspace is a git repository with those files already "
                    "committed, because the task is about repository state."
                )
            A("")
            A(f"**Passes when:** `{task.verify.strip()}` exits 0.")
            A("")
            A(f"**Judged on:** {task.rubric}")
            A("")
            if task.expect_touched:
                touched = ", ".join(f"`{f}`" for f in task.expect_touched)
                A(f"**Expected to produce or change:** {touched}")
                A("")

    # ------------------------------------------------------------- excluded
    A("## Use cases deliberately not covered")
    A("")
    A(
        "These exist in the corpus taxonomy but earn no task, because their "
        "outcome cannot be checked by a command in a sealed workspace. "
        "Including them would mean grading on a judge's opinion alone — which "
        "is the thing this suite exists to stop doing. They are listed rather "
        "than quietly missing."
    )
    A("")
    A("| use case | why not |")
    A("|---|---|")
    for key, why in sorted(NOT_COVERED.items()):
        A(f"| `{key}` | {why} |")
    A("")

    # ---------------------------------------------------------------- limits
    A("## What the suite still cannot tell you")
    A("")
    A(
        "- **Tasks are small and self-contained.** That is what makes them "
        "cheap enough to rerun on every change. They do not test work spanning "
        "a large existing codebase, sessions lasting hours, or recovery from a "
        "mistake made long before."
    )
    A(
        "- **One attempt per task.** Agent runs are not deterministic; a single "
        "pass or fail carries real variance. Differences of one task are inside "
        "the noise."
    )
    A(
        "- **The verifier checks the outcome, not the route.** An agent that "
        "arrives by a wasteful path still passes, which is why steps, repeated "
        "calls and failed calls are reported beside the pass rate."
    )
    A("")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(build(), encoding="utf-8")
    print(f"wrote {a.out} ({a.out.stat().st_size:,} bytes, {len(TASKS)} tasks)")


if __name__ == "__main__":
    main()
