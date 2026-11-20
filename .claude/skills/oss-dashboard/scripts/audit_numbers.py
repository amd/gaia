#!/usr/bin/env python3
"""Re-count the dashboard's headline numbers straight from GitHub.

    python audit_numbers.py http://localhost:8787/admin --repo owner/name [--ranges 7,30,90]
    CF_ACCESS_CLIENT_ID=ID CF_ACCESS_CLIENT_SECRET=SECRET \\
        python audit_numbers.py https://example.org/dashboard --repo owner/name

For each range it reads the exact window the dashboard counted (`window` in
/api/momentum and /api/ci) and counts the same thing on GitHub: PRs opened and
merged, issues opened and closed (search API), and build runs on the default
branch (Actions API) over the part of the window the dashboard has runs for,
plus stars and release downloads now. A mismatch means a window, filter or
denominator bug; that is the class of bug unit tests miss. Exits 1 on any
mismatch beyond the stated tolerance.

Dashboard auth (a deployed dashboard, behind Cloudflare Access): an Access
service token from CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET (preferred:
flags end up in shell history), or --client-id and --client-secret. A local
`wrangler dev` with ADMIN_DEV_NO_AUTH=1 needs none.

GitHub auth: GITHUB_TOKEN (`GITHUB_TOKEN="$(gh auth token)"` reuses a logged-in
GitHub CLI). Search allows 30 queries a minute, so a full run takes a minute or two.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable

TOKEN = os.environ.get("GITHUB_TOKEN", "")
MAX_RUNS_PER_QUERY = 1000
MIN_WINDOW_S = 60


def fetch(url: str, headers: dict[str, str]) -> tuple[object, dict[str, str]]:
    req = urllib.request.Request(
        url, headers={"User-Agent": "oss-dashboard-audit", **headers}
    )
    # A dropped connection or a 502/503/504 is retried twice; a third failure stops the audit with the URL.
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8")), {
                    k.lower(): v for k, v in r.headers.items()
                }
        except urllib.error.HTTPError as e:
            if e.code in (502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            ConnectionError,
            TimeoutError,
        ) as e:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise SystemExit(
                f"audit: GET {url.split('?')[0]} failed three times ({e}); run the audit again."
            ) from e
    raise RuntimeError("unreachable")


def gh(path: str) -> tuple[object, dict[str, str]]:
    h = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    for attempt in range(3):
        try:
            return fetch("https://api.github.com" + path, h)
        except urllib.error.HTTPError as e:
            # Search's secondary rate limit: wait it out rather than report a false mismatch.
            if e.code in (403, 429) and attempt < 2:
                time.sleep(int(e.headers.get("retry-after", "60")))
                continue
            raise
    raise RuntimeError("unreachable")


def search_count(q: str) -> int:
    data, _ = gh("/search/issues?per_page=1&q=" + urllib.parse.quote(q))
    time.sleep(2.1)  # 30 searches a minute
    return data["total_count"]


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso_s(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def runs_in(
    repo: str,
    start: int,
    end: int,
    get: Callable[[str], tuple[object, dict[str, str]]] = gh,
) -> list[dict]:
    """Every workflow run created in [start, end] (epoch seconds, inclusive), any branch. The Actions
    API returns at most 1,000 runs per query, so a window that reports more is halved, as the
    Worker's collector does; one that cannot be halved below a minute is an error, not a truncation.
    """
    created = urllib.parse.quote(f"{iso_s(start)}..{iso_s(end)}")
    out: list[dict] = []
    for page in range(1, 11):
        data, _ = get(
            f"/repos/{repo}/actions/runs?per_page=100&page={page}&status=completed&created={created}"
        )
        if page == 1 and data["total_count"] > MAX_RUNS_PER_QUERY:
            if end - start <= MIN_WINDOW_S:
                raise SystemExit(
                    f"audit: more than {MAX_RUNS_PER_QUERY:,} workflow runs between {iso_s(start)} and {iso_s(end)}; the Actions API would truncate."
                )
            mid = (start + end) // 2
            return runs_in(repo, start, mid, get) + runs_in(repo, mid + 1, end, get)
        out += data["workflow_runs"]
        if len(data["workflow_runs"]) < 100:
            break
    return out


def is_build_run(run: dict, build: dict) -> bool:
    """A decided run the dashboard counts as build health on the default branch."""
    branch = build["branch"]
    on_branch = run["head_branch"] == branch or (
        run["event"] == "merge_group"
        and (run["head_branch"] or "").startswith(f"gh-readonly-queue/{branch}/")
    )
    return (
        on_branch
        and run["status"] == "completed"
        and run["conclusion"] in ("success", "failure")
        and run["event"] in build["events"]
        and run["name"] not in build["excluded"]
    )


def build_window(ci: dict) -> tuple[datetime, datetime] | None:
    """The part of the dashboard's CI window it has runs for: [max(since, coverage.first), until]."""
    first = (ci.get("coverage") or {}).get("first")
    if not first:
        return None
    since, until = parse_iso(ci["window"]["since"]), parse_iso(ci["window"]["until"])
    return max(since, parse_iso(first)), until


def build_runs(
    repo: str, ci: dict, get: Callable[[str], tuple[object, dict[str, str]]] = gh
) -> tuple[list[dict], datetime, datetime] | None:
    win = build_window(ci)
    if win is None:
        return None
    since, until = win
    runs = runs_in(repo, int(since.timestamp()), int(until.timestamp()), get)
    kept = [
        r
        for r in runs
        if since <= parse_iso(r["created_at"]) <= until and is_build_run(r, ci["build"])
    ]
    return kept, since, until


mismatches = 0


def compare(
    label: str, dash: float | int | None, live: float | int, tol: float = 0
) -> None:
    global mismatches
    ok = dash is not None and abs(dash - live) <= tol
    mismatches += 0 if ok else 1
    print(
        f"  {'ok  ' if ok else 'DIFF'}  {label}: dashboard {dash}, GitHub {live}{f' (tolerance {tol:g})' if tol else ''}"
    )


def access_headers(client_id: str | None, client_secret: str | None) -> dict[str, str]:
    cid = client_id or os.environ.get("CF_ACCESS_CLIENT_ID")
    secret = client_secret or os.environ.get("CF_ACCESS_CLIENT_SECRET")
    if bool(cid) != bool(secret):
        print(
            "audit: an Access service token needs both its client id and secret "
            "(CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET, or --client-id and --client-secret); only one was given.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return (
        {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": secret} if cid else {}
    )


def main() -> int:
    # Workflow names and warnings can hold emoji; a piped Windows console is cp1252 and would crash on them.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--ranges", default="7,30,90")
    ap.add_argument(
        "--client-id", help="Access service token id (default: CF_ACCESS_CLIENT_ID)"
    )
    ap.add_argument(
        "--client-secret",
        help="Access service token secret (default: CF_ACCESS_CLIENT_SECRET)",
    )
    a = ap.parse_args()
    if not TOKEN:
        print(
            'No GitHub token: set GITHUB_TOKEN, e.g. GITHUB_TOKEN="$(gh auth token)" with a logged-in GitHub CLI. Anonymous search allows only 10 queries a minute.',
            file=sys.stderr,
        )
        return 2
    base = a.url.rstrip("/")
    dh = access_headers(a.client_id, a.client_secret)

    def dash(path: str) -> dict:
        return fetch(base + path, dh)[0]

    meta, _ = gh(f"/repos/{a.repo}")
    r = a.repo
    for n in a.ranges.split(","):
        m = dash(f"/api/momentum?range={n}&bots=1&exclude=")
        w = m["window"]
        span = f"{w['since'][:19]}Z..{w['until'][:19]}Z"
        print(f"Range {n} days ({span}):")
        s = m["summary"]
        compare(
            "PRs opened", s["prsOpened"], search_count(f"repo:{r} is:pr created:{span}")
        )
        compare(
            "PRs merged",
            s["prsMerged"],
            search_count(f"repo:{r} is:pr is:merged merged:{span}"),
        )
        compare(
            "issues opened",
            s["issuesOpened"],
            search_count(f"repo:{r} is:issue created:{span}"),
        )
        compare(
            "issues closed",
            s["issuesClosed"],
            search_count(f"repo:{r} is:issue closed:{span}"),
        )
        # No branch= filter: it would drop merge-queue runs, which run on gh-readonly-queue/<branch>/... branches.
        ci = dash(f"/api/ci?range={n}")
        if "build" not in ci:
            raise SystemExit(
                "audit: /api/ci has no `build` field (branch, events, excluded); the Worker predates this script. Redeploy it from the template."
            )
        got = build_runs(r, ci)
        if got is None:
            print(
                "  skip  build runs: the dashboard has no workflow runs yet (coverage.first is empty)."
            )
            continue
        runs, since, until = got
        print(
            f"  CI runs collected from {since.isoformat()[:19]}Z to {until.isoformat()[:19]}Z (events {', '.join(ci['build']['events'])} on {ci['build']['branch']})"
        )
        d_dec = sum(wf["passed"] + wf["failed"] for wf in ci["main"]["workflows"])
        d_ok = sum(wf["passed"] for wf in ci["main"]["workflows"])
        # Runs re-run after collection change conclusion; allow a few.
        tol = max(2, len(runs) * 0.02)
        compare("decided build runs on the default branch", d_dec, len(runs), tol=tol)
        compare(
            "passed build runs on the default branch",
            d_ok,
            sum(1 for x in runs if x["conclusion"] == "success"),
            tol=tol,
        )

    print("Totals now:")
    t = dash("/api/traction")
    compare(
        "stars (snapshot vs live)",
        t["starsHeadline"],
        meta["stargazers_count"],
        tol=max(5, meta["stargazers_count"] * 0.01),
    )
    d = dash("/api/downloads?range=30")
    if not d.get("empty"):
        live = 0
        page = f"/repos/{r}/releases?per_page=100"
        while page:
            rels, h = gh(page)
            live += sum(
                x["download_count"]
                for rel in rels
                if not rel["draft"]
                for x in rel["assets"]
            )
            nxt = [p for p in h.get("link", "").split(",") if 'rel="next"' in p]
            page = (
                nxt[0].split(";")[0].strip()[1:-1].replace("https://api.github.com", "")
                if nxt
                else None
            )
        snap = sum(x["install"] + x["meta"] for x in d["releases"])
        # The snapshot is from the last collection; live can only be higher, by about a day of downloads.
        compare(
            "release downloads, all files (snapshot vs live)",
            snap,
            live,
            tol=max(50, live * 0.05),
        )
    print(f"{mismatches} mismatch(es).")
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
