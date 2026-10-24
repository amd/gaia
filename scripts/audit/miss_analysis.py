#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Measure which bug classes reach a fix PR without an audit catching them first.

Rewrites the generated block of ``scripts/audit/missed_bug_classes.md``, which all
three proactive audits read at run time. The checklists in that file are curated
by hand; this script keeps the evidence under them current, so the prompts follow
where bugs are actually being found instead of where they were found once.

A merged ``fix`` PR counts as CAUGHT when it closes an issue the audits filed
(label ``weekly-audit``); every other one is a MISS and is assigned to every class
whose pattern matches its title. Titles only: PR bodies mention "Windows" or
"release" in passing, which inflated those classes several-fold. Audit issues closed as
duplicate or not-planned are the NOISE side: the classes the prompts should stop
producing.

Refresh (monthly, or after a burst of fixes in one area):

    python scripts/audit/miss_analysis.py --days 120
    git diff scripts/audit/missed_bug_classes.md   # then edit the checklists if a
                                                   # new class is climbing

stdlib + gh only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import closed_duplicates  # noqa: E402  (sibling module; scripts/audit is no package)

DOC = Path(__file__).resolve().parent / "missed_bug_classes.md"
START = "<!-- miss-analysis:start -->"
END = "<!-- miss-analysis:end -->"

#: One entry per class the checklists name. Keep the names identical to the
#: headings in missed_bug_classes.md.
CLASSES: tuple[tuple[str, str], ...] = (
    ("No deadline", r"\bhangs?\b|\bhung\b|stall|timeout|timed out|deadline|stuck|freez|forever|no progress|bounded time|spin"),
    ("Shared state across processes", r"\brace\b|concurren|\block(ed)?\b|\blease|atomic|simultaneous|shared|another (chat|app|process|session)|from under|user'?s own|kills? the user|port .*restart|restarted on|stolen port|overwrit"),
    ("Dishonest status", r"silent|swallow|honest|false (success|pass)|report(s|ed)? .* as|says? .* (fail|missing|stopped)|instead of (failing|an error)|no error|exited early|green when|mislabel|misreport|not reported"),
    ("Cold start and non-default setups", r"first[- ]run|first (start|launch)|fresh|clean install|new user|skip-models|--pre\b|proxy|tls|certificate|remote |gaia_home|not (yet )?(installed|downloaded)|skipped model"),
    ("Windows behaviour", r"windows|win32|\bcrlf\b|powershell|\.exe\b|console window|backslash|locked|code ?page|utf-16"),
    ("Model output handling", r"markup|tool[- ]call|json (answer|envelope)|truncat|reasoning|placeholder arguments|dsml|first quote"),
    ("Release and CI ordering", r"\brelease|\bpublish|\bci\b|\brc\b|pipeline|\btag\b|wheel|pypi|\bnpm\b|merge queue|main('s)? (ci|unit tests)"),
    ("Doc claim vs behaviour", r"\bdocs?\b|readme|changelog|\bspec\b|contract|documented|guide"),
    # Scope-based: behaviour a static read rarely catches. Counted so the table
    # says honestly how much of the fix stream is out of a static audit's reach.
    ("Agent UI and TUI behaviour", r"^\w+\((ui|webui|tui|website|web)\)"),
    ("Agent answers and tool use", r"^\w+\((agent|agents|gaia|gaia-agent|tools|chat|memory)\)"),
)

#: Fallback bucket for a miss no pattern names. Reported, never hidden.
UNCLASSIFIED = "Other"


def _gh(args: list[str]) -> list[dict]:
    completed = subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if completed.returncode != 0:
        raise SystemExit(f"`gh {' '.join(args[:3])} ...` failed:\n{completed.stderr}")
    return json.loads(completed.stdout)


def fetch_fix_prs(repo: str, start: date, end: date, chunk_days: int = 10) -> list[dict]:
    """Merged `fix` PRs, fetched in date chunks so no single query hits gh's cap."""
    prs: dict[int, dict] = {}
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=chunk_days - 1), end)
        batch = _gh(
            [
                "pr", "list", "--repo", repo, "--state", "merged", "--limit", "1000",
                "--search", f"fix in:title merged:{cursor.isoformat()}..{stop.isoformat()}",
                "--json", "number,title,body,mergedAt,closingIssuesReferences",
            ]
        )
        if len(batch) >= 1000:
            raise SystemExit(
                f"{cursor}..{stop} returned 1000 PRs, gh's cap; lower --chunk-days so "
                "no fix is silently left out of the count."
            )
        prs.update({p["number"]: p for p in batch})
        cursor = stop + timedelta(days=1)
    return list(prs.values())


CLOSES_RE = re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)


def closed_issues(pr: dict) -> set[int]:
    refs = {r["number"] for r in pr.get("closingIssuesReferences") or []}
    return refs | {int(n) for n in CLOSES_RE.findall(pr.get("body") or "")}


def classify(pr: dict) -> list[str]:
    title = pr["title"].lower()
    hits = [name for name, rx in CLASSES if re.search(rx, title)]
    return hits or [UNCLASSIFIED]


def analyse(prs: list[dict], audit_issues: list[dict]) -> dict:
    audit_numbers = {i["number"] for i in audit_issues}
    caught, missed = [], []
    for pr in prs:
        (caught if closed_issues(pr) & audit_numbers else missed).append(pr)
    classes: Counter = Counter()
    examples: dict[str, list[int]] = defaultdict(list)
    for pr in sorted(missed, key=lambda p: p["mergedAt"], reverse=True):
        for name in classify(pr):
            classes[name] += 1
            if len(examples[name]) < 5:
                examples[name].append(pr["number"])
    noise: Counter = Counter()
    for issue in audit_issues:
        reason = issue.get("stateReason") or ""
        if reason not in ("DUPLICATE", "NOT_PLANNED"):
            continue
        dup = closed_duplicates.canonical_of(issue) if reason == "DUPLICATE" else None
        noise[f"duplicate of #{dup}" if dup else reason.lower().replace("_", " ")] += 1
    return {
        "total": len(prs),
        "caught": len(caught),
        "caught_examples": sorted(p["number"] for p in caught)[-8:],
        "classes": classes,
        "examples": examples,
        "noise": noise,
    }


def render(result: dict, start: date, end: date) -> str:
    total, caught = result["total"], result["caught"]
    share = f"{100 * caught / total:.0f}%" if total else "n/a"
    lines = [
        START,
        f"_Generated by `scripts/audit/miss_analysis.py` for merged `fix` PRs "
        f"{start}..{end}. Do not edit by hand._",
        "",
        f"**{caught} of {total} fix PRs ({share}) closed an issue an audit filed first**"
        f" (e.g. {', '.join(f'#{n}' for n in result['caught_examples']) or 'none'}).",
        "",
        "| Missed class | Fix PRs | Recent examples |",
        "|---|---|---|",
    ]
    for name, count in result["classes"].most_common():
        refs = ", ".join(f"#{n}" for n in result["examples"][name])
        lines.append(f"| {name} | {count} | {refs} |")
    lines += [
        "",
        "A PR can match several classes. Audit issues closed as noise in the same data:",
        "",
    ]
    for reason, count in result["noise"].most_common(8):
        lines.append(f"- {reason}: {count}")
    lines.append(END)
    return "\n".join(lines)


def fetch_audit_issues(repo: str, cap: int) -> list[dict]:
    """Every audit-filed issue's number and close reason; duplicates with their canonical.

    Paged and capped (closed_duplicates.py): the old single 5000-issue query pulled
    every comment of every issue and could not tell a short list from a cut one.
    """
    issues = closed_duplicates.list_labelled(
        repo, "weekly-audit", ("OPEN", "CLOSED"), cap
    )
    details = {
        d["number"]: d
        for d in closed_duplicates.duplicate_details(
            [i["id"] for i in issues if i.get("stateReason") == "DUPLICATE"]
        )
    }
    return [dict(details.get(i["number"], {}), **i) for i in issues]


def splice(doc: str, block: str) -> str:
    if doc.count(START) != 1 or doc.count(END) != 1:
        raise SystemExit(f"{DOC} must contain exactly one {START} ... {END} block.")
    head, _, rest = doc.partition(START)
    _, _, tail = rest.partition(END)
    return head + block + tail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default="amd/gaia")
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--end", default="", help="YYYY-MM-DD (default: today)")
    parser.add_argument("--chunk-days", type=int, default=10)
    parser.add_argument(
        "--issue-cap",
        type=int,
        default=closed_duplicates.DEFAULT_CAP,
        help="Fail rather than read past this many audit-labelled issues.",
    )
    args = parser.parse_args(argv)

    end = date.fromisoformat(args.end) if args.end else date.today()
    start = end - timedelta(days=args.days)
    prs = fetch_fix_prs(args.repo, start, end, args.chunk_days)
    audit_issues = fetch_audit_issues(args.repo, args.issue_cap)
    block = render(analyse(prs, audit_issues), start, end)
    DOC.write_text(splice(DOC.read_text(encoding="utf-8"), block), encoding="utf-8")
    print(block)
    return 0


if __name__ == "__main__":
    sys.exit(main())
