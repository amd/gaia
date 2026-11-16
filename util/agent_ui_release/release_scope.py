#!/usr/bin/env python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""List what changed between two GAIA releases and seed an Agent UI test plan.

    python release_scope.py --out RUN_DIR                    # newest tag vs. the tag before it
    python release_scope.py --out RUN_DIR --since release    # newest tag vs. the last final release
    python release_scope.py --out RUN_DIR --target v0.25.4-rc3 --base v0.25.3

Writes RUN_DIR/scope.json (one entry per merged PR, with the surfaces it
touches, a priority and its PR "Test plan") and RUN_DIR/plan.md (the same,
grouped by surface, for a person to read). `test_report.py init` turns
scope.json into the live report.

Run `git fetch --tags origin` first: the range is computed from local tags.
Needs `gh` (logged in) for PR titles, labels, linked issues and test plans;
`--offline` skips it and works from commit subjects alone.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = "amd/gaia"
TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(-rc\d+)?$")
PR_RE = re.compile(r"\(#(\d+)\)\s*$")
CLOSES_RE = re.compile(r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.I)
CC_RE = re.compile(r"^(\w+)(?:\(([^)]*)\))?!?:\s*(.*)$")
DEV_ONLY_STEP = re.compile(
    r"^`?\s*(python|pytest|uv |npm|npx|cd |go |git |gh |black|isort|flake8|pylint)|"
    r"\b(lint|pytest|unit tests?|vitest|go test|eval_flagship\.yml)\b",
    re.I,
)

# First match wins; order from most to least user-visible.
SURFACES = [
    (
        "electron-shell",
        re.compile(
            r"^src/gaia/apps/webui/(main\.cjs|preload\.cjs|main-safety-net\.cjs|services/|electron-builder\.yml|build/|bin/)"
        ),
    ),
    (
        "agent-ui",
        re.compile(r"^src/gaia/apps/webui/(src|public|index\.html|app\.config\.json)"),
    ),
    ("ui-backend", re.compile(r"^src/gaia/ui/")),
    ("agent", re.compile(r"^(hub/agents/gaia/|src/gaia/agents/|src/gaia/chat/)")),
    ("skills", re.compile(r"^hub/skills/|^src/gaia/skills/")),
    (
        "models-llm",
        re.compile(r"^src/gaia/llm/|^src/gaia/vlm/|^src/gaia/rag/|^src/gaia/audio/"),
    ),
    (
        "connectors",
        re.compile(r"^src/gaia/connectors/|^src/gaia/mcp/|^hub/agents/email/"),
    ),
    ("installer", re.compile(r"^installer/|^src/gaia/installer/|^scripts/install")),
    (
        "daemon",
        re.compile(r"^src/gaia/daemon/|^src/gaia/schedule/|^src/gaia/messaging/"),
    ),
    ("tui", re.compile(r"^tui/")),
    ("other-code", re.compile(r"^src/|^hub/")),
]
# Paths that never change what the Agent UI does.
INERT = re.compile(
    r"^(docs/|tests/|\.github/|\.claude/|eval/|util/|README|CHANGELOG|(?!hub/).*\.md$)|"
    r"^src/gaia/apps/webui/(visual/|.*\.test\.tsx?$|src/.*__tests__/)"
)
# Surfaces a person can reach from the Agent UI window.
UI_REACHABLE = {
    "electron-shell",
    "agent-ui",
    "ui-backend",
    "agent",
    "skills",
    "models-llm",
    "connectors",
    "installer",
    "daemon",
    "other-code",
}


def run(*cmd: str) -> str:
    return subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    ).stdout


def release_tags() -> list[str]:
    # Version order, RCs before their final: a hotfix tagged late must not become the base.
    tags = run(
        "git",
        "-c",
        "versionsort.suffix=-rc",
        "tag",
        "--list",
        "v*",
        "--sort=-v:refname",
    ).split()
    return [t for t in tags if TAG_RE.match(t)]


def pick_range(target: str | None, base: str | None, since: str) -> tuple[str, str]:
    tags = release_tags()
    if not tags:
        sys.exit("No vX.Y.Z tags here. Run `git fetch --tags origin` first.")
    target = target or tags[0]
    if target not in tags and target != "HEAD":
        sys.exit(
            f"{target} is not a release tag here. Run `git fetch --tags origin`, "
            f"or pass --target HEAD to test an unreleased branch."
        )
    if base:
        return target, base
    older = tags[tags.index(target) + 1 :] if target in tags else tags
    if since == "release":
        older = [t for t in older if "-rc" not in t]
    if not older:
        sys.exit(f"No release older than {target} to diff against; pass --base.")
    return target, older[0]


def commits(base: str, target: str) -> list[dict]:
    out = run(
        "git",
        "log",
        "--first-parent",
        "--format=%H%x1f%s%x1f%b%x1e",
        f"{base}..{target}",
    )
    result = []
    for rec in out.split("\x1e"):
        if not rec.strip():
            continue
        sha, subject, body = (rec.strip("\n").split("\x1f") + ["", ""])[:3]
        files = [
            f
            for f in run("git", "show", "--name-only", "--format=", sha).splitlines()
            if f
        ]
        result.append(
            {"sha": sha, "subject": subject.strip(), "body": body, "files": files}
        )
    return result


def surfaces_for(files: list[str]) -> list[str]:
    found = []
    for path in files:
        if INERT.match(path):
            continue
        for name, rx in SURFACES:
            if rx.match(path):
                if name not in found:
                    found.append(name)
                break
    return found


def gh_prs(numbers: list[int]) -> dict[int, dict]:
    """Title, labels, linked issues and body for each PR, 25 per GraphQL call."""
    owner, name = REPO.split("/")
    found: dict[int, dict] = {}
    for i in range(0, len(numbers), 25):
        chunk = numbers[i : i + 25]
        fields = (
            "number title url body labels(first:15){nodes{name}} "
            "closingIssuesReferences(first:5){nodes{number title url}}"
        )
        parts = " ".join(
            f"p{n}: pullRequest(number: {n}) {{ {fields} }}" for n in chunk
        )
        query = (
            f'query {{ repository(owner: "{owner}", name: "{name}") {{ {parts} }} }}'
        )
        try:
            data = json.loads(run("gh", "api", "graphql", "-f", f"query={query}"))
        except subprocess.CalledProcessError as exc:
            sys.exit(
                f"gh api graphql failed: {exc.stderr.strip()}\n"
                "Run `gh auth status`, or pass --offline to plan from commit subjects only."
            )
        for pr in (data.get("data", {}).get("repository") or {}).values():
            if pr:
                found[pr["number"]] = pr
    return found


def test_plan(body: str) -> list[str]:
    """The PR's own '## Test plan' checklist, which is the best starting test case."""
    m = re.search(
        r"^#+\s*test plan\s*$(.*?)(?=^#+\s|\Z)", body or "", re.I | re.M | re.S
    )
    if not m:
        return []
    lines = [ln.strip() for ln in m.group(1).splitlines()]
    steps = [
        re.sub(r"^[-*]\s*(\[[ xX]\]\s*)?", "", ln)
        for ln in lines
        if ln.startswith(("-", "*"))
    ]
    # CI already ran the PR's test commands; keep the steps a person does in the app.
    return [s for s in steps if not DEV_ONLY_STEP.match(s)][:10]


def classify(kind: str, surfaces: list[str]) -> tuple[str, str]:
    """(priority, why) — P1 must be exercised in the app, P3 is not reachable from it."""
    reachable = [s for s in surfaces if s in UI_REACHABLE]
    if not surfaces:
        return "skip", "docs/tests/CI only"
    if not reachable:
        return "P3", f"not reachable from the Agent UI ({', '.join(surfaces)})"
    if kind in ("fix", "feat", "perf") and any(
        s in ("agent-ui", "electron-shell", "ui-backend", "installer")
        for s in reachable
    ):
        return "P1", "user-visible change in the app"
    if kind in ("fix", "feat", "perf"):
        return "P1", "behaviour change reachable through chat"
    return "P2", "regression spot check"


def build(base: str, target: str, offline: bool) -> dict:
    items = []
    raw = commits(base, target)
    numbers = [int(m.group(1)) for c in raw if (m := PR_RE.search(c["subject"]))]
    meta = {} if offline or not numbers else gh_prs(numbers)
    for c in raw:
        m = PR_RE.search(c["subject"])
        number = int(m.group(1)) if m else None
        pr = meta.get(number, {}) if number else {}
        title = pr.get("title") or PR_RE.sub("", c["subject"]).strip()
        cc = CC_RE.match(title)
        kind, scope = (cc.group(1).lower(), cc.group(2) or "") if cc else ("other", "")
        surfaces = surfaces_for(c["files"])
        priority, why = classify(kind, surfaces)
        issues = [
            i["number"]
            for i in (pr.get("closingIssuesReferences") or {}).get("nodes", [])
        ]
        issues = issues or sorted({int(n) for n in CLOSES_RE.findall(c["body"])})
        items.append(
            {
                "id": f"PR{number}" if number else c["sha"][:9],
                "pr": number,
                "url": pr.get("url")
                or (f"https://github.com/{REPO}/pull/{number}" if number else None),
                "sha": c["sha"],
                "title": title,
                "kind": kind,
                "scope": scope,
                "labels": [
                    n["name"] for n in (pr.get("labels") or {}).get("nodes", [])
                ],
                "issues": issues,
                "surfaces": surfaces,
                "priority": priority,
                "why": why,
                "test_plan": test_plan(pr.get("body", "")),
                "files": len(c["files"]),
            }
        )
    return {"repo": REPO, "base": base, "target": target, "items": items}


def render(scope: dict) -> str:
    items = scope["items"]
    counts = {
        p: sum(1 for i in items if i["priority"] == p)
        for p in ("P1", "P2", "P3", "skip")
    }
    out = [
        f"# Agent UI test plan: {scope['target']} (since {scope['base']})",
        "",
        f"{len(items)} merged changes: **{counts['P1']} must test**, {counts['P2']} spot check, "
        f"{counts['P3']} not reachable from the app, {counts['skip']} docs/tests/CI only.",
        "",
        "Edit this plan before testing: merge items that share a screen, add the exploratory "
        "and always-check areas, and give each P1 a concrete expected result.",
        "",
    ]
    for prio, heading in (
        ("P1", "Must test"),
        ("P2", "Spot check"),
        ("P3", "Not reachable from the Agent UI"),
        ("skip", "Skipped"),
    ):
        group = [i for i in items if i["priority"] == prio]
        if not group:
            continue
        out += [f"## {heading} ({len(group)})", ""]
        for i in sorted(group, key=lambda i: (i["surfaces"][:1], i["kind"])):
            link = f"[#{i['pr']}]({i['url']})" if i["pr"] else i["sha"][:9]
            where = ", ".join(i["surfaces"]) or "-"
            issues = " ".join(f"#{n}" for n in i["issues"])
            out.append(
                f"- **{i['id']}** {link} `{i['kind']}` {i['title']} — _{where}_"
                + (f" (closes {issues})" if issues else "")
            )
            if prio in ("P1", "P2"):
                out += [f"  - [ ] {step}" for step in i["test_plan"]]
        out.append("")
    return "\n".join(out)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Run directory to write scope.json and plan.md into",
    )
    ap.add_argument(
        "--target", help="Tag under test (default: newest release tag, RCs included)"
    )
    ap.add_argument("--base", help="Tag to diff against (default: picked by --since)")
    ap.add_argument(
        "--since",
        choices=("rc", "release"),
        default="rc",
        help="rc: the tag just before --target (default); release: the last final release",
    )
    ap.add_argument(
        "--offline", action="store_true", help="Skip gh; plan from commit subjects only"
    )
    args = ap.parse_args()

    target, base = pick_range(args.target, args.base, args.since)
    scope = build(base, target, args.offline)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "scope.json").write_text(json.dumps(scope, indent=1), encoding="utf-8")
    (args.out / "plan.md").write_text(render(scope), encoding="utf-8")
    p1 = sum(1 for i in scope["items"] if i["priority"] == "P1")
    print(f"{target} since {base}: {len(scope['items'])} changes, {p1} must test")
    print(f"Wrote {args.out / 'scope.json'} and {args.out / 'plan.md'}")


if __name__ == "__main__":
    main()
