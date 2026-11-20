# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Dispatch tonight's audit and confirm its run before reporting success."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_AUDIT_DIR = Path(__file__).resolve().parent
_UTIL_DIR = _AUDIT_DIR.parents[1] / "util"
for _entry in (str(_AUDIT_DIR), str(_UTIL_DIR)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from audit_history import (  # noqa: E402  pylint: disable=wrong-import-position
    ROTATION_TITLE,
    parse_time,
)
from gh_retry import (  # noqa: E402  pylint: disable=wrong-import-position
    wait_out_rate_limit,
)
from github_release_retry import (  # noqa: E402  pylint: disable=wrong-import-position
    RateLimited,
    _is_rate_limit_error,
)

# The installation API budget refills hourly, so dispatch may wait out a full reset.
DISPATCH_BUDGET = 3900
CONFIRM_BUDGET = 300
# GitHub stamps created_at by its own clock; allow for a runner clock that runs ahead.
CLOCK_SLACK = timedelta(seconds=60)


def _split_http_response(stdout: str) -> tuple[str, str]:
    parts = re.split(r"\r?\n\r?\n", stdout, maxsplit=1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _rate_limited(headers: str, error: str) -> RateLimited | None:
    lines = headers.splitlines()
    match = re.match(r"HTTP/\S+\s+(\d+)", lines[0]) if lines else None
    status = int(match[1]) if match else None
    if status not in (403, 429):
        return None
    parsed = {
        key.strip().lower(): value.strip()
        for line in lines[1:]
        if ":" in line
        for key, _, value in [line.partition(":")]
    }
    spent = parsed.get("x-ratelimit-remaining") == "0"
    retry_after = parsed.get("retry-after", "")
    reset = parsed.get("x-ratelimit-reset", "") if spent else ""
    if (
        status != 429
        and not spent
        and not retry_after.isdigit()
        and not _is_rate_limit_error(error)
    ):
        return None
    return RateLimited(
        error,
        reset=int(reset) if reset.isdigit() else None,
        retry_after=int(retry_after) if retry_after.isdigit() else None,
    )


def request(args: list[str], deadline: float) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("GitHub request deadline exceeded")

    def attempt() -> str:
        result = subprocess.run(
            ["gh", "api", "--include", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=max(1.0, deadline - time.monotonic()),
        )
        headers, body = _split_http_response(result.stdout or "")
        if result.returncode == 0:
            return body
        error = (result.stderr or "") + body
        limited = _rate_limited(headers, error)
        if limited is not None:
            raise limited
        raise RuntimeError(error)

    return wait_out_rate_limit(
        attempt,
        remaining,
        label=f"gh api {args[0]}",
        sleep=time.sleep,
        monotonic=time.monotonic,
        now=time.time,
    )


def main(repo: str, workflow: str, night: str) -> None:
    endpoint = f"repos/{repo}/actions/workflows/{workflow}"
    started = datetime.now(timezone.utc).replace(microsecond=0) - CLOCK_SLACK
    request(
        [
            f"{endpoint}/dispatches",
            "-f",
            "ref=main",
            "-f",
            f"inputs[rotation_night]={night}",
        ],
        time.monotonic() + DISPATCH_BUDGET,
    )
    deadline = time.monotonic() + CONFIRM_BUDGET
    suffix = f" — {ROTATION_TITLE} {night}"
    while time.monotonic() < deadline:
        body = request(
            [f"{endpoint}/runs?branch=main&event=workflow_dispatch&per_page=100"],
            deadline,
        )
        for run in json.loads(body)["workflow_runs"]:
            if (run.get("display_title") or "").endswith(suffix) and (
                parse_time(run["created_at"]) >= started
            ):
                with open(
                    os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8"
                ) as summary:
                    print(
                        f"Started `{workflow}` for {ROTATION_TITLE} {night}: "
                        f"{run['html_url']}",
                        file=summary,
                    )
                return
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    raise TimeoutError(f"Could not confirm {workflow} for {ROTATION_TITLE} {night}")


if __name__ == "__main__":
    main(*sys.argv[1:])
