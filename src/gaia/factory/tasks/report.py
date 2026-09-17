# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Turn task runs into a report: what worked, how it was done, what it cost.

Every figure is re-derived from the run directory on each build, so the prose
cannot drift away from the data it describes.

The report keeps **correctness and quality in separate columns and never adds
them together.** They answer different questions and have different standing:
one is an exit code, the other is an opinion. A single blended "score" would
hide which of the two moved.

Beyond the headline it reports *how* the work was done — steps, repeated calls,
failed calls, which tools each arm reached for, how the prompt grew. That is the
part that informs harness and architecture decisions: two arms with the same
pass rate and a 3× difference in steps are not the same result.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Dict, List

#: Below this many judged tasks an arm's quality percentage swings double digits
#: on one item. Such figures are shown — hiding them would misstate coverage —
#: but marked, so nobody quotes them as a score.
MIN_FOR_QUALITY_SCORE = 4

#: How each harness is named in every table. Three different things get called
#: "Claude" in this work and conflating them makes a report unreadable:
#:
#: * ``GAIA`` — GAIA's agent loop, prompt and tools, with some model behind it.
#:   ``GAIA-Claude-Opus-5`` is *GAIA driven by Opus*, not Claude Code.
#: * ``Claude Code`` — Anthropic's agent: its own loop, prompt and tools, run
#:   here on the same tasks. The reference for how far GAIA has to go.
#: * ``Claude Code sessions`` — the harvested corpus of real recorded work.
#:   Never an arm; it is observational data about tasks nobody ran for a
#:   benchmark, and it is always labelled as such.
HARNESS_LABEL = {"gaia": "GAIA", "claude-code": "Claude Code"}


def harness_of(arm: str, metas: Dict[str, dict]) -> str:
    """Human-readable harness for *arm*, inferred from the name if unrecorded."""
    recorded = (metas.get(arm) or {}).get("harness")
    if recorded:
        return HARNESS_LABEL.get(recorded, recorded)
    return "Claude Code" if arm.startswith("ClaudeCode") else "GAIA"


def order_arms(episodes: Dict[str, List[dict]], metas: Dict[str, dict]) -> List[str]:
    """Reference arms first, then the arms under test.

    Claude Code is the yardstick, not a competitor in the ranking, so its rows
    stay together at the top of every table however well or badly GAIA does.
    Sorting everything by pass rate would let the reference drift into the
    middle of the field and turn a comparison into a leaderboard.
    """
    return sorted(
        episodes,
        key=lambda a: (
            harness_of(a, metas) != "Claude Code",
            -_pass_rate(episodes[a]),
            a,
        ),
    )


def load(run: Path, arms_dir: str):
    episodes: Dict[str, List[dict]] = {}
    metas: Dict[str, dict] = {}
    for d in sorted((run / arms_dir).iterdir()):
        ep, mt = d / "episodes.json", d / "run_meta.json"
        if ep.exists():
            episodes[d.name] = json.loads(ep.read_text(encoding="utf-8"))
        if mt.exists():
            metas[d.name] = json.loads(mt.read_text(encoding="utf-8"))
    quality, qverdicts, qmapping = {}, {}, {}
    qdir = run / "_quality"
    if (qdir / "scorecard.json").exists():
        quality = json.loads((qdir / "scorecard.json").read_text(encoding="utf-8"))
        qverdicts = json.loads((qdir / "verdicts.json").read_text(encoding="utf-8"))
        qmapping = json.loads((qdir / "mapping.json").read_text(encoding="utf-8"))
    return episodes, metas, quality, qverdicts, qmapping


def quality_by_task(qverdicts, qmapping) -> Dict[str, Dict[str, int]]:
    """arm -> task -> raw 0-3 score."""
    out: Dict[str, Dict[str, int]] = defaultdict(dict)
    for key, verdict in qverdicts.items():
        for letter, score in (verdict.get("scores") or {}).items():
            arm = qmapping.get(key, {}).get(letter)
            if arm:
                out[arm][key] = score
    return out


def common_subset(episodes: Dict[str, List[dict]]) -> List[str]:
    """Tasks every arm accomplished — the only like-for-like quality comparison.

    Quality is judged on passing attempts, so an arm that failed half the suite
    is scored only on the half it managed, which is typically the easier half.
    Comparing that percentage against an arm scored on everything rewards
    failure. Restricting to the tasks all arms completed removes the effect;
    the cost is a smaller n, which is stated wherever the figure appears.
    """
    sets = [{e["task"] for e in eps if e["accomplished"]} for eps in episodes.values()]
    return sorted(set.intersection(*sets)) if sets else []


def _secs(value) -> str:
    """Wall clock, or an em dash when the run cannot supply one.

    A filtered run has no attributable wall clock — apportioning the original
    across a subset would invent a number — so it stores null rather than lie.
    """
    return f"{value:.0f}s" if isinstance(value, (int, float)) else "—"


def _q(values, pct):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * pct))] if ordered else 0


def build(run: Path, arms_dir: str, reference: Dict = None) -> str:
    episodes, metas, quality, qverdicts, qmapping = load(run, arms_dir)
    if not episodes:
        raise SystemExit(f"no arm produced episodes.json under {run / arms_dir}")

    qarms = quality.get("arms") or {}
    qbt = quality_by_task(qverdicts, qmapping)
    common = common_subset(episodes)
    arms = order_arms(episodes, metas)
    tasks = sorted({e["task"] for eps in episodes.values() for e in eps})
    L: List[str] = []
    A = L.append

    A(f"# {run.name} — task results")
    A("")
    A(
        f"**{len(tasks)} tasks, {len(arms)} arms.** Each arm ran a real agent loop "
        "to completion in an isolated workspace. Tools executed, files changed, "
        "and a command decided whether the task was done. Arms differ in the "
        "**harness** (GAIA or Claude Code) and the **model** — both are named "
        "in every table, because a difference between harnesses is not a "
        "difference between models."
    )
    A("")
    A(
        "> **Correctness is an exit code, not an opinion.** A task passes when "
        "its verifier returns 0 — the tests run, the number checks out, the "
        "required content is there. Quality is judged separately and only for "
        "attempts that passed, so the two are never blended."
    )
    A("")

    # -------------------------------------------------------------- headline
    A("## Did the work get done")
    A("")
    A(
        "| arm | harness | model | accomplished | quality (same tasks) "
        "| quality (all it passed) | steps (median) | wall clock |"
    )
    A("|---|---|---|---:|---:|---:|---:|---:|")
    for arm in arms:
        eps = episodes[arm]
        q = qarms.get(arm)
        qall = "—"
        if q:
            mark = "" if q["tasks_scored"] >= MIN_FOR_QUALITY_SCORE else " †"
            qall = f"{q['quality']:.0f} ({q['tasks_scored']}){mark}"
        sub = [qbt[arm][k] for k in common if k in qbt.get(arm, {})]
        qcom = f"**{100*sum(sub)/(len(sub)*3):.0f}**" if sub else "—"
        model = (metas.get(arm) or {}).get("model", "—")
        A(
            f"| **{arm}** | {harness_of(arm, metas)} | `{model}` "
            f"| **{sum(e['accomplished'] for e in eps)}/{len(eps)}** "
            f"| {qcom} | {qall} | {_med(e['steps'] for e in eps):.0f} "
            f"| {_secs(metas.get(arm, {}).get('wall_clock_s'))} |"
        )
    A("")
    A(
        "_**Harness and model are separate columns on purpose.** "
        "`GAIA-Claude-Opus-5` is *GAIA's agent loop driven by Opus*; "
        "`ClaudeCode-Opus-5` is *Claude Code's own loop* on the same task and "
        "the same model. A difference between those two rows is a difference "
        "between agent systems, not between models._"
    )
    A("")
    A(
        f"_**Read the first quality column, not the second.** It covers the "
        f"{len(common)} task(s) *every* arm accomplished, so all arms are scored "
        "on identical work. The second column scores each arm on whatever it "
        "managed — an arm that failed half the suite is graded only on the half "
        "it finished, which is usually the easier half, and its percentage rises "
        "as a result. Counts are in brackets; rows under "
        f"{MIN_FOR_QUALITY_SCORE} judged tasks are marked †._"
    )
    A("")
    if common:
        A("_Common subset: " + ", ".join(f"`{k}`" for k in common) + "._")
        A("")
    if len(common) < MIN_FOR_QUALITY_SCORE:
        # Said out loud rather than buried in a limits section. A column of
        # near-identical percentages invites a ranking that the sample cannot
        # support, and the reader has no way to know that from the numbers.
        A(
            f"> ⚠️ **Quality did not separate these arms.** The common subset is "
            f"{len(common)} task(s), where a single rubric point moves an arm "
            "double digits and most arms sit at or near the ceiling. Treat the "
            "quality columns as evidence that passing work was broadly "
            "acceptable — not as a ranking. **Correctness is what separated the "
            "arms in this run**, and it is measured, not judged."
        )
        A("")

    # Fires only when the data shows it, so the observation can never outlive
    # the numbers that justified it.
    ranked_qual = sorted(
        (a for a in arms if [k for k in common if k in qbt.get(a, {})]),
        key=lambda a: -sum(qbt[a][k] for k in common if k in qbt[a]),
    )
    # The most-correct arm landing in the bottom half on quality is the signal.
    under_test = [a for a in arms if harness_of(a, metas) != "Claude Code"]
    top_gaia = under_test[0] if under_test else arms[0]
    if len(ranked_qual) >= 4 and top_gaia in ranked_qual[len(ranked_qual) // 2 :]:
        A(
            f"> **The arm that accomplished the most — `{top_gaia}` — rates "
            f"*lowest* on the quality of that work, and `{ranked_qual[0]}` rates "
            "highest while finishing fewer tasks.** Worth understanding before "
            "acting on either column. The rubric penalises scope creep and "
            "over-building, and the arms that finish everything also do the most "
            "unrequested extra. Whether that is a flaw or a virtue depends on "
            "whether you would rather have the task done or have it done "
            "narrowly — but it does mean **the two columns must never be "
            "averaged into a single score**, and it is a hypothesis this sample "
            "is too small to confirm."
        )
        A("")

    # ------------------------------------------------ harness vs harness
    pairs = []
    for arm in arms:
        model = (metas.get(arm) or {}).get("model")
        if harness_of(arm, metas) != "GAIA" or not model:
            continue
        twin = next(
            (
                a
                for a in arms
                if harness_of(a, metas) == "Claude Code"
                and (metas.get(a) or {}).get("model") == model
            ),
            None,
        )
        if twin:
            pairs.append((model, arm, twin))
    if pairs:
        A("## GAIA against Claude Code, same model, same tasks")
        A("")
        A(
            "The only comparison here that isolates the **agent system**. Both "
            "rows of a pair run the identical task in an identical sandbox "
            "against the identical verifier, driven by the same model — so what "
            "differs is the loop, the prompt and the tool surface."
        )
        A("")
        A(
            "| model | harness | accomplished | steps (median) | calls/task "
            "| failed calls | tokens in | cost |"
        )
        A("|---|---|---:|---:|---:|---:|---:|---:|")
        for model, g, cc in pairs:
            for arm in (g, cc):
                eps = episodes[arm]
                calls = sum(len(e["tool_calls"]) for e in eps)
                failed = sum(e["tool_failures"] for e in eps)
                cost = sum(e.get("cost_usd") or 0 for e in eps)
                tin = sum(e["tokens_in"] for e in eps)
                A(
                    f"| `{model}` | **{harness_of(arm, metas)}** "
                    f"| {sum(e['accomplished'] for e in eps)}/{len(eps)} "
                    f"| {_med(e['steps'] for e in eps):.0f} "
                    f"| {calls/len(eps):.1f} "
                    f"| {failed} ({100*failed/calls if calls else 0:.0f}%) "
                    f"| {tin:,} "
                    f"| {('$%.2f' % cost) if cost else '—'} |"
                )
        A("")
        A(
            "_Cost is reported only where the harness reports it. Claude Code "
            "returns real billed dollars; the GAIA arms run on an internal "
            "gateway that bills separately, so `—` means **not reported**, "
            "never free. Token counts include cache reads and writes, which are "
            "real input the model processed._"
        )
        A("")

    # -------------------------------------------------- why quality differed
    if qbt:
        A("## Why the quality scores differ")
        A("")
        A(
            "The per-task scores with the judge's own reasoning, so a headline "
            "percentage can always be traced to the specific work that produced "
            "it. A one-point gap on one task moves an arm ~8 points overall — "
            "read the notes before the numbers."
        )
        A("")
        A("| task | " + " | ".join(a.replace("GAIA-", "") for a in arms) + " |")
        A("|---|" + "---:|" * len(arms))
        for key in sorted(qverdicts):
            cells = [str(qbt[a][key]) if key in qbt.get(a, {}) else "—" for a in arms]
            A(f"| `{key}` | " + " | ".join(cells) + " |")
        A("")
        for key in sorted(qverdicts):
            note = (qverdicts[key].get("note") or "").strip()
            if note:
                letters = {
                    ltr: arm.replace("GAIA-", "")
                    for ltr, arm in (qmapping.get(key) or {}).items()
                }
                # The judge writes about A/B/C. Substituting the arm names back
                # in is what makes the note readable at all — and it is the only
                # step where the blind mapping is undone.
                for ltr, arm in sorted(letters.items()):
                    note = re.sub(rf"\b{ltr}\b", f"**{arm}**", note)
                A(f"- **`{key}`** — {note}")
        A("")

    # ------------------------------------------------------------- per task
    A("## Which tasks were accomplished")
    A("")
    A("| task | use case | " + " | ".join(arms) + " |")
    A("|---|---|" + "---|" * len(arms))
    for key in tasks:
        row, uc = [], ""
        for arm in arms:
            e = next((x for x in episodes[arm] if x["task"] == key), None)
            uc = uc or (e or {}).get("use_case", "")
            if not e:
                row.append("–")
            elif e["accomplished"]:
                row.append(f"✅ {e['steps']}")
            elif e["crashed"]:
                row.append("💥")
            else:
                row.append(f"❌ {e['steps']}")
        A(f"| `{key}` | {uc} | " + " | ".join(row) + " |")
    A("")
    A(
        "_✅/❌ followed by the number of steps the attempt used. 💥 is a crash or timeout._"
    )
    A("")

    # ------------------------------------------------------------- behaviour
    A("## How the work was done")
    A("")
    A(
        "Two arms with the same pass rate are not the same result. This is the "
        "part that informs harness design."
    )
    A("")
    A("| arm | calls/task | failed calls | repeated calls | distinct tools | errors |")
    A("|---|---:|---:|---:|---:|---:|")
    for arm in arms:
        eps = episodes[arm]
        calls = sum(len(e["tool_calls"]) for e in eps)
        failed = sum(e["tool_failures"] for e in eps)
        A(
            f"| {arm} | {calls/len(eps):.1f} "
            f"| {failed} ({100*failed/calls if calls else 0:.0f}%) "
            f"| {sum(e['repeated_calls'] for e in eps)} "
            f"| {len({t for e in eps for t in e['distinct_tools']})} "
            f"| {sum(len(e['errors']) for e in eps)} |"
        )
    A("")
    A(
        "_**Repeated calls** are identical tool invocations the agent had "
        "already made — work it paid for twice. **Failed calls** are tool "
        "results that reported an error; a high count with a high pass rate "
        "means the agent recovered, which is itself a capability._"
    )
    A("")

    # --------------------------------------------------- checking their work
    #: Tools whose purpose is to confirm a result rather than produce one. An
    #: agent that never calls one is asserting rather than checking.
    VERIFYING = {
        "run_shell_command",
        "execute_python_file",
        "analyze_data_file",
        "query_specific_file",
    }
    A("## Did they check their own work")
    A("")
    A(
        "The share of tasks where the agent ran *something* to confirm its "
        "result — a test, a script, a data tool — rather than asserting it. "
        "This is a behavioural signal, not a score: an arm can be right without "
        "checking, but it is right by luck, and luck does not survive a harder "
        "input."
    )
    A("")
    A("| arm | tasks with a verifying call | on tasks it accomplished |")
    A("|---|---:|---:|")
    for arm in arms:
        eps = episodes[arm]
        done = [e for e in eps if e["accomplished"]]
        any_v = sum(bool(VERIFYING & set(e["distinct_tools"])) for e in eps)
        done_v = sum(bool(VERIFYING & set(e["distinct_tools"])) for e in done)
        A(
            f"| {arm} | {any_v}/{len(eps)} | "
            f"{done_v}/{len(done)} ({100*done_v/len(done) if done else 0:.0f}%) |"
        )
    A("")

    # ----------------------------------------------------------------- tools
    A("## What each arm reached for")
    A("")
    used = {
        arm: Counter(c["tool"] for e in episodes[arm] for c in e["tool_calls"])
        for arm in arms
    }
    every = sorted(
        {t for c in used.values() for t in c},
        key=lambda t: -sum(c[t] for c in used.values()),
    )
    A("| tool | " + " | ".join(arms) + " |")
    A("|---|" + "---:|" * len(arms))
    for tool in every:
        A(f"| `{tool}` | " + " | ".join(str(used[a][tool] or "—") for a in arms) + " |")
    A("")
    fails = defaultdict(Counter)
    for arm in arms:
        for e in episodes[arm]:
            for c in e["tool_calls"]:
                if c["failed"]:
                    fails[arm][c["tool"]] += 1
    worst = [(a, t, n) for a, c in fails.items() for t, n in c.items()]
    if worst:
        A(
            "Tools that failed, by arm: "
            + ", ".join(
                f"`{t}` ×{n} ({a})"
                for a, t, n in sorted(worst, key=lambda x: -x[2])[:8]
            )
            + "."
        )
        A("")

    # ------------------------------------------------------------------ cost
    A("## Cost, latency and context")
    A("")
    A("| arm | tokens in | tokens out | median task | slowest task |")
    A("|---|---:|---:|---:|---:|")
    estimated = []
    for arm in arms:
        eps = episodes[arm]
        tin, tout = sum(e["tokens_in"] for e in eps), sum(e["tokens_out"] for e in eps)
        # Only the Claude path reports usage from the backend. Elsewhere the
        # input figure is the recorder's own tiktoken count and the output
        # figure does not exist at all. Marking which is which matters: the two
        # use different tokenizers, so they are comparable in trend, not to the
        # token. A bare "0" for output would read as a free run.
        local = (metas.get(arm, {}).get("transport") or "") != "anthropic"
        if local:
            estimated.append(arm)
        A(
            f"| {arm} | {tin:,}{'~' if local else ''} "
            f"| {f'{tout:,}' if tout else '—'} "
            f"| {_med(e['wall_clock_s'] for e in eps):.0f}s "
            f"| {max(e['wall_clock_s'] for e in eps):.0f}s |"
        )
    A("")
    if estimated:
        A(
            "_`~` marks a **locally estimated** input count ("
            + ", ".join(f"`{a}`" for a in estimated)
            + "). Only the Claude path surfaces backend-reported usage; "
            "elsewhere the recorder counts with its own tokenizer, and output "
            "tokens are not captured at all — hence `—`, not zero. Compare "
            "these across arms as trends, not to the token._"
        )
        A("")

    # ------------------------------------------------------- context and cache
    measured = [a for a in arms if any(e["input_tokens_by_call"] for e in episodes[a])]
    if measured:
        A("### Where the context actually goes")
        A("")
        A(
            "Measured per LLM call by the agent's own turn recorder — not "
            "inferred from tool-output sizes, which understates the truth "
            "several-fold."
        )
        A("")
        A(
            "| arm | fixed prefill | first call | last call | grew by | "
            "cache hit after first |"
        )
        A("|---|---:|---:|---:|---:|---:|")
        for arm in measured:
            eps = [e for e in episodes[arm] if e["input_tokens_by_call"]]
            fixed = _med(e["fixed_prefill_tokens"] for e in eps)
            first = _med(e["input_tokens_by_call"][0] for e in eps)
            last = _med(e["input_tokens_by_call"][-1] for e in eps)
            later = [h for e in eps for h in e["cache_hit_by_call"][1:]]
            A(
                f"| {arm} | {fixed:,.0f} | {first:,.0f} | {last:,.0f} "
                f"| +{last-first:,.0f} "
                f"| {100*sum(later)/len(later) if later else 0:.0f}% |"
            )
        A("")
        eps0 = [e for e in episodes[measured[0]] if e["input_tokens_by_call"]]
        share = (
            100
            * _med(e["fixed_prefill_tokens"] for e in eps0)
            / max(1.0, _med(e["input_tokens_by_call"][0] for e in eps0))
        )
        A(
            f"**The system prompt and tool schemas are ~{share:.0f}% of the very "
            "first request**, and a whole task adds only a few thousand tokens "
            "on top. These are short tasks, so this is the fixed cost of *having* "
            "a broad agent, paid on every call before the work starts. It is the "
            "strongest architectural signal in this run: the lever is the size of "
            "that fixed prefix and how well it caches, not the conversation."
        )
        A("")
    if quality:
        ju = quality.get("judge_usage") or {}
        A(
            f"**Quality judging** used {ju.get('in', 0):,} in / {ju.get('out', 0):,} "
            f"out on `{quality.get('judge_model')}`, in "
            f"{quality.get('judge_wall_clock_s', '?')}s."
        )
        A("")

    # --------------------------------------------- what can run unattended
    from .enterprise import (
        AUTONOMY_LEVELS,
        FAMILIES,
        MIN_ATTEMPTS_FOR_A_VERDICT,
        UNATTENDED_PASS_BAR,
        autonomy_table,
    )

    A("## What could run unattended, and what still needs a human")
    A("")
    A(
        "The question behind every deployment decision. It is **not** the same "
        "as asking which use case the agent is best at: a task it performs "
        "perfectly still needs a human if nobody can tell when it goes wrong, "
        "or if being wrong cannot be undone."
    )
    A("")
    A("### The four levels")
    A("")
    A("| level | what it means | when it is justified |")
    A("|---|---|---|")
    for level in AUTONOMY_LEVELS:
        A(f"| **{level.name}** | {level.definition} | {level.requires} |")
    A("")
    A("### How each use case is placed")
    A("")
    A(
        "Three gates, applied in order. Each can only move a use case **down** "
        "the ladder, never up — so a high pass rate can never override an "
        "irreversible action."
    )
    A("")
    A(
        "1. **Is it reversible?** If a mistake cannot be undone by re-running "
        "— a published release, a rewritten history, a leaked secret — it is "
        "*Gated*, whatever the measured pass rate says."
    )
    A(
        "2. **Can a machine check it?** If no command can separate success from "
        "failure, the agent cannot know it succeeded either, so a human has to "
        "read the result."
    )
    A(
        f"3. **Is there enough evidence?** A verdict needs at least "
        f"{MIN_ATTEMPTS_FOR_A_VERDICT} attempts and a pass rate of "
        f"{UNATTENDED_PASS_BAR:.0f}%. Below either, it is *Checked after*."
    )
    A("")
    rows = autonomy_table(episodes, metas)
    A("| use case | family | placement | best measured | attempts | why |")
    A("|---|---|---|---:|---:|---|")
    for r in rows:
        A(
            f"| `{r['use_case']}` | {r['family']} | **{r['verdict']}** "
            f"| {r['pass_rate']:.0f}% | {r['n']} | {r['why']} |"
        )
    A("")
    from .enterprise import autonomy_rollup

    counts = (reference or {}).get("use_case_counts") or {}
    rollup, span = autonomy_rollup(rows, counts)
    A("### How much of the real workload each level covers")
    A("")
    A(
        "**Share of measured work** weights each use case by how often it "
        "actually occurred in the recorded sessions, so it answers the "
        "operational question rather than the academic one: *of the work we do, "
        "how much could run at this level today?* Levels reaching nothing are "
        "still listed — a zero is a finding, and hiding the row would read as "
        "'not assessed'."
    )
    A("")
    A(
        "| level | use cases | % of use cases | % of measured work | % of all recorded work | examples |"
    )
    A("|---|---:|---:|---:|---:|---|")
    for r in rollup:
        ex = ", ".join(f"`{e}`" for e in r["examples"]) or "—"
        A(
            f"| **{r['level']}** | {r['use_cases']} "
            f"| {r['share_of_use_cases']:.0f}% "
            f"| **{r['share_of_measured_work']:.0f}%** "
            f"| {r['share_of_all_work']:.0f}% | {ex} |"
        )
    A("")
    if span["corpus_volume"]:
        A(
            f"_The last two columns differ because this suite covers "
            f"**{span['coverage']:.0f}% of recorded work** "
            f"({span['measured_volume']:,} of {span['corpus_volume']:,} tasks). "
            "The remainder is use cases with no task here at all — they are "
            "unplaced, not unattended, and the gap between the two columns is "
            "exactly how much of your workload this table cannot yet speak to._"
        )
        A("")

    unattended = [r for r in rows if r["verdict_key"] == "unattended"]
    thin = [r for r in rows if "too little evidence" in r["why"]]
    gated = [r for r in rows if r["verdict_key"] == "gated"]
    if unattended:
        A(
            f"**{len(unattended)} use case(s) reach *Unattended*.** Everything "
            "else needs a human somewhere in the loop."
        )
    else:
        A(
            "**Nothing reaches *Unattended* on this run, and that is an honest "
            "answer rather than a pessimistic one.** "
            f"{len(thin)} of {len(rows)} use cases are held back purely by "
            "sample size — this suite runs one or two tasks each, so a '100%' "
            "is one or two lucky attempts. **Increasing tasks per use case is "
            "the single cheapest change that would move this table**, cheaper "
            f"than any model upgrade. The {len(gated)} *Gated* rows will not "
            "move: they are placed there by consequence, not performance."
        )
    A("")

    A("### The families these roll up to")
    A("")
    A("| family | share of real work | default placement | what a wrong answer costs |")
    A("|---|---:|---|---|")
    fam_share = {}
    if reference and reference.get("use_case_counts"):
        from .enterprise import shares as _shares

        fam_share = {
            r["key"]: r["share"] for r in _shares(reference["use_case_counts"])
        }
    from .enterprise import LEVEL_BY_KEY

    for fam in FAMILIES:
        share = f"{fam_share[fam.key]:.0f}%" if fam.key in fam_share else "—"
        A(
            f"| **{fam.name}** | {share} "
            f"| {LEVEL_BY_KEY[fam.autonomy].name} "
            f"| {fam.blast_radius.replace('**', '')} |"
        )
    A("")

    # ----------------------------------------------------- routing policy
    from .routing import CONFIDENT_ATTEMPTS, policy, rollup

    route_rows = policy(episodes, metas, reference)
    if route_rows:
        A("## A routing policy, derived from these results")
        A("")
        A(
            "One default model is wrong in both directions at once: too "
            "expensive for work a small model handles perfectly, not strong "
            "enough for work that defeats one. The rule below is deliberately "
            "plain — **for each use case, the cheapest model that got it right "
            "every time it tried** — with two overrides."
        )
        A("")
        A(
            "1. **Irreversible work does not route down.** Publishing a release "
            "or rewriting history pins to the strongest model; saving tokens "
            "there is a bad trade."
        )
        A(
            "2. **Nothing clean means escalate and say so.** A use case no "
            "model passed cleanly goes to the strongest model and is reported "
            "as unresolved rather than quietly assigned."
        )
        A("")
        roll = rollup(route_rows)
        A("| model | use cases | share of real work | rows with thin evidence |")
        A("|---|---:|---:|---:|")
        for r in roll:
            A(
                f"| `{r['model']}` | {r['use_cases']} | **{r['share']:.1f}%** "
                f"| {r['low_confidence']} |"
            )
        A("")
        cheap = roll[0] if roll else {}
        weak = sum(1 for r in route_rows if r["confidence"] in ("low", "none"))
        A(
            f"> ⚠️ **Read this as a hypothesis, not a deployment plan.** "
            f"{weak} of {len(route_rows)} rows rest on fewer than "
            f"{CONFIDENT_ATTEMPTS} attempts, so a 'clean pass' can be one lucky "
            f"run. The headline — that around **{cheap.get('share', 0):.0f}% of "
            f"real work might run on `{cheap.get('model', '?')}`** — is exactly "
            "the claim that needs more tasks per use case before anyone acts on "
            "it. It is also the strongest argument for building them."
        )
        A("")
        A("| use case | share of work | recommended | confidence | why |")
        A("|---|---:|---|---|---|")
        for r in route_rows:
            mark = "" if r["confidence"] == "good" else " ⚠"
            A(
                f"| `{r['use_case']}` | {r['share_of_work']:.1f}% "
                f"| `{r['recommend']}` | {r['confidence']}{mark} | {r['why']} |"
            )
        A("")
        A(
            "_`policy` confidence means the row was set by the irreversibility "
            "rule rather than by measurement, so more data will not move it. "
            "Claude Code is absent because it is the reference harness we "
            "measure against, not somewhere GAIA can route a request._"
        )
        A("")

    # ------------------------------------------- against real recorded work
    if reference:
        A("## How hard these tasks are, against real recorded work")
        A("")
        A(
            f"**Reference: {reference['source']}** — "
            f"{reference['human_tasks']:,} human-originated tasks across "
            f"{reference['sessions']} sessions. These were never run for a "
            "benchmark and nothing verified them; they are observational, and "
            "they are the only honest way to ask whether this suite is hard "
            "enough to be worth passing."
        )
        A("")
        bench_steps = sorted(e["steps"] for eps in episodes.values() for e in eps)
        bmed = _med(bench_steps)
        bmax = max(bench_steps) if bench_steps else 0
        r = reference["steps"]
        A(
            "| steps per task | this suite (measured) | Claude Code sessions (recorded) |"
        )
        A("|---|---:|---:|")
        A(f"| median | {bmed:.0f} | **{r['median']:.0f}** |")
        A(f"| p90 | {_q(bench_steps, 0.90):.0f} | **{r['p90']:.0f}** |")
        A(f"| max | {bmax} | **{r['max']}** |")
        A("")
        pcts = reference.get("percentile_of") or {}
        near = min(pcts, key=lambda k: abs(int(k) - bmax)) if pcts else None
        if near:
            A(
                f"**The hardest attempt in this suite used {bmax} steps, which "
                f"lands around the {pcts[near]:.0f}th percentile of real tasks** "
                f"— so roughly {100 - pcts[near]:.0f}% of genuine work is longer "
                "than anything measured here. "
                f"{reference['share_over_20_steps']}% of real tasks exceed 20 "
                f"steps and {reference['share_over_50_steps']}% exceed 50."
            )
            A("")
        A(
            f"_{reference['sessions_using_subagents']} of "
            f"{reference['sessions']} recorded sessions delegated to subagents. "
            "GAIA has no delegation primitive, so no arm here can use one and "
            "the suite is blind to that dimension entirely._"
        )
        A("")

    # ---------------------------------------------------------------- limits
    A("## What these numbers cannot tell you")
    A("")
    A("| limit | why it matters |")
    A("|---|---|")
    A(
        f"| {len(tasks)} tasks | Enough to separate arms that differ a lot. Not "
        "enough for a confident per-use-case figure — one task is one data point. |"
    )
    A(
        "| Tasks are small and self-contained | They finish in minutes, which is "
        "what makes them runnable often. They do not test work spanning a large "
        "existing codebase, long sessions, or recovery over hours. |"
    )
    A(
        "| A verifier checks the outcome, not the route | An arm that reaches the "
        "right answer by a wasteful path still passes. That is why steps and "
        "repeated calls are reported beside the pass rate, not instead of it. |"
    )
    A(
        "| Quality is judged only on passing attempts | It answers *given it "
        "worked, is it good*. It is silent on arms that failed, and its sample "
        "is whatever that arm accomplished. |"
    )
    A(
        "| One attempt per task | Agent runs are not deterministic. A single pass "
        "or fail carries real variance; repeat runs are needed to call a small "
        "difference real. |"
    )
    A("")
    return "\n".join(L)


def _pass_rate(eps: List[dict]) -> float:
    return sum(e["accomplished"] for e in eps) / len(eps) if eps else 0.0


def _med(values) -> float:
    vals = list(values)
    return median(vals) if vals else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--arms", default="_arms")
    ap.add_argument(
        "--reference",
        type=Path,
        default=None,
        help="corpus_reference JSON: recorded sessions used as a real-world yardstick",
    )
    a = ap.parse_args()
    reference = (
        json.loads(a.reference.read_text(encoding="utf-8"))
        if a.reference and a.reference.exists()
        else None
    )
    out = a.run / "report.md"
    out.write_text(build(a.run, a.arms, reference), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
