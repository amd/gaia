#!/usr/bin/env python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The live test report for an Agent UI release pass: what was tested, what wasn't, what broke.

Every command reads and rewrites RUN_DIR/report.json and re-renders RUN_DIR/report.md,
so the Markdown is always current and a crashed session loses nothing.

    test_report.py init RUN_DIR --build "v0.25.4-rc3 installer" [--scope RUN_DIR/scope.json] [--include P1,P2]
    test_report.py add RUN_DIR --title "Arabic: settings dialog" --area i18n [--priority P1] [--expect "..."]
    test_report.py set RUN_DIR ITEM passed|failed|blocked|not-tested|out-of-scope|in-progress \
        --note "what you saw" [--evidence shots/012_settings.png ...]
    test_report.py find RUN_DIR --title "..." --severity high --items ITEM[,ITEM] \
        --repro "1. ... 2. ..." --expected "..." --actual "..." [--evidence ...]
    test_report.py link RUN_DIR F3 [--issue URL] [--pr URL] [--status filed|fixing|fixed|verified|reopened|wontfix|duplicate] [--note ...]
    test_report.py round RUN_DIR --label "retest on main@abc1234 with fixes for F1-F4"
    test_report.py retest RUN_DIR             # one retest item per finding marked fixed
    test_report.py bench RUN_DIR --suite smoke --summary PATH --headline "4/5 passed"
    test_report.py status RUN_DIR             # counts and what is outstanding
    test_report.py gate RUN_DIR               # exit 0 only when the pass is really finished

The session that drives the app owns this file. Fix sessions report back to it;
they never write it themselves.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ITEM_STATES = (
    "planned",
    "in-progress",
    "passed",
    "failed",
    "blocked",
    "not-tested",
    "out-of-scope",
)
OPEN_ITEM = ("planned", "in-progress")
FINDING_STATES = (
    "open",
    "filed",
    "fixing",
    "fixed",
    "verified",
    "reopened",
    "wontfix",
    "duplicate",
)
CLOSED_FINDING = ("verified", "wontfix", "duplicate")
SEVERITIES = ("critical", "high", "medium", "low")


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def load(run: Path) -> dict:
    path = run / "report.json"
    if not path.exists():
        sys.exit(
            f"No report at {path}. Start one with `test_report.py init {run} --build ...`."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def save(run: Path, rep: dict) -> None:
    rep["updated"] = now()
    tmp = run / "report.json.tmp"
    tmp.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(run / "report.json")
    (run / "report.md").write_text(render(rep), encoding="utf-8")


def cur_round(rep: dict) -> int:
    return rep["rounds"][-1]["n"]


def item(rep: dict, key: str) -> dict:
    for it in rep["items"]:
        if it["id"].lower() == key.lower():
            return it
    sys.exit(f"No item {key}. `test_report.py status` lists them.")


def finding(rep: dict, key: str) -> dict:
    for f in rep["findings"]:
        if f["id"].lower() == key.lower():
            return f
    sys.exit(f"No finding {key}.")


def next_t_id(rep: dict) -> str:
    """T1, T2, ... counting only hand-added items, so PR and retest ids don't skip numbers."""
    used = {
        int(i["id"][1:])
        for i in rep["items"]
        if i["id"].startswith("T") and i["id"][1:].isdigit()
    }
    return f"T{max(used, default=0) + 1}"


def new_item(rep: dict, title: str, area: str, priority: str, **extra) -> dict:
    it = {
        "id": extra.pop("id", None) or next_t_id(rep),
        "title": title,
        "area": area,
        "priority": priority,
        "status": "planned",
        "round": cur_round(rep),
        "history": [],
        **extra,
    }
    if any(x["id"] == it["id"] for x in rep["items"]):
        sys.exit(f"Item {it['id']} already exists.")
    rep["items"].append(it)
    return it


# ── commands ────────────────────────────────────────────────────────────────


def cmd_init(a) -> None:
    a.run.mkdir(parents=True, exist_ok=True)
    if (a.run / "report.json").exists() and not a.force:
        sys.exit(f"{a.run / 'report.json'} exists; pass --force to start over.")
    rep = {
        "build": a.build,
        "machine": a.machine,
        "base": None,
        "target": None,
        "created": now(),
        "rounds": [{"n": 1, "label": "first pass", "started": now()}],
        "items": [],
        "findings": [],
        "benchmark": [],
    }
    if a.scope:
        scope = json.loads(a.scope.read_text(encoding="utf-8"))
        rep["base"], rep["target"] = scope["base"], scope["target"]
        keep = set(a.include.split(","))
        for s in scope["items"]:
            if s["priority"] in keep:
                new_item(
                    rep,
                    s["title"],
                    ",".join(s["surfaces"][:2]) or "-",
                    s["priority"],
                    id=s["id"],
                    source=s.get("url"),
                    issues=s.get("issues", []),
                    steps=s.get("test_plan", []),
                )
            elif s["priority"] == "P3":
                it = new_item(
                    rep,
                    s["title"],
                    ",".join(s["surfaces"][:2]),
                    "P3",
                    id=s["id"],
                    source=s.get("url"),
                )
                it["status"] = "out-of-scope"
                it["history"].append(
                    {
                        "at": now(),
                        "round": 1,
                        "status": "out-of-scope",
                        "note": s["why"],
                    }
                )
    save(a.run, rep)
    print(f"Report started: {len(rep['items'])} items. {a.run / 'report.md'}")


def cmd_add(a) -> None:
    rep = load(a.run)
    it = new_item(rep, a.title, a.area, a.priority, expect=a.expect, source=a.source)
    save(a.run, rep)
    print(it["id"])


def cmd_set(a) -> None:
    rep = load(a.run)
    it = item(rep, a.item)
    if a.status in ("not-tested", "blocked", "failed") and not a.note:
        sys.exit(f"Say why it is {a.status}: --note is required.")
    if a.status == "passed" and not a.evidence and not a.note:
        sys.exit("A pass needs what you saw: --note and/or --evidence.")
    it["status"] = a.status
    it["round"] = cur_round(rep)
    it["history"].append(
        {
            "at": now(),
            "round": cur_round(rep),
            "status": a.status,
            "note": a.note,
            "evidence": a.evidence or [],
        }
    )
    if it.get("finding") and a.status == "failed":
        f = finding(rep, it["finding"])
        f["status"] = "reopened"
        f["history"].append({"at": now(), "status": "reopened", "note": a.note})
    save(a.run, rep)
    print(f"{it['id']}: {a.status}")


def cmd_find(a) -> None:
    rep = load(a.run)
    items = [item(rep, k)["id"] for k in a.items.split(",")] if a.items else []
    area = a.area or (item(rep, items[0])["area"] if items else "-")
    f = {
        "id": f"F{len(rep['findings']) + 1}",
        "title": a.title,
        "severity": a.severity,
        "area": area,
        "items": items,
        "repro": a.repro,
        "expected": a.expected,
        "actual": a.actual,
        "evidence": a.evidence or [],
        "status": "open",
        "issue": None,
        "prs": [],
        "round_found": cur_round(rep),
        "history": [{"at": now(), "status": "open"}],
    }
    rep["findings"].append(f)
    for k in items:
        it = item(rep, k)
        if it["status"] in OPEN_ITEM + ("passed",):
            it["status"] = "failed"
            it["history"].append(
                {
                    "at": now(),
                    "round": cur_round(rep),
                    "status": "failed",
                    "note": f"{f['id']}: {a.title}",
                    "evidence": a.evidence or [],
                }
            )
    save(a.run, rep)
    print(f["id"])


def cmd_link(a) -> None:
    rep = load(a.run)
    f = finding(rep, a.finding)
    if a.issue:
        f["issue"] = a.issue
        f["status"] = "filed" if f["status"] == "open" else f["status"]
    if a.pr and a.pr not in f["prs"]:
        f["prs"].append(a.pr)
        f["status"] = "fixing" if f["status"] in ("open", "filed") else f["status"]
    if a.status:
        if a.status == "verified" and not a.note:
            sys.exit(
                "Say which retest proved it: --note (and the retest item's evidence)."
            )
        if a.status in ("wontfix", "duplicate") and not a.note:
            sys.exit(f"Say why it is {a.status}: --note is required.")
        f["status"] = a.status
    f["history"].append(
        {
            "at": now(),
            "status": f["status"],
            "note": a.note,
            "issue": a.issue,
            "pr": a.pr,
        }
    )
    save(a.run, rep)
    print(f"{f['id']}: {f['status']}")


def cmd_round(a) -> None:
    rep = load(a.run)
    rep["rounds"].append({"n": cur_round(rep) + 1, "label": a.label, "started": now()})
    save(a.run, rep)
    print(f"Round {cur_round(rep)}: {a.label}")


def cmd_retest(a) -> None:
    rep = load(a.run)
    made = []
    for f in rep["findings"]:
        if f["status"] != "fixed":
            continue
        rid = f"R{cur_round(rep)}-{f['id']}"
        if any(x["id"] == rid for x in rep["items"]):
            continue
        new_item(
            rep,
            f"Retest {f['id']}: {f['title']}",
            f["area"],
            "P1",
            id=rid,
            finding=f["id"],
            steps=[f["repro"]] if f["repro"] else [],
            expect=f["expected"],
        )
        made.append(rid)
    save(a.run, rep)
    print("Retest items: " + (", ".join(made) or "none (no finding is marked fixed)"))


def cmd_bench(a) -> None:
    rep = load(a.run)
    rep["benchmark"].append(
        {
            "at": now(),
            "round": cur_round(rep),
            "suite": a.suite,
            "agent": a.agent,
            "summary": str(a.summary) if a.summary else None,
            "headline": a.headline,
            "status": a.status,
        }
    )
    save(a.run, rep)
    print(f"Benchmark {a.suite}: {a.status} {a.headline or ''}".strip())


def problems(rep: dict) -> list[str]:
    out = []
    for it in rep["items"]:
        if it["status"] in OPEN_ITEM + ("blocked",):
            out.append(f"{it['id']} is still {it['status']}: {it['title']}")
        if (
            it["status"] == "failed"
            and not it.get("finding")
            and not any(it["id"] in f["items"] for f in rep["findings"])
        ):
            out.append(f"{it['id']} failed with no finding recorded")
    for f in rep["findings"]:
        if f["status"] not in CLOSED_FINDING:
            out.append(f"{f['id']} is {f['status']}: {f['title']}")
        elif f["status"] == "verified" and not any(
            x.get("finding") == f["id"] and x["status"] == "passed"
            for x in rep["items"]
        ):
            out.append(f"{f['id']} is marked verified but no retest item passed")
    last = cur_round(rep)
    if any(f["round_found"] == last for f in rep["findings"]):
        out.append(f"round {last} found new issues: another retest round is needed")
    return out


def cmd_status(a) -> None:
    rep = load(a.run)
    print(summary_line(rep))
    for p in problems(rep):
        print(f"  - {p}")


def cmd_gate(a) -> None:
    rep = load(a.run)
    left = problems(rep)
    if left:
        print(f"Not finished ({len(left)}):")
        for p in left:
            print(f"  - {p}")
        sys.exit(1)
    print(f"Finished: {summary_line(rep)}")


# ── rendering ───────────────────────────────────────────────────────────────


def counts(rep: dict) -> dict:
    return {s: sum(1 for it in rep["items"] if it["status"] == s) for s in ITEM_STATES}


def summary_line(rep: dict) -> str:
    c = counts(rep)
    fo = sum(1 for f in rep["findings"] if f["status"] not in CLOSED_FINDING)
    return (
        f"round {cur_round(rep)}: {c['passed']} passed, {c['failed']} failed, {c['blocked']} blocked, "
        f"{c['not-tested']} not tested, {c['planned'] + c['in-progress']} outstanding; "
        f"{len(rep['findings'])} findings, {fo} still open"
    )


def _last(it: dict) -> dict:
    return it["history"][-1] if it["history"] else {}


def _ev(paths: list[str]) -> str:
    return " ".join(f"[{Path(p).name}]({p})" for p in paths or [])


def render(rep: dict) -> str:
    left = problems(rep)
    verdict = (
        "**Finished.** Every planned item has a result and every finding is verified or closed."
        if not left
        else f"**In progress.** {len(left)} things stand between this pass and done."
    )
    rng = f" ({rep['target']} since {rep['base']})" if rep.get("target") else ""
    md = [
        f"# Agent UI release test: {rep['build']}{rng}",
        "",
        verdict,
        "",
        summary_line(rep) + ".",
        "",
        f"Machine: {rep.get('machine') or 'not recorded'} · started {rep['created']} · updated {rep.get('updated', '')}",
        "",
    ]

    md += ["## Findings", ""]
    if rep["findings"]:
        md += [
            "| ID | Sev | Status | Finding | Issue | Fix | Found in |",
            "|---|---|---|---|---|---|---|",
        ]
        for f in rep["findings"]:
            md.append(
                f"| {f['id']} | {f['severity']} | {f['status']} | {f['title']} | {f['issue'] or '-'} | "
                f"{' '.join(f['prs']) or '-'} | round {f['round_found']} |"
            )
    else:
        md.append("None yet.")
    md.append("")

    outstanding = [
        it for it in rep["items"] if it["status"] in OPEN_ITEM + ("blocked",)
    ]
    md += ["## Outstanding", ""]
    md += [
        f"- **{it['id']}** [{it['priority']}] {it['title']} — {it['status']}"
        + (f": {_last(it).get('note')}" if _last(it).get("note") else "")
        for it in outstanding
    ] or ["Nothing."]
    md.append("")

    not_tested = [
        it for it in rep["items"] if it["status"] in ("not-tested", "out-of-scope")
    ]
    md += ["## Not tested, and why", ""]
    md += [
        f"- **{it['id']}** {it['title']} — {_last(it).get('note') or it['status']}"
        for it in not_tested
    ] or ["Everything planned was tested."]
    md.append("")

    md += [
        "## Tested",
        "",
        "| ID | Pri | Area | Item | Result | Round | What was seen | Evidence |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for it in rep["items"]:
        if it["status"] in ("passed", "failed"):
            h = _last(it)
            result = it["status"]
            mine = [f for f in rep["findings"] if it["id"] in f["items"]]
            if (
                result == "failed"
                and mine
                and all(f["status"] == "verified" for f in mine)
            ):
                result = (
                    "failed, fix verified (" + ", ".join(f["id"] for f in mine) + ")"
                )
            note = (h.get("note") or "").replace("|", "\\|").replace("\n", " ")
            md.append(
                f"| {it['id']} | {it['priority']} | {it['area']} | {it['title']} | {result} | "
                f"{it['round']} | {note} | {_ev(h.get('evidence'))} |"
            )
    md.append("")

    md += [
        "## Rounds",
        "",
        "| Round | What | Started | New findings |",
        "|---|---|---|---|",
    ]
    for r in rep["rounds"]:
        n = sum(1 for f in rep["findings"] if f["round_found"] == r["n"])
        md.append(f"| {r['n']} | {r['label']} | {r['started']} | {n} |")
    md.append("")

    if rep["benchmark"]:
        md += [
            "## Benchmark (ai-pc-evals)",
            "",
            "| Round | Suite | Agent | Status | Result | Summary |",
            "|---|---|---|---|---|---|",
        ]
        for b in rep["benchmark"]:
            md.append(
                f"| {b['round']} | {b['suite']} | {b.get('agent') or '-'} | {b['status']} | "
                f"{b.get('headline') or '-'} | {b.get('summary') or '-'} |"
            )
        md.append("")

    if rep["findings"]:
        md += ["## Finding details", ""]
        for f in rep["findings"]:
            md += [
                f"### {f['id']} · {f['title']}",
                "",
                f"- Severity {f['severity']}, area {f['area']}, items {', '.join(f['items']) or '-'}",
                f"- Expected: {f['expected'] or '-'}",
                f"- Actual: {f['actual'] or '-'}",
            ]
            if f["repro"]:
                md += ["- Steps:", "", "  " + f["repro"].replace("\n", "\n  "), ""]
            if f["evidence"]:
                md.append(f"- Evidence: {_ev(f['evidence'])}")
            md.append("")
    return "\n".join(md)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def p(name, fn):
        sp = sub.add_parser(name)
        sp.add_argument("run", type=Path, help="Run directory holding report.json")
        sp.set_defaults(fn=fn)
        return sp

    sp = p("init", cmd_init)
    sp.add_argument(
        "--build",
        required=True,
        help="What is under test, e.g. 'v0.25.4-rc3 Windows installer'",
    )
    sp.add_argument(
        "--machine", help="Hardware and OS, e.g. 'Strix Halo 128 GB, Windows 11'"
    )
    sp.add_argument("--scope", type=Path, help="scope.json from release_scope.py")
    sp.add_argument(
        "--include",
        default="P1,P2",
        help="Priorities from scope.json to plan (default P1,P2)",
    )
    sp.add_argument("--force", action="store_true")

    sp = p("add", cmd_add)
    sp.add_argument("--title", required=True)
    sp.add_argument("--area", required=True)
    sp.add_argument("--priority", default="P2", choices=("P1", "P2", "P3"))
    sp.add_argument("--expect", help="The result that counts as a pass")
    sp.add_argument("--source", help="PR or issue URL this checks")

    sp = p("set", cmd_set)
    sp.add_argument("item")
    sp.add_argument("status", choices=ITEM_STATES)
    sp.add_argument("--note")
    sp.add_argument("--evidence", nargs="*")

    sp = p("find", cmd_find)
    sp.add_argument("--title", required=True)
    sp.add_argument("--severity", required=True, choices=SEVERITIES)
    sp.add_argument("--area", help="Default: the first failing item's area")
    sp.add_argument("--items", help="Comma-separated item ids this finding fails")
    sp.add_argument("--repro", default="")
    sp.add_argument("--expected", default="")
    sp.add_argument("--actual", default="")
    sp.add_argument("--evidence", nargs="*")

    sp = p("link", cmd_link)
    sp.add_argument("finding")
    sp.add_argument("--issue")
    sp.add_argument("--pr")
    sp.add_argument("--status", choices=FINDING_STATES)
    sp.add_argument("--note")

    sp = p("round", cmd_round)
    sp.add_argument("--label", required=True)

    p("retest", cmd_retest)

    sp = p("bench", cmd_bench)
    sp.add_argument("--suite", required=True)
    sp.add_argument("--agent", help="Leg, e.g. gaia-agent-ui")
    sp.add_argument("--summary", type=Path, help="The grader's summary.md")
    sp.add_argument(
        "--headline", help="The grader's own pass line, copied, never estimated"
    )
    sp.add_argument(
        "--status", default="graded", choices=("graded", "not-graded", "not-run")
    )

    p("status", cmd_status)
    p("gate", cmd_gate)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
