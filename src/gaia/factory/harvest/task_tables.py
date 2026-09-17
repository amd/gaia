# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Report sections for the per-task view and the tool inventory.

Kept out of ``report.py``, which is already 65KB. Same contract as every table
there: take traces, return markdown, print from ``report.main``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, List, Sequence

from gaia.factory.harvest import inventory as inv
from gaia.factory.harvest.tasks import Task, segment, summarise


def collect(traces: Sequence[dict]) -> List[Task]:
    """Every task in the corpus, from every session."""
    out: List[Task] = []
    for t in traces:
        out.extend(segment(t))
    return out


def _needs_reader(tasks: Sequence[Task]) -> str:
    if any(s.get("prompt_index", -1) >= 0 for t in tasks for s in t.steps):
        return ""
    return (
        "_No step carries a `prompt_index`. This cache predates per-task "
        "attribution — re-run `python -m gaia.factory.harvest.scan` to rebuild "
        "`traces.jsonl`, or every task below will show zero steps._\n\n"
    )


def task_grid_table(traces: Sequence[dict]) -> str:
    """Activity against domain — what this corpus was actually used for.

    The session-level label answered a different question: 300 sessions carried
    1,208 asks, so a session tagged ``code_review`` hid whatever else happened
    after the review was done.
    """
    tasks = collect(traces)
    if not tasks:
        return "_no tasks_"
    s = summarise(tasks)
    acts = [a for a, _ in Counter(t.activity for t in tasks).most_common()]
    doms = [d for d, _ in Counter(t.domain for t in tasks).most_common()]

    grid: Dict[str, Counter] = defaultdict(Counter)
    for t in tasks:
        grid[t.activity][t.domain] += 1

    head = "| activity | " + " | ".join(doms) + " | **total** |"
    rule = "|---|" + "---:|" * (len(doms) + 1)
    rows = [head, rule]
    for a in acts:
        cells = [str(grid[a].get(d, 0) or "") for d in doms]
        rows.append(
            f"| {a} | " + " | ".join(cells) + f" | **{sum(grid[a].values())}** |"
        )
    tot = [str(sum(grid[a].get(d, 0) for a in acts)) for d in doms]
    rows.append("| **total** | " + " | ".join(tot) + f" | **{len(tasks)}** |")

    inherited = sum(1 for t in tasks if t.inherited)
    note = (
        f"\n{len(tasks)} tasks across {len({t.session_id for t in tasks})} sessions; "
        f"{s['tasks_with_tool_calls']} ran at least one tool. "
        f"{inherited} ({100*inherited/len(tasks):.0f}%) inherited their label from the "
        "preceding task — a follow-up turn continues the work before it, and "
        "those are weaker evidence than a label the ask earned.\n"
    )
    return _needs_reader(tasks) + "\n".join(rows) + "\n" + note


def flow_table(traces: Sequence[dict], top: int = 12) -> str:
    """The shape each activity takes, as a collapsed family sequence."""
    tasks = [t for t in collect(traces) if t.n_steps]
    if not tasks:
        return "_no tasks with tool calls_"
    by: Dict[str, Counter] = defaultdict(Counter)
    for t in tasks:
        by[t.activity][t.flow] += 1
    rows = [
        "| activity | n | median steps | most common shape |",
        "|---|---:|---:|---|",
    ]
    for a, flows in sorted(by.items(), key=lambda kv: -sum(kv[1].values())):
        group = [t for t in tasks if t.activity == a]
        med = sorted(t.n_steps for t in group)[len(group) // 2]
        shape, n = flows.most_common(1)[0]
        rows.append(f"| {a} | {len(group)} | {med} | `{shape[:60]}` ({n}) |")
    return "\n".join(rows[: top + 2])


def inventory_table(traces: Sequence[dict], top: int = 20) -> str:
    """Every surface the agent reaches for, with switches where they apply."""
    tasks = collect(traces)
    surfaces = inv.build(tasks)
    c = inv.coverage(surfaces)

    out = [
        f"**{c['agent_tools']}** agent tools · **{c['mcp_tools']}** MCP tools across "
        f"**{c['mcp_servers']}** servers · **{c['shell_binaries']}** shell binaries "
        f"with **{c['shell_switch_pairs']}** distinct switches.",
        "",
        f"The top 10 binaries are {c['top10_share']:.0%} of all shell calls, and "
        f"{c['binaries_used_once']} binaries were used exactly once — a long tail that "
        "any top-N table hides.",
        "",
        "| binary | calls | fail | switches seen |",
        "|---|---:|---:|---|",
    ]
    for r in surfaces["shell"][:top]:
        sw = ", ".join(f"`{f}`" for f, _ in r.switches.most_common(6)) or "—"
        out.append(f"| `{r.name}` | {r.calls} | {r.failure_rate:.0%} | {sw} |")

    out += [
        "",
        "**Agent and MCP tools**",
        "",
        "| tool | surface | calls | fail |",
        "|---|---|---:|---:|",
    ]
    for r in (surfaces["agent"] + surfaces["mcp"])[:top]:
        label = f"{r.server}/{r.name.split('__')[-1]}" if r.server else r.name
        out.append(f"| `{label}` | {r.surface} | {r.calls} | {r.failure_rate:.0%} |")
    return "\n".join(out)


def needs_table(traces: Sequence[dict]) -> str:
    """What each kind of work actually requires — the build-decision view."""
    tasks = collect(traces)
    surfaces = inv.build(tasks)
    shell = inv.by_activity(surfaces, "shell")
    mcp = inv.by_activity(surfaces, "mcp")
    rows = ["| activity | shell | MCP |", "|---|---|---|"]
    for a in sorted(set(shell) | set(mcp)):
        s = ", ".join(f"`{x}`" for x in shell.get(a, [])[:8]) or "—"
        m = ", ".join(f"`{x.split('__')[-1]}`" for x in mcp.get(a, [])[:4]) or "—"
        rows.append(f"| {a} | {s} | {m} |")
    rows.append("")
    rows.append(
        "_A tool appears against an activity when it recurs corpus-wide and that "
        "activity accounts for a real share of its use. Shell parsing cannot fully "
        "separate a command from the inside of a quoted string, so rare tokens are "
        "filtered rather than trusted._"
    )
    return "\n".join(rows)
