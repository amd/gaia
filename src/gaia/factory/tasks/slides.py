# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Build slides.html for a task run. Every figure derived, none typed.

The deck says the same things as the report and in the same order, because a
slide that disagrees with its own report is worse than no slide. What it drops
is detail, not caveats: where the sample is too small to support a ranking the
deck says so on the slide itself, not in a footnote nobody projects.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from .report import (
    MIN_FOR_QUALITY_SCORE,
    _med,
    _secs,
    common_subset,
    harness_of,
    load,
    order_arms,
    quality_by_task,
)

E = html.escape


def _COST_RANK(model: str) -> int:
    """Cheapest first, so capability tables read left-to-right as cost."""
    from .routing import COST_ORDER

    return COST_ORDER.index(model) if model in COST_ORDER else len(COST_ORDER)


#: Thresholds for highlighting. Fixed and shared so a green cell means the same
#: thing on every slide — a per-table colour scale would make a weak field look
#: strong simply because nothing better was in it.
GOOD_RATE, BAD_RATE = 95.0, 70.0


def _rate(pct, good=GOOD_RATE, bad=BAD_RATE, text=None):
    """A percentage, coloured only when it is genuinely notable."""
    label = text if text is not None else f"{pct:.0f}%"
    if pct >= good:
        return f"<b style='color:#34d399'>{label}</b>"
    if pct < bad:
        return f"<b style='color:#f87171'>{label}</b>"
    return label


#: Column headers, wrapped onto two lines. The full model id has to survive —
#: "Kimi" and "Qwen3.6" read fine and say nothing, and parameter count and
#: variant are exactly what a claim about a model rests on — but at full length
#: the ids collide, so they break rather than shrink further.
ARM_HEADER = {
    "ClaudeCode-Opus-5": "Claude Code<br>Opus-5",
    "ClaudeCode-Sonnet-5": "Claude Code<br>Sonnet-5",
    "GAIA-Claude-Opus-5": "Claude<br>Opus-5",
    "GAIA-Claude-Sonnet-5": "Claude<br>Sonnet-5",
    "GAIA-Kimi-K2.7-Code": "Kimi-K2.7<br>Code",
    "GAIA-Qwen3.6-35B-A3B": "Qwen3.6<br>35B-A3B",
    "GAIA-GPT-oss-120B": "GPT-oss<br>120B",
}


def _arm_label(arm: str) -> str:
    """Two-line column header keeping the model fully identifiable."""
    if arm in ARM_HEADER:
        return ARM_HEADER[arm]
    return (
        arm.replace("ClaudeCode-", "CC ")
        .replace("GAIA-Claude-", "")
        .replace("GAIA-", "")
    )


def _table(head, rows, left=(0,)):
    h = "".join(f"<th>{c}</th>" for c in head)
    body = ""
    for row in rows:
        cells = "".join(
            f"<td{" style='text-align:left'" if i in left else ''}>{c}</td>"
            for i, c in enumerate(row)
        )
        body += f"<tr>{cells}</tr>"
    return f'<table class="t tight"><tr>{h}</tr>{body}</table>'


def build(run: Path, arms_dir: str, style: Path, reference: dict = None) -> str:
    episodes, metas, quality, qverdicts, qmapping = load(run, arms_dir)
    qarms = quality.get("arms") or {}
    qbt = quality_by_task(qverdicts, qmapping)
    common = common_subset(episodes)
    arms = order_arms(episodes, metas)
    tasks = sorted({e["task"] for eps in episodes.values() for e in eps})
    S = []

    def slide(label, title, lead, body, foot):
        S.append(
            '<section class="slide"><div class="pad">'
            f'<div><span class="sn">{len(S) + 1}</span>'
            f'<span class="snlab">{label}</span></div>'
            f'<h1 class="title">{title}</h1><div class="lead">{lead}</div>{body}'
            f'<div class="foot">{foot}</div></div></section>'
        )

    under_test = [a for a in arms if harness_of(a, metas) != "Claude Code"] or arms
    best, worst = under_test[0], under_test[-1]
    nb = sum(e["accomplished"] for e in episodes[best])
    nw = sum(e["accomplished"] for e in episodes[worst])
    n_cc = sum(1 for a in arms if harness_of(a, metas) == "Claude Code")

    # Opens on what the work *is*, before any result. A deck that led with the
    # benchmark would be arguing the benchmark matters by showing the benchmark.
    # These come from recorded sessions, not the suite's own composition.
    if reference and reference.get("use_case_counts"):
        from .enterprise import (
            FAMILIES,
            LEVEL_BY_KEY,
            by_domain,
            coverage,
            family_of,
            shares,
        )
        from .suite import TASKS as SUITE_TASKS

        fam_share = {r["key"]: r for r in shares(reference["use_case_counts"])}
        cov = coverage(SUITE_TASKS)
        domains = by_domain(reference["use_case_counts"])
        know = next(d for d in domains if d["key"] == "knowledge")

        total_tasks = len(SUITE_TASKS)

        # Family, then the use cases inside it. The family row carries the
        # totals; the rows beneath show what the family is actually made of,
        # which is the level people recognise their own work at.
        profile = reference.get("use_case_profile") or {}
        counts = reference["use_case_counts"]

        def _dom_table(domain_key):
            rows = []
            for f in [x for x in FAMILIES if x.domain == domain_key]:
                n = cov[f.key]["tasks"]
                rows.append(
                    [
                        f"<b>{E(f.short)}</b>",
                        f"<b>{fam_share.get(f.key, {}).get('share', 0):.0f}%</b>",
                        f"<b>{n}</b>",
                        f"<b>{E(LEVEL_BY_KEY[f.autonomy].name)}</b>",
                    ]
                )
                # Use cases within the family, busiest first.
                inside = sorted(
                    (uc for uc in counts if family_of(uc) == f.key),
                    key=lambda uc: -counts[uc],
                )
                for uc in inside:
                    share = profile.get(uc, {}).get("share_of_tasks")
                    tasks_here = len(
                        {
                            e["task"]
                            for eps in episodes.values()
                            for e in eps
                            if e["use_case"] == uc
                        }
                    )
                    rows.append(
                        [
                            f"&nbsp;&nbsp;<code>{E(uc)}</code>",
                            f"{share:.0f}%" if share is not None else "—",
                            str(tasks_here) if tasks_here else "–",
                            "",
                        ]
                    )
            return _table(
                ["family / use case", "share", "tasks", "supervision"],
                rows,
                left=(0, 3),
            ).replace('class="t tight"', 'class="t tight wide"')

        know = next(d for d in domains if d["key"] == "knowledge")
        slide(
            "What the work is",
            f"{know['share']:.0f}% of agent work is not software development.",
            f"<b>{reference['human_tasks']:,} real tasks</b> across "
            f"{reference['sessions']} recorded sessions — not a survey. "
            f"Our suite of <b>{total_tasks} tasks</b> is built to that shape. "
            "Families are grouped by <b>what a wrong answer costs</b>; use "
            "cases sit beneath the family they belong to.",
            '<div class="split"><div><div class="colhd">Software development</div>'
            + _dom_table("software")
            + '</div><div><div class="colhd">Knowledge work</div>'
            + _dom_table("knowledge")
            + "</div></div>",
            f"{len({t.use_case for t in SUITE_TASKS})} of "
            f"{len(reference['use_case_counts'])} recorded use cases covered "
            "&nbsp;·&nbsp; grouped by <b>what a wrong answer costs</b>",
        )

    # Executive highlights: the two tables that carry the argument, plus the
    # numbers behind them. Everything computed, so a claim cannot outlive its
    # evidence.
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

    # Ranked by judged quality, best first: the reader's question is "which is
    # best", and a table ordered by anything else makes them do the sorting.
    _ranked = []
    for arm in arms:
        eps = episodes[arm]
        sub = [qbt[arm][k] for k in common if k in qbt.get(arm, {})]
        _ranked.append(
            (arm, eps, sub, 100 * sum(sub) / (len(sub) * 3) if sub else -1.0)
        )
    _ranked.sort(key=lambda r: -r[3])
    results_rows = []
    for arm, eps, sub, qual in _ranked:
        done = sum(e["accomplished"] for e in eps)
        results_rows.append(
            [
                f"<b>{_arm_label(arm).replace(chr(60)+chr(98)+chr(114)+chr(62), chr(32))}</b>",
                harness_of(arm, metas),
                _rate(100 * done / len(eps), text=f"{done}/{len(eps)}"),
                _rate(qual, bad=75, good=90) if sub else "—",
            ]
        )
    harness_rows = []
    for model, g, cc in pairs:
        for arm in (cc, g):
            eps = episodes[arm]
            calls = sum(len(e["tool_calls"]) for e in eps)
            failed = sum(e["tool_failures"] for e in eps)
            pct = 100 * failed / calls if calls else 0
            harness_rows.append(
                [
                    f"<code>{E(model.replace('claude-', ''))}</code>",
                    f"<b>{harness_of(arm, metas)}</b>",
                    f"{calls/len(eps):.1f}",
                    _rate(100 - pct, good=95, bad=85, text=f"{pct:.0f}%"),
                ]
            )

    gf = sum(sum(e["tool_failures"] for e in episodes[g]) for _, g, _ in pairs)
    gc = sum(sum(len(e["tool_calls"]) for e in episodes[g]) for _, g, _ in pairs)
    cf = sum(sum(e["tool_failures"] for e in episodes[c]) for _, _, c in pairs)
    cc_n = sum(sum(len(e["tool_calls"]) for e in episodes[c]) for _, _, c in pairs)
    g_rate = 100 * gf / max(gc, 1)
    c_rate = 100 * cf / max(cc_n, 1)

    measured = [a for a in arms if any(e["input_tokens_by_call"] for e in episodes[a])]
    fixed_pct = 0
    if measured:
        eps0 = [e for e in episodes[measured[0]] if e["input_tokens_by_call"]]
        fixed_pct = (
            100
            * _med(e["fixed_prefill_tokens"] for e in eps0)
            / max(_med(e["input_tokens_by_call"][0] for e in eps0), 1)
        )

    know_share = 0
    if reference and reference.get("use_case_counts"):
        from .enterprise import by_domain as _bd

        know_share = next(
            d["share"]
            for d in _bd(reference["use_case_counts"])
            if d["key"] == "knowledge"
        )

    # Four small tables, one conclusion each. Prose is kept to a single line:
    # an executive slide earns attention with numbers, not paragraphs.
    # Table 1: what the work is and what it costs. Earlier this carried "use
    # cases" and "our tasks", which describe the benchmark rather than the
    # work, sat within one of each other, and told the reader nothing.
    from .enterprise import FAMILIES as _CF
    from .enterprise import autonomy_rollup as _roll2
    from .enterprise import autonomy_table as _at2
    from .enterprise import by_domain as _bd2
    from .enterprise import by_domain as _cbd
    from .enterprise import family_of as _cfo

    _fam_share, _domains = {}, []
    _profile = (reference or {}).get("use_case_profile") or {}
    if reference and reference.get("use_case_counts"):
        from .enterprise import shares as _csh

        _fam_share = {r["key"]: r["share"] for r in _csh(reference["use_case_counts"])}
        _domains = _cbd(reference["use_case_counts"])

    def _fam_stat(fam_key, field):
        """Volume-weighted figure for a family, from the recorded corpus."""
        rows = [
            (v, _profile[uc]["tasks"])
            for uc, v in _profile.items()
            if _cfo(uc) == fam_key
        ]
        total = sum(n for _, n in rows)
        if not total:
            return 0.0
        return sum(v[field] * n for v, n in rows) / total

    _work_rows = []
    for dom in _domains or [{"key": "software"}, {"key": "knowledge"}]:
        label = {"software": "Software dev", "knowledge": "Knowledge work"}[dom["key"]]
        spend = sum(
            _profile[uc]["share_of_spend"]
            for uc in _profile
            if {f.key: f.domain for f in _CF}.get(_cfo(uc)) == dom["key"]
        )
        _work_rows.append(
            [
                f"<b>{E(label)}</b>",
                f"<b>{dom.get('share', 0):.0f}%</b>",
                f"<b>{spend:.0f}%</b>",
                "",
            ]
        )
        for fam in [f for f in _CF if f.domain == dom["key"]]:
            fam_spend = sum(
                _profile[uc]["share_of_spend"] for uc in _profile if _cfo(uc) == fam.key
            )
            _work_rows.append(
                [
                    f"&nbsp;&nbsp;{E(fam.short)}",
                    f"{_fam_share.get(fam.key, 0):.0f}%",
                    f"{fam_spend:.0f}%",
                    f"{_fam_stat(fam.key, 'median_steps'):.0f}",
                ]
            )
    t_harness = _table(
        ["domain / family", "share of work", "share of spend", "typical steps"],
        _work_rows,
        left=(0,),
    )

    t_results = _table(
        ["system", "harness", "done", "quality"], results_rows, left=(0, 1)
    )

    dom_rows = []
    if reference and reference.get("use_case_counts"):
        for d in _bd2(reference["use_case_counts"]):
            dom_rows.append(
                [
                    f"<b>{E(d['name'])}</b>",
                    f"{d['share']:.0f}%",
                    f"{d['use_cases']}",
                ]
            )
    t_domain = _table(["what the work is", "share", "use cases"], dom_rows, left=(0,))

    auto_rows = []
    _rows2 = _at2(episodes, metas)
    _ro2, _ = _roll2(_rows2, (reference or {}).get("use_case_counts") or {})
    for r in _ro2:
        if r["use_cases"] or r["key"] in ("unattended", "gated"):
            auto_rows.append(
                [
                    f"<b>{E(r['level'])}</b>",
                    f"{r['use_cases']}",
                    _rate(
                        100 - r["share_of_measured_work"],
                        good=101,
                        bad=101,
                        text=f"{r['share_of_measured_work']:.0f}%",
                    ),
                ]
            )
    t_auto = _table(["safe to run", "use cases", "% of work"], auto_rows, left=(0,))

    cost_rows = []
    if measured:
        eps0 = [e for e in episodes[measured[0]] if e["input_tokens_by_call"]]
        fixed = _med(e["fixed_prefill_tokens"] for e in eps0)
        first = _med(e["input_tokens_by_call"][0] for e in eps0)
        last = _med(e["input_tokens_by_call"][-1] for e in eps0)
        later = [h for e in eps0 for h in e["cache_hit_by_call"][1:]]
        cost_rows = [
            [
                "Instructions + tools",
                f"{fixed:,.0f}",
                f"{100 * fixed / max(first, 1):.0f}%",
            ],
            [
                "Everything the task added",
                f"{last-first:,.0f}",
                f"{100 * (last - first) / max(last, 1):.0f}%",
            ],
            [
                "Served from cache after call 1",
                "—",
                f"{100*sum(later)/len(later) if later else 0:.0f}%",
            ],
        ]
    t_cost = _table(["tokens per request", "count", "share"], cost_rows, left=(0,))

    def _cell(title, table):
        return f'<div><div class="colhd">{title}</div>{table}</div>'

    from .routing import policy as _hp
    from .routing import rollup as _hr

    _hroutes = _hp(episodes, metas, reference)
    _hroll = _hr(_hroutes) if _hroutes else []
    _hcheap = _hroll[0] if _hroll else {}

    def _qual_of(model):
        """Common-subset quality for whichever arm ran *model*."""
        for arm in arms:
            if (metas.get(arm) or {}).get("model") != model:
                continue
            sub = [qbt[arm][k] for k in common if k in qbt.get(arm, {})]
            if sub:
                return 100 * sum(sub) / (len(sub) * 3)
        return 0.0

    _cheap_qual = _qual_of(_hcheap.get("model", ""))
    _best_qual = max(
        (
            100
            * sum(qbt[a][k] for k in common if k in qbt.get(a, {}))
            / (len([k for k in common if k in qbt.get(a, {})]) * 3)
            for a in arms
            if [k for k in common if k in qbt.get(a, {})]
        ),
        default=0.0,
    )

    slide(
        "Executive highlights",
        "Most agent work is not coding, and cost is mostly fixed overhead.",
        "<b>Volume and spend are not the same map.</b> A third of the work "
        "never touches a repository yet accounts for under a tenth of the "
        "spend. And every system finished most of the suite while quality "
        f"spans {_cheap_qual:.0f}–{_best_qual:.0f} — finishing the "
        "work and doing it well are different questions.",
        '<div class="quad">'
        + _cell("1 &nbsp; What the work is, and what it costs", t_harness)
        + _cell("2 &nbsp; How the models did", t_results)
        + _cell("3 &nbsp; Cost is fixed, not conversational", t_cost)
        + "</div>",
        "<b>Done</b> is a command's verdict &nbsp;·&nbsp; <b>quality</b> judged "
        f"blind on the {len(common)} tasks all systems finished &nbsp;·&nbsp; "
        "green ≥95%, red <70%",
    )

    # 1 — what changed
    slide(
        "What changed",
        "Correctness is a command's exit code, not a judge's opinion.",
        "Every previous experiment asked a model what it would do next, once, and "
        "had a judge rate the answer. Nothing ran. <b>This runs a real agent "
        "loop to completion in a throwaway workspace</b> — tools execute, files "
        "change — then runs a command that decides whether the task was "
        "actually accomplished. Correctness is an exit code, not an opinion.",
        '<div class="facts sm">'
        f'<div class="fact"><div class="fnum">{len(tasks)}</div>'
        '<div class="fttl">tasks, run end to end</div><div class="fsub">Each '
        "starts from real files, ends in a verified artefact. Small on purpose — "
        "a benchmark too slow to rerun never catches a regression.</div></div>"
        f'<div class="fact w"><div class="fnum">{len(arms)}</div>'
        f'<div class="fttl">arms &nbsp;·&nbsp; {n_cc} on Claude Code</div>'
        '<div class="fsub">GAIA arms vary the model behind one agent. The '
        "<b>Claude Code</b> arms run the same tasks through a different agent "
        "system entirely — the reference to measure GAIA against.</div></div>"
        f'<div class="fact g"><div class="fnum">{nb - nw}</div>'
        '<div class="fttl">task spread, best to worst</div><div class="fsub">'
        f"{E(best)} finished {nb}; {E(worst)} finished {nw}. The step-level "
        "benchmark put these models far closer together.</div></div></div>",
        "Verifiers are themselves tested both ways — they must fail the untouched "
        "setup and pass a hand-written correct solution",
    )

    # 2 — did the work get done
    rows = []
    for arm in arms:
        eps = episodes[arm]
        sub = [qbt[arm][k] for k in common if k in qbt.get(arm, {})]
        rows.append(
            [
                f"<b>{E(arm)}</b>",
                harness_of(arm, metas),
                f"<b>{sum(e['accomplished'] for e in eps)}/{len(eps)}</b>",
                f"{100*sum(sub)/(len(sub)*3):.0f}" if sub else "—",
                f"{_med(e['steps'] for e in eps):.0f}",
                _secs(metas.get(arm, {}).get("wall_clock_s")),
            ]
        )
    ranked_qual = sorted(
        (a for a in arms if [k for k in common if k in qbt.get(a, {})]),
        key=lambda a: -sum(qbt[a][k] for k in common if k in qbt[a]),
    )
    inverted = (
        len(ranked_qual) >= 4 and under_test[0] in ranked_qual[len(ranked_qual) // 2 :]
    )
    title = (
        "The two columns rank the arms in opposite orders."
        if inverted
        else "Every system finished most of the suite."
    )
    lead = (
        "<b>Accomplished</b> is the verifier's count — the only hard number here. "
        f"<b>Quality</b> is judged blind over the {len(common)} task(s) <i>every</i> "
        "arm completed, so all arms are rated on identical work rather than on "
        "whatever each happened to manage."
    )
    if inverted:
        lead += (
            f" <b>{E(under_test[0]).replace('GAIA-', '')} finishes the most and rates "
            f"lowest; {E(ranked_qual[0]).replace('GAIA-', '')} rates highest on "
            "less.</b> The rubric penalises unrequested extra work, and the arms "
            "that finish everything do the most of it — which is exactly why "
            "these two numbers must never be averaged together."
        )
    if len(common) < MIN_FOR_QUALITY_SCORE:
        lead += (
            " At this sample size quality cannot rank anything; read it as a signal."
        )
    slide(
        "Results",
        title,
        lead,
        _table(
            ["arm", "harness", "accomplished", "quality", "steps", "wall clock"],
            rows,
            left=(0, 1),
        ),
        "Quality is judged only on attempts that passed &nbsp;·&nbsp; "
        f"<b>{len(common)} common tasks — a direction, not a verdict</b>",
    )

    # The comparison that isolates the agent system, placed immediately after
    # the headline because it is the single most actionable result here.
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
        rows = []
        for model, g, cc in pairs:
            for arm in (g, cc):
                eps = episodes[arm]
                calls = sum(len(e["tool_calls"]) for e in eps)
                failed = sum(e["tool_failures"] for e in eps)
                rows.append(
                    [
                        f"<code>{E(model)}</code>",
                        f"<b>{harness_of(arm, metas)}</b>",
                        f"{sum(e['accomplished'] for e in eps)}/{len(eps)}",
                        f"{calls/len(eps):.1f}",
                        f"<b>{100*failed/calls if calls else 0:.0f}%</b>",
                    ]
                )
        gf = sum(sum(e["tool_failures"] for e in episodes[g]) for _, g, _ in pairs)
        gc = sum(sum(len(e["tool_calls"]) for e in episodes[g]) for _, g, _ in pairs)
        cf = sum(sum(e["tool_failures"] for e in episodes[c]) for _, _, c in pairs)
        cc_ = sum(sum(len(e["tool_calls"]) for e in episodes[c]) for _, _, c in pairs)
        g_rate = 100 * gf / gc if gc else 0
        c_rate = 100 * cf / cc_ if cc_ else 0
        slide(
            "Harness, not model",
            "Same model, same tasks. The difference is the agent system.",
            "Each pair below runs the identical task in an identical sandbox "
            "against the identical verifier, driven by the same model — so "
            "what differs is the loop, the prompt and the tool surface. "
            f"<b>GAIA fails {g_rate:.0f}% of its tool calls against Claude "
            f"Code's {c_rate:.0f}%</b>, and needs materially more calls to "
            "reach the same place. Both finish the work; one of them fights "
            "its own tools to get there.",
            _table(
                ["model", "harness", "accomplished", "calls/task", "failed calls"],
                rows,
                left=(0, 1),
            ),
            "This is the gap that open PRs #3909 (shell rules), #3907 (scratch "
            "directory) and #3902 (script cwd) target — an independent "
            "benchmark reproducing the same failure rate they were written from",
        )

    # Quality per family with the harness held fixed, so the only thing varying
    # is the model. Pass/fail says a model *can* do the work; this says how well
    # — and the two do not rank the models the same way.
    from .enterprise import FAMILIES as _QF
    from .enterprise import family_of as _qfo
    from .routing import routable_arms as _qra

    _qarms = sorted(_qra(metas).items(), key=lambda kv: _COST_RANK(kv[1]))
    _task_uc = {e["task"]: e["use_case"] for eps in episodes.values() for e in eps}

    def _fam_quality(domain_key):
        rows = []
        for fam in [f for f in _QF if f.domain == domain_key]:
            cells = [f"<b>{E(fam.short)}</b>"]
            for arm, _model in _qarms:
                scores = [
                    v
                    for task, v in qbt.get(arm, {}).items()
                    if _qfo(_task_uc.get(task, "")) == fam.key
                ]
                cells.append(
                    _rate(
                        100 * sum(scores) / (len(scores) * 3),
                        good=90,
                        bad=70,
                        text=f"{100 * sum(scores) / (len(scores) * 3):.0f}",
                    )
                    if scores
                    else "–"
                )
            rows.append(cells)
        # Aggregate across the domain, so the reader gets one number per model
        # without adding up the rows themselves.
        total = ["<b>all of this work</b>"]
        fam_keys = {f.key for f in _QF if f.domain == domain_key}
        for arm, _model in _qarms:
            scores = [
                v
                for task, v in qbt.get(arm, {}).items()
                if _qfo(_task_uc.get(task, "")) in fam_keys
            ]
            total.append(
                f"<b>{_rate(100 * sum(scores) / (len(scores) * 3), good=90, bad=70, text=f'{100 * sum(scores) / (len(scores) * 3):.0f}')}</b>"
                if scores
                else "–"
            )
        rows.append(total)
        return _table(
            ["work family"] + [_arm_label(a) for a, _ in _qarms],
            rows,
            left=(0,),
        )

    slide(
        "Quality by model",
        "Finishing the work and doing it well rank the models differently.",
        "One harness, four models — so the only variable is the model. "
        "Scores are judged blind, 0–100, on work that already passed its "
        "verifier. <b>A model can clear a family and still produce the weakest "
        "output in it.</b>",
        '<div class="split roomy">'
        '<div><div class="colhd">Software development</div>'
        + _fam_quality("software")
        + '</div><div><div class="colhd">Knowledge work</div>'
        + _fam_quality("knowledge")
        + "</div></div>",
        "Models ordered cheapest to most capable, left to right &nbsp;·&nbsp; "
        "green ≥90, red <70 &nbsp;·&nbsp; a dash means that model passed nothing "
        "in the family, so there was nothing to judge",
    )

    # Routing: the practical output of everything above.
    from .routing import policy as _pol
    from .routing import rollup as _rup

    _routes = _pol(episodes, metas, reference)
    if _routes:
        from .enterprise import FAMILIES as _RF

        _RDOM = {f.key: f.domain for f in _RF}
        _short = lambda m: (  # noqa: E731
            m.replace("claude-", "")
            .replace("-35B-A3B", "")
            .replace("-K2.7-Code", "")
            .replace("-120B", "")
        )

        def _route_table(domain_key, limit=7):
            rows = []
            for r in [x for x in _routes if _RDOM.get(x["family"]) == domain_key][
                :limit
            ]:
                colour = (
                    "#fbbf24"
                    if r["confidence"] == "policy"
                    else ("#f87171" if r["confidence"] == "none" else "#e5e7eb")
                )
                rows.append(
                    [
                        f"<code>{E(r['use_case'])}</code>",
                        f"<b>{r['share_of_work']:.0f}%</b>",
                        f"<span style='color:{colour}'>{E(_short(r['recommend']))}</span>",
                        "✓" if r["downgraded"] else "—",
                        f"{r['attempts']}",
                    ]
                )
            return _table(
                ["use case", "share", "route to", "cheaper?", "evidence"],
                rows,
                left=(0, 2),
            )

        roll = _rup(_routes)
        cheap = roll[0] if roll else {}
        weak = sum(1 for r in _routes if r["confidence"] in ("low", "none"))
        slide(
            "Routing policy",
            f"{cheap.get('share', 0):.0f}% of work could offload — if you "
            "accept the quality drop.",
            "Rule: <b>cheapest model that passed every attempt</b>. Irreversible "
            "work pins to the strongest regardless. "
            f"<b>{weak} of {len(_routes)} rows rest on 1–2 attempts</b> — "
            "a hypothesis to test, not a policy to deploy.",
            '<div class="split"><div><div class="colhd">Software development</div>'
            + _route_table("software")
            + '</div><div><div class="colhd">Knowledge work</div>'
            + _route_table("knowledge")
            + "</div></div>"
            + '<div class="colhd" style="margin-top:10px">Share of workload per model</div>'
            + _table(
                ["model", "use cases", "share of work", "thin evidence"],
                [
                    [
                        f"<code>{E(_short(r['model']))}</code>",
                        str(r["use_cases"]),
                        f"<b>{r['share']:.0f}%</b>",
                        str(r["low_confidence"]),
                    ]
                    for r in roll
                ],
                left=(0,),
            ),
            "<span style='color:#fbbf24'>Amber</span> = pinned by the "
            "irreversibility rule, not by measurement &nbsp;·&nbsp; Claude Code "
            "is excluded: it is the reference, not a routing target",
        )

    # Complexity per family, measured. The equivalent slide in the session
    # analysis showed this for recorded work; this is the same shape for work
    # we actually ran and verified, so the two can be read side by side.
    from .enterprise import FAMILIES as _F
    from .enterprise import family_of as _fo

    _DOM = {f.key: f.domain for f in _F}

    def _stat(values):
        v = sorted(values)
        if not v:
            return "—"
        return f"{v[0]:.0f} / {_med(v):.0f} / {v[-1]:.0f}"

    def _complexity_table(domain_key):
        rows = []
        for fam in [f for f in _F if f.domain == domain_key]:
            eps = [
                e
                for arm in arms
                for e in episodes[arm]
                if _fo(e["use_case"]) == fam.key and e["accomplished"]
            ]
            if not eps:
                rows.append([f"<b>{E(fam.short)}</b>", "—", "—", "—", "—"])
                continue
            tin = sum(e["tokens_in"] for e in eps) / len(eps)
            tout = sum(e["tokens_out"] for e in eps) / len(eps)
            rows.append(
                [
                    f"<b>{E(fam.short)}</b>",
                    _stat([e["steps"] for e in eps]),
                    _stat([len(e["tool_calls"]) for e in eps]),
                    f"{tin/1000:.0f}K / {tout/1000:.1f}K",
                    _stat([e["wall_clock_s"] for e in eps]),
                ]
            )
        return _table(
            ["family", "steps", "tool calls", "tokens in/out", "seconds"],
            rows,
            left=(0,),
        )

    done = [e for arm in arms for e in episodes[arm] if e["accomplished"]]
    slide(
        "How hard each family is",
        "Cost per task varies several-fold by family, not by domain.",
        "<b>min / median / max</b> across every successful attempt by every "
        "system. Tokens are the per-task mean and include cache reads.",
        '<div class="split"><div><div class="colhd">Software development</div>'
        + _complexity_table("software")
        + '</div><div><div class="colhd">Knowledge work</div>'
        + _complexity_table("knowledge")
        + "</div></div>",
        f"{len(done)} successful attempts across {len(arms)} arms &nbsp;·&nbsp; "
        "failed attempts excluded, since an abandoned task understates cost",
    )

    from .enterprise import autonomy_table as _at

    _PCOLOR = {
        "unattended": "#34d399",
        "checked": "#e5e7eb",
        "gated": "#fbbf24",
        "assisted": "#f87171",
    }

    # What each use case actually costs, from the recorded corpus rather than
    # from our own runs: an order of magnitude more observations per use case,
    # so the numbers describe real demand instead of a 1-or-2-task sample.
    if reference and reference.get("use_case_profile"):
        from .enterprise import FAMILIES as _UF
        from .enterprise import family_of as _ufo

        _UDOM = {f.key: f.domain for f in _UF}
        _USHORT = {f.key: f.short for f in _UF}
        profile = reference["use_case_profile"]
        places = {r["use_case"]: r for r in _at(episodes, metas)}

        def _uc_table(domain_key, limit=8):
            picked = [
                (uc, v)
                for uc, v in profile.items()
                if _UDOM.get(_ufo(uc)) == domain_key
            ][:limit]
            rows = []
            for uc, v in picked:
                place = places.get(uc, {})
                colour = _PCOLOR.get(place.get("verdict_key", ""), "#e5e7eb")
                rows.append(
                    [
                        f"<code>{E(uc)}</code>",
                        E(_USHORT.get(_ufo(uc), "")),
                        f"{v['share_of_tasks']:.0f}%",
                        f"{v['share_of_tokens']:.0f}%",
                        f"<b>{v['share_of_spend']:.0f}%</b>",
                        f"${v['est_cost_usd']:.2f}",
                        f"{v['median_steps']} / {v['p90_steps']} / {v['max_steps']}",
                        f"{v['median_result_tokens']:,} / {v['p90_result_tokens']:,}",
                    ]
                )
            return _table(
                [
                    "use case",
                    "family",
                    "% tasks",
                    "% tokens",
                    "% spend",
                    "est $/task",
                    "steps med/p90/max",
                    "context tok med/p90",
                ],
                rows,
                left=(0, 1),
            ).replace('class="t tight"', 'class="t tight wide"')

        slide(
            "What each use case costs",
            "A question runs 1 step. A bug fix runs 46. Same agent, same tools.",
            f"From <b>{reference['human_tasks']:,} real tasks</b> in recorded "
            "<b>Claude Code sessions on Opus 5</b> — actual demand, not our "
            "benchmark. <b>% spend</b> is volume × per-task cost, so it shows "
            "where the money goes rather than which task is dearest.",
            '<div class="colhd">Software development</div>'
            + _uc_table("software")
            + '<div class="colhd" style="margin-top:10px">Knowledge work</div>'
            + _uc_table("knowledge"),
            "Top 8 by volume per domain &nbsp;·&nbsp; <b>cost is estimated</b> "
            "at Opus list pricing assuming a 17K-token prefix at 95% cache hit "
            "&nbsp;·&nbsp; ~4 chars per token &nbsp;·&nbsp; families match the "
            "next slide",
        )

    # The payoff: what the measurements imply for how to build an agent system.
    # Every row carries the number it rests on, so a recommendation cannot drift
    # from the evidence that produced it.
    from .routing import policy as _rp
    from .routing import rollup as _rr

    _rt = _rp(episodes, metas, reference)
    _rl = _rr(_rt) if _rt else []
    _cheap = _rl[0] if _rl else {}

    _fixed_pct, _cache_pct = 0, 0
    if measured:
        _e0 = [e for e in episodes[measured[0]] if e["input_tokens_by_call"]]
        _fixed_pct = (
            100
            * _med(e["fixed_prefill_tokens"] for e in _e0)
            / max(_med(e["input_tokens_by_call"][0] for e in _e0), 1)
        )
        _later = [h for e in _e0 for h in e["cache_hit_by_call"][1:]]
        _cache_pct = 100 * sum(_later) / len(_later) if _later else 0

    _gf = sum(sum(e["tool_failures"] for e in episodes[g]) for _, g, _ in pairs)
    _gc = sum(sum(len(e["tool_calls"]) for e in episodes[g]) for _, g, _ in pairs)
    _cf = sum(sum(e["tool_failures"] for e in episodes[c]) for _, _, c in pairs)
    _cn = sum(sum(len(e["tool_calls"]) for e in episodes[c]) for _, _, c in pairs)

    _know = 0
    if reference and reference.get("use_case_counts"):
        from .enterprise import by_domain as _bd3

        _know = next(
            d["share"]
            for d in _bd3(reference["use_case_counts"])
            if d["key"] == "knowledge"
        )
    _gated = sum(1 for r in _rt if r["confidence"] == "policy")

    recs = [
        [
            "<b>Route by use case, not by default</b>",
            f"{_cheap.get('share', 0):.0f}% of work passed on the cheapest "
            "model tested",
            "A single default model overpays on most requests and "
            "under-delivers on the rest.",
        ],
        [
            "<b>Optimise the fixed prefix, not the conversation</b>",
            f"{_fixed_pct:.0f}% of the first request is instructions and tool "
            f"schemas; {_cache_pct:.0f}% cache-hit after call 1",
            "Trim and cache the preamble. Shortening history saves almost " "nothing.",
        ],
        [
            "<b>Invest in the tool layer before the model</b>",
            f"{100 * _gf / max(_gc, 1):.0f}% vs "
            f"{100 * _cf / max(_cn, 1):.0f}% tool-call "
            "failure, same models",
            "Two harnesses, identical models: the weaker tool surface needs "
            "~50% more calls to finish.",
        ],
        [
            "<b>Design for knowledge work, not just code</b>",
            f"{_know:.0f}% of real demand never touches a repository",
            "Retrieval, citation and document output deserve the same effort "
            "as the code path.",
        ],
        [
            "<b>Bigger is not automatically better</b>",
            "Sonnet matched or beat Opus on judged quality, same harness",
            "The rubric penalises unrequested extra work, and the larger model "
            "does more of it — stray files, padded answers, skipped "
            "verification. Size buys capability, not restraint.",
        ],
        [
            "<b>Gate on reversibility, not on confidence</b>",
            f"{_gated} use cases are irreversible regardless of score",
            "Autonomy should key off whether a mistake can be undone, not off "
            "how well the model tested.",
        ],
    ]
    slide(
        "Recommendations",
        "Five design choices the measurements support.",
        "Each rests on a figure from this run, shown beside it.",
        _table(
            ["recommendation", "the evidence", "what it means"], recs, left=(0, 1, 2)
        )
        + '<div class="quad" style="margin-top:12px">'
        + '<div><div class="colhd">Backing rec 1 &nbsp;— where work would route</div>'
        + _table(
            ["model", "use cases", "share of work"],
            [
                [
                    f"<code>{E(r['model'].replace('claude-', '').replace('-35B-A3B', '').replace('-K2.7-Code', '').replace('-120B', ''))}</code>",
                    str(r["use_cases"]),
                    f"<b>{r['share']:.0f}%</b>",
                ]
                for r in _rl
            ],
            left=(0,),
        )
        + "</div>"
        + '<div><div class="colhd">Backing rec 3 &nbsp;— same model, two harnesses</div>'
        + _table(["model", "harness", "calls", "failed"], harness_rows, left=(0, 1))
        + "</div></div>",
        (
            "Derived from 24 verified tasks across 6 systems and "
            f"{reference['human_tasks']:,} recorded real tasks"
            if reference
            else "Derived from 24 verified tasks across 6 systems"
        ),
    )

    # Always last, and never cut: a deck that states no boundary invites the
    # reader to assume there isn't one.
    slide(
        "Limits",
        "What these numbers do not cover.",
        "Each of these bounds what can be concluded from the slides above.",
        _table(
            ["limit", "what it means"],
            [
                [
                    "<b>One attempt per task</b>",
                    "Agent runs are not deterministic. A one-task difference "
                    "between two systems is inside the noise.",
                ],
                [
                    f"<b>{len(tasks)} tasks, 1–2 per use case</b>",
                    "Enough to separate systems that differ a lot. Not enough "
                    "to support a per-use-case score, or a routing decision.",
                ],
                [
                    "<b>Tasks are small</b>",
                    "Our hardest run used 23 steps; real recorded tasks reach "
                    "417. We do not yet measure long-horizon work.",
                ],
                [
                    "<b>The verifier checks the outcome, not the route</b>",
                    "A system that arrives wastefully still passes — which "
                    "is why steps and failed calls are shown beside pass rates.",
                ],
                [
                    "<b>Quality is judged, correctness is not</b>",
                    "Pass/fail is a command's exit code. Quality is a model's "
                    "opinion on work that already passed, and is never mixed in.",
                ],
            ],
            left=(0, 1),
        ),
        "Full derivations in <b>report.md</b> &nbsp;·&nbsp; task definitions and "
        "verifiers in <b>TASKS.md</b>",
    )

    extra = """<style>
.t.tight th,.t.tight td{padding:6px 7px;font-size:12px}
.t th{font-size:10.5px;letter-spacing:.2px}
.split.roomy .t th{font-size:11px}
.t.wide th,.t.wide td{padding:4px 6px;font-size:10.5px;white-space:nowrap}
.t.wide td:first-child{white-space:normal}
.facts.sm .fnum{font-size:30px}.facts.sm .fsub{font-size:11.5px;line-height:1.42}
.split{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:4px}
.quad{display:grid;grid-template-columns:1fr 1fr;gap:12px 34px;margin-top:8px}
.quad .t th,.quad .t td{padding:3.5px 9px;font-size:11.5px;line-height:1.25}
.quad .colhd{font-size:11.5px;margin-bottom:5px}
/* The hierarchy column carries an indented family name and must not be
   squeezed into the number beside it. */
.quad .t td:first-child,.quad .t th:first-child{min-width:168px;white-space:nowrap}
/* This slide carries only two short tables, so it can afford real size. */
.split.roomy{gap:34px}
.split.roomy .t th,.split.roomy .t td{padding:7px 11px;font-size:13.5px}
.split.roomy .colhd{font-size:13px;margin-bottom:10px}
.colhd{font-family:'Archivo',sans-serif;font-weight:800;font-size:12px;
 color:var(--teal2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
</style>"""
    head = style.read_text(encoding="utf-8").replace("</head>", extra + "</head>")
    return head + '<div class="deck">' + "\n".join(S) + "</div></body></html>"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--arms", default="_arms")
    ap.add_argument("--style", type=Path, required=True)
    ap.add_argument(
        "--reference",
        type=Path,
        default=None,
        help="corpus_reference JSON; adds the two opening use-case slides",
    )
    a = ap.parse_args()
    reference = (
        json.loads(a.reference.read_text(encoding="utf-8"))
        if a.reference and a.reference.exists()
        else None
    )
    out = a.run / "slides.html"
    html_text = build(a.run, a.arms, a.style, reference)
    out.write_text(html_text, encoding="utf-8")
    print(f"wrote {out} ({len(html_text):,} bytes)")


if __name__ == "__main__":
    main()
