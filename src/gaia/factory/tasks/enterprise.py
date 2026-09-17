# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""An enterprise-facing grouping of the use cases, and why it differs.

The corpus taxonomy groups work by *activity* — build, verify, operate,
content, analyse, decide, agent. That is the right axis for measuring an agent
and the wrong one for deciding what to deploy, because it says nothing about
the question a buyer actually asks: **what happens if this is wrong?**

Reviewing a pull request and cutting a release are both "work on a codebase".
One produces an opinion a human can overrule; the other changes what customers
receive. Grouped by activity they sit in adjacent buckets. Grouped by blast
radius they are nowhere near each other, and only the second grouping tells you
which to automate first.

So this is a second, coarser lens over the same 23 use cases:

* **What it is for** — in a sentence a non-engineer can act on.
* **Blast radius** — what a wrong answer costs, which is what bounds autonomy.
* **Can a machine check it** — whether success is decidable, which determines
  whether it can be improved by measurement or only by review.

Shares are computed from the recorded corpus, never typed, so the ranking
reflects what the work actually was rather than what anyone assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence


@dataclass(frozen=True)
class Family:
    """One enterprise-legible grouping of use cases."""

    key: str
    name: str
    #: Name for narrow tables. A full name that wraps into the next column
    #: is worse than an abbreviation the reader can still place.
    short: str
    #: What this family is for, in business terms.
    purpose: str
    #: What a wrong answer costs. This is what bounds how much autonomy is safe.
    blast_radius: str
    #: Default rung of :data:`AUTONOMY_LEVELS` this family sits at before
    #: any measurement. Evidence can move a use case down, never up.
    autonomy: str
    #: ``software`` or ``knowledge``. The split people actually reason in:
    #: whether the work requires touching a codebase at all. It cuts across
    #: blast radius rather than aligning with it, which is why both groupings
    #: are needed — one says how risky the work is, this says who does it.
    domain: str
    #: Can a mistake be undone by re-running, or has something left the
    #: building? This is the hard gate on unattended operation: no measured
    #: pass rate makes an irreversible action safe to take unsupervised.
    reversible: bool
    use_cases: Sequence[str]


@dataclass(frozen=True)
class AutonomyLevel:
    """One rung of the supervision ladder, defined once and used everywhere."""

    key: str
    name: str
    definition: str
    #: What has to be true for this rung to be defensible.
    requires: str


#: Four rungs, from most to least supervised. The vocabulary is fixed so that
#: "medium autonomy" cannot mean three different things in three tables — the
#: distinction that matters is **who sees the result before it takes effect**,
#: not how clever the agent is.
AUTONOMY_LEVELS: Sequence[AutonomyLevel] = (
    AutonomyLevel(
        key="unattended",
        name="Unattended",
        definition=(
            "The agent runs and its result takes effect. Nobody looks unless "
            "something fails."
        ),
        requires=(
            "A machine can tell success from failure, the action is reversible, "
            "and the pass rate is demonstrated on enough attempts to believe."
        ),
    ),
    AutonomyLevel(
        key="checked",
        name="Checked after",
        definition=(
            "The agent applies the result, and a human reviews it afterwards "
            "on their own schedule. Mistakes reach the artefact but not the "
            "customer."
        ),
        requires=(
            "The action is reversible and a mistake is visible to a reviewer "
            "who was not watching it happen."
        ),
    ),
    AutonomyLevel(
        key="gated",
        name="Gated",
        definition=(
            "The agent proposes; a human approves before anything takes "
            "effect. The agent does the work, the human owns the decision."
        ),
        requires=(
            "Used when the action is irreversible, or when being wrong is "
            "expensive enough that after-the-fact review is too late."
        ),
    ),
    AutonomyLevel(
        key="assisted",
        name="Assisted",
        definition=(
            "A human drives and the agent contributes. The agent never acts on "
            "its own initiative."
        ),
        requires="Used where success is undefined or the agent is unproven.",
    ),
)

LEVEL_BY_KEY = {level.key: level for level in AUTONOMY_LEVELS}

FAMILIES: Sequence[Family] = (
    Family(
        key="ship",
        short="Ship software",
        domain="software",
        name="Ship software",
        purpose=(
            "Write and change the product itself — new capability, defect "
            "repair, restructuring, keeping dependencies current."
        ),
        blast_radius=(
            "A wrong answer reaches users as a bug. Caught by tests and review "
            "if they exist, which is why this family is the easiest to trust: "
            "the safety net is already built."
        ),
        autonomy="checked",
        reversible=True,
        use_cases=(
            "feature_impl",
            "bug_fix",
            "refactor",
            "security_fix",
            "dependency_upgrade",
        ),
    ),
    Family(
        key="guard",
        short="Guard quality",
        domain="software",
        name="Guard quality",
        purpose=(
            "Decide whether someone else's work is fit to proceed — reviewing "
            "changes, auditing documents, triaging what broke."
        ),
        blast_radius=(
            "A wrong answer is a missed defect or a false alarm. A missed "
            "defect ships; a false alarm wastes an engineer's afternoon and, "
            "repeated, trains the team to ignore the agent."
        ),
        autonomy="checked",
        reversible=True,
        use_cases=(
            "code_review",
            "pr_triage",
            "issue_triage",
            "doc_audit",
            "ci_failure_triage",
            "test_authoring",
        ),
    ),
    Family(
        key="operate",
        short="Run operations",
        domain="software",
        name="Run the operation",
        purpose=(
            "Act on real systems and repositories — cutting releases, "
            "repository mechanics, preparing environments."
        ),
        blast_radius=(
            "**The highest here.** A wrong answer publishes a bad release, "
            "rewrites history or leaks a secret. Several of these actions are "
            "not reversible by re-running the agent."
        ),
        autonomy="gated",
        reversible=False,
        use_cases=("release_cut", "repo_ops", "env_setup"),
    ),
    Family(
        key="decide",
        short="Decisions",
        domain="knowledge",
        name="Turn information into decisions",
        purpose=(
            "Answer questions, research options, plan work, and rank what "
            "matters. The largest family in real use, and the least like "
            "programming."
        ),
        blast_radius=(
            "A wrong answer is a confidently wrong recommendation, and it is "
            "the hardest failure to notice: there is no test to go red, only a "
            "decision that turns out badly later."
        ),
        autonomy="checked",
        reversible=True,
        use_cases=(
            "qa_conversational",
            "web_research",
            "planning",
            "data_analysis",
            "data_extraction",
        ),
    ),
    Family(
        key="produce",
        short="Documents",
        domain="knowledge",
        name="Produce material for people",
        purpose=(
            "Write the documents, notes and decks that other people read — "
            "references, meeting records, summaries for a room."
        ),
        blast_radius=(
            "A wrong answer is plausible text stating something untrue, which "
            "propagates because it reads well. Invented detail is the "
            "characteristic failure, not omission."
        ),
        autonomy="checked",
        reversible=True,
        use_cases=("doc_authoring", "meeting_transcript", "slide_deck"),
    ),
    Family(
        key="self",
        short="Agent itself",
        domain="software",
        name="Extend the agent itself",
        purpose=(
            "Work on the agent's own prompts, skills, tools and configuration "
            "— the team using the agent to improve the agent."
        ),
        blast_radius=(
            "A wrong answer degrades every later task quietly. The worst "
            "property of this family is that its failures are invisible until "
            "something else is measured."
        ),
        autonomy="gated",
        reversible=False,
        use_cases=("agent_config",),
    ),
)

FAMILY_OF: Dict[str, str] = {uc: fam.key for fam in FAMILIES for uc in fam.use_cases}


def family_of(use_case: str) -> str:
    """Enterprise family for *use_case*, or ``other`` when unmapped."""
    return FAMILY_OF.get(use_case, "other")


def coverage(tasks) -> Dict[str, Dict[str, object]]:
    """How many suite tasks land in each family, and which use cases."""
    out: Dict[str, Dict[str, object]] = {}
    for fam in FAMILIES:
        mine = [t for t in tasks if family_of(t.use_case) == fam.key]
        out[fam.key] = {
            "name": fam.name,
            "tasks": len(mine),
            "task_keys": sorted(t.key for t in mine),
            "use_cases_covered": sorted({t.use_case for t in mine}),
            "use_cases_defined": list(fam.use_cases),
        }
    return out


def shares(use_case_counts: Dict[str, int]) -> List[Dict[str, object]]:
    """Family shares of real recorded work, largest first.

    Takes the corpus's per-use-case counts so the ranking is measured. An
    unmapped use case is reported under ``other`` rather than dropped — a
    silently discarded slice would make every share above it look larger.
    """
    total = sum(use_case_counts.values()) or 1
    by_family: Dict[str, int] = {}
    for uc, n in use_case_counts.items():
        by_family[family_of(uc)] = by_family.get(family_of(uc), 0) + n
    names = {f.key: f.name for f in FAMILIES}
    names["other"] = "Unclassified"
    return sorted(
        (
            {
                "key": key,
                "name": names.get(key, key),
                "tasks": n,
                "share": round(100 * n / total, 1),
            }
            for key, n in by_family.items()
        ),
        key=lambda row: -row["share"],
    )


#: A use case is only a candidate for unattended running when the best system
#: measured actually clears this. Set high on purpose: at 90% one task in ten
#: is wrong, which is already a lot to let through without a human.
UNATTENDED_PASS_BAR = 90.0

#: Attempts required before a pass rate is allowed to justify anything. At
#: one or two attempts per use case a '100%' is one or two lucky runs, and
#: reporting it as evidence of autonomy is how a benchmark misleads people
#: who did not build it.
MIN_ATTEMPTS_FOR_A_VERDICT = 5


def autonomy_table(episodes, metas=None):
    """Per use case: can this run unattended, and what does the evidence say?

    Three gates, applied in order, each of which can only *downgrade*:

    1. **Reversible?** An irreversible action is never unattended, whatever the
       pass rate. Cutting a release or rewriting history cannot be undone by
       re-running the agent.
    2. **Machine-checkable?** If no command can tell success from failure, the
       agent cannot know it succeeded either — so a human has to look.
    3. **Reliable enough?** Measured from the runs, using the best-performing
       system on that use case. A capability nothing has demonstrated is not
       made safe by being easy to check.

    Returns rows sorted worst-first, because the interesting end of this table
    is what you cannot yet automate.
    """
    from .suite import BY_KEY

    best: Dict[str, Dict[str, object]] = {}
    for arm, eps in episodes.items():
        for e in eps:
            uc = e["use_case"]
            slot = best.setdefault(uc, {"passed": 0, "total": 0, "arm": arm})
            # Track the best single arm per use case, not a pooled average: the
            # question is whether *some* system can be trusted with it.
            by_arm = slot.setdefault("_by_arm", {})
            rec = by_arm.setdefault(arm, [0, 0])
            rec[0] += 1 if e["accomplished"] else 0
            rec[1] += 1

    rows = []
    for uc, slot in best.items():
        by_arm = slot["_by_arm"]
        arm, (passed, total) = max(
            by_arm.items(), key=lambda kv: (kv[1][0] / max(kv[1][1], 1), kv[1][1])
        )
        rate = 100 * passed / total if total else 0.0
        tasks = [t for t in BY_KEY.values() if t.use_case == uc]
        checkable = bool(tasks) and not all(t.judged_only for t in tasks)
        fam_key = family_of(uc)
        fam = next((f for f in FAMILIES if f.key == fam_key), None)
        reversible = fam.reversible if fam else True

        # Gates apply in order and only ever downgrade.
        if not reversible:
            verdict, why = "gated", "irreversible — a human must approve the action"
        elif not checkable:
            verdict, why = "gated", "no machine check, so the agent cannot self-verify"
        elif total < MIN_ATTEMPTS_FOR_A_VERDICT:
            verdict, why = (
                "checked",
                f"only {total} attempt(s) — too little evidence to trust it alone",
            )
        elif rate < UNATTENDED_PASS_BAR:
            verdict, why = "checked", f"best measured {rate:.0f}%, under the bar"
        else:
            verdict, why = (
                "unattended",
                f"self-checkable, reversible, {rate:.0f}% over {total} attempts",
            )

        rows.append(
            {
                "use_case": uc,
                "family": fam.name if fam else "—",
                "best_arm": arm,
                "pass_rate": round(rate, 1),
                "n": total,
                "reversible": reversible,
                "machine_checkable": checkable,
                "verdict": LEVEL_BY_KEY[verdict].name,
                "verdict_key": verdict,
                "why": why,
            }
        )
    rank = {l.key: i for i, l in enumerate(AUTONOMY_LEVELS)}
    return sorted(rows, key=lambda r: (-rank[r["verdict_key"]], -r["pass_rate"]))


def autonomy_rollup(rows, use_case_counts=None):
    """Per level: how many use cases, and what share of real work they carry.

    Two denominators, and conflating them is the trap this exists to avoid.
    *Share of use cases* counts every use case equally, which flatters the rare
    ones. *Share of real work* weights each by how often it actually occurred in
    the recorded corpus, and is the figure that answers the question an operator
    is really asking: **how much of what we do could run unattended today?**

    Levels with nothing in them are still emitted. A zero is a finding — an
    autonomy level that no use case reaches is exactly what a reader needs to
    see, and omitting the row would let them assume it simply was not assessed.
    """
    total_uc = len(rows) or 1
    counts = use_case_counts or {}
    measured_volume = sum(counts.get(r["use_case"], 0) for r in rows)
    corpus_volume = sum(counts.values())

    out = []
    for level in AUTONOMY_LEVELS:
        mine = [r for r in rows if r["verdict_key"] == level.key]
        volume = sum(counts.get(r["use_case"], 0) for r in mine)
        out.append(
            {
                "level": level.name,
                "key": level.key,
                "use_cases": len(mine),
                "share_of_use_cases": round(100 * len(mine) / total_uc, 1),
                # Of the work this suite actually measures...
                "share_of_measured_work": (
                    round(100 * volume / measured_volume, 1) if measured_volume else 0.0
                ),
                # ...and of everything in the corpus, measured or not.
                "share_of_all_work": (
                    round(100 * volume / corpus_volume, 1) if corpus_volume else 0.0
                ),
                "examples": sorted(r["use_case"] for r in mine)[:4],
            }
        )
    return out, {
        "measured_volume": measured_volume,
        "corpus_volume": corpus_volume,
        "coverage": (
            round(100 * measured_volume / corpus_volume, 1) if corpus_volume else 0.0
        ),
    }


#: Human-readable names for the two domains, and what separates them.
DOMAINS = {
    "software": (
        "Software development",
        "Work that touches a codebase or the systems around it — writing it, "
        "checking it, shipping it, keeping it running.",
    ),
    "knowledge": (
        "Knowledge work",
        "Work that produces an answer, a decision or a document. It may be "
        "*about* software, but no repository is modified to do it.",
    ),
}


def by_domain(use_case_counts=None):
    """Both domains with their families and, when given counts, their shares."""
    counts = use_case_counts or {}
    total = sum(counts.values()) or 1
    out = []
    for key, (name, definition) in DOMAINS.items():
        fams = [f for f in FAMILIES if f.domain == key]
        volume = sum(counts.get(uc, 0) for f in fams for uc in f.use_cases)
        out.append(
            {
                "key": key,
                "name": name,
                "definition": definition,
                "families": fams,
                "use_cases": sum(len(f.use_cases) for f in fams),
                "share": round(100 * volume / total, 1) if counts else None,
                "tasks_in_corpus": volume,
            }
        )
    return sorted(out, key=lambda d: -(d["share"] or 0))
