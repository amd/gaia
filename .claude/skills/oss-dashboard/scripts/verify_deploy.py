#!/usr/bin/env python3
"""Check that a deployed dashboard is private and healthy.

    CF_ACCESS_CLIENT_ID=ID CF_ACCESS_CLIENT_SECRET=SECRET \\
        python verify_deploy.py https://example.org/dashboard [--also-unprotected https://name.account.workers.dev/dashboard]
    python verify_deploy.py https://example.org/dashboard --anonymous-only

Anonymous checks: the page and /api/status must redirect to the Cloudflare
Access sign-in (a 401/403 from the Worker itself means Access does not cover
that path), and the data API and the collector must refuse. Signed-in checks,
with an Access service token: a signed-in caller gets data, the collector
refuses calls without its header or from another site, and every source's
latest collection succeeded recently. The service token comes from
CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET (preferred: flags end up in
shell history) or --client-id and --client-secret; without one, pass
--anonymous-only. Exits 1 when any check fails, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)
REDIRECTS = (301, 302, 303, 307, 308)


def call(
    method: str, url: str, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], str]:
    req = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": "oss-dashboard-verify", **(headers or {})},
    )
    try:
        with OPENER.open(req, timeout=30) as r:
            return (
                r.status,
                {k.lower(): v for k, v in r.headers.items()},
                r.read().decode("utf-8", "replace"),
            )
    except urllib.error.HTTPError as e:
        return (
            e.code,
            {k.lower(): v for k, v in e.headers.items()},
            e.read().decode("utf-8", "replace"),
        )


results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((ok, what))
    print(f"  {'PASS' if ok else 'FAIL'}  {what}")


def to_access(status: int, headers: dict[str, str]) -> bool:
    """A redirect to the Cloudflare Access sign-in (<team>.cloudflareaccess.com)."""
    if status not in REDIRECTS:
        return False
    host = (urllib.parse.urlsplit(headers.get("location", "")).hostname or "").lower()
    return host.endswith(".cloudflareaccess.com")


def refused(status: int, headers: dict[str, str]) -> bool:
    """Anonymous access is refused: a redirect to the Access sign-in, or 401/403 from the Worker."""
    return status in (401, 403) or to_access(status, headers)


def behind_access(
    path: str, app_path: str, status: int, headers: dict[str, str]
) -> tuple[bool, str]:
    """`path` must be behind Access: the Worker refusing on its own means Access lets it through.
    `app_path` is the dashboard's base path, which the Access application must cover."""
    if to_access(status, headers):
        return True, f"GET {path} redirects to Access sign-in (HTTP {status})"
    if status in (401, 403):
        return (
            False,
            f"GET {path} answered HTTP {status} from the Worker: Access does not cover {path}; add both {app_path} and {app_path}/* to the Access application",
        )
    loc = f" to {headers.get('location', '')[:80]}" if status in REDIRECTS else ""
    return False, f"GET {path} is not behind Access (HTTP {status}{loc})"


def finished(row: dict) -> datetime:
    return datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00"))


def latest_per_source(log: list[dict]) -> dict[str, dict]:
    """The most recent row of each source; the log can hold several rows per source, in any order."""
    latest: dict[str, dict] = {}
    for row in log:
        cur = latest.get(row["source"])
        if cur is None or finished(row) > finished(cur):
            latest[row["source"]] = row
    return latest


def main() -> int:
    # Workflow names and warnings can hold emoji; a piped Windows console is cp1252 and would crash on them.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("url", help="dashboard URL, e.g. https://example.org/dashboard")
    ap.add_argument(
        "--client-id", help="Access service token id (default: CF_ACCESS_CLIENT_ID)"
    )
    ap.add_argument(
        "--client-secret",
        help="Access service token secret (default: CF_ACCESS_CLIENT_SECRET)",
    )
    ap.add_argument(
        "--anonymous-only",
        action="store_true",
        help="run only the anonymous checks (no service token)",
    )
    ap.add_argument(
        "--also-unprotected",
        action="append",
        default=[],
        help="another hostname that must also refuse (e.g. workers.dev)",
    )
    ap.add_argument("--stale-hours", type=float, default=36)
    a = ap.parse_args()
    base = a.url.rstrip("/")
    cid = a.client_id or os.environ.get("CF_ACCESS_CLIENT_ID")
    secret = a.client_secret or os.environ.get("CF_ACCESS_CLIENT_SECRET")
    if bool(cid) != bool(secret):
        print(
            "verify: a service token needs both its client id and secret (CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET); only one was given.",
            file=sys.stderr,
        )
        return 2
    if not cid and not a.anonymous_only:
        print(
            "verify: no Access service token. Set CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET (Access > Service Auth, see README.md "
            "'Deploy and sign-in' step 4), or pass --anonymous-only to run just the anonymous checks.",
            file=sys.stderr,
        )
        return 2

    print("Anonymous caller:")
    path = urllib.parse.urlsplit(base).path or "/"
    for u, p in (
        (base, path),
        (base + "/api/status", path.rstrip("/") + "/api/status"),
    ):
        s, h, _ = call("GET", u)
        ok, what = behind_access(p, path, s, h)
        check(ok, what)
    s, h, _ = call("GET", base + "/api/downloads")
    check(refused(s, h), f"GET {base}/api/downloads is refused (HTTP {s})")
    s, h, _ = call("POST", base + "/api/collect", {"x-admin-action": "collect"})
    check(refused(s, h), f"POST {base}/api/collect is refused (HTTP {s})")
    # A hostname Access is not meant to cover (workers.dev) must still be refused by the Worker itself.
    for u in [x.rstrip("/") + "/api/status" for x in a.also_unprotected]:
        s, h, _ = call("GET", u)
        check(refused(s, h), f"GET {u} is refused (HTTP {s})")

    if a.anonymous_only:
        print("--anonymous-only: signed-in checks skipped.")
        return 0 if all(ok for ok, _ in results) else 1

    auth = {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": secret}
    print("Service token:")
    s, _, body = call("GET", base + "/api/status", auth)
    check(s == 200, f"GET /api/status with the token answers 200 (HTTP {s})")
    s2, _, _ = call("POST", base + "/api/collect?only=repo", auth)
    check(s2 == 403, f"collect without x-admin-action is refused (HTTP {s2})")
    s3, _, _ = call(
        "POST",
        base + "/api/collect?only=repo",
        {**auth, "x-admin-action": "collect", "Origin": "https://evil.example"},
    )
    check(s3 == 403, f"collect from another site is refused (HTTP {s3})")
    if s != 200:
        return 1
    latest = latest_per_source(json.loads(body).get("log", []))
    check(bool(latest), f"collection has run ({len(latest)} sources logged)")
    now = datetime.now(timezone.utc)
    for src, row in sorted(latest.items()):
        age = (now - finished(row)).total_seconds() / 3600
        # GitHub computes repository statistics in the background on first request: not a deploy failure.
        pending = not row["ok"] and "still computing" in (row.get("detail") or "")
        state = (
            "ok"
            if row["ok"]
            else (
                "pending (GitHub is computing statistics; read on the next run)"
                if pending
                else "FAILED"
            )
        )
        check(
            (bool(row["ok"]) or pending) and age <= a.stale_hours,
            f"source '{src}': {state}, {age:.0f} h ago — {(row.get('detail') or '')[:120]}",
        )
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
