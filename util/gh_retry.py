# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run a command that calls the GitHub API, waiting out a spent API budget.

Every workflow in the repository shares one GITHUB_TOKEN API budget, so a busy
hour can spend it just as a release publishes. gh then reports a release that
exists as "release not found": it races a REST lookup against a GraphQL draft
lookup and prefers not-found when both fail. A failed command is therefore run
again only when the token's core budget is spent or its output names a rate
limit, and only while the wait fits the bound. Any other failure returns the
command's own exit code at once.

    python3 util/gh_retry.py --max-wait-seconds 3600 -- gh release upload ...
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Sequence

from github_release_retry import _is_rate_limit_error, core_budget_reset

# The reset epoch can lag the server's clock by a second or two.
_RESET_SLACK_SECONDS = 5


def _quoted(text: str) -> str:
    """Show an earlier attempt's output without GitHub parsing its ::error:: lines."""
    return "".join(f"  | {line}\n" for line in text.splitlines())


def run(
    cmd: Sequence[str],
    max_wait_seconds: int,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    budget_reset: Callable[[], int | None] = core_budget_reset,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    now: Callable[[], float] = time.time,
) -> int:
    deadline = monotonic() + max_wait_seconds
    attempt = 0
    while True:
        done = runner(list(cmd), capture_output=True, text=True, check=False)
        output = (done.stdout or "") + (done.stderr or "")
        if done.returncode == 0:
            sys.stdout.write(done.stdout or "")
            sys.stderr.write(done.stderr or "")
            return 0
        reset = budget_reset()
        if reset is None and not _is_rate_limit_error(output):
            sys.stdout.write(done.stdout or "")
            sys.stderr.write(done.stderr or "")
            return done.returncode
        if reset is not None:
            delay = max(1, int(reset - now()) + _RESET_SLACK_SECONDS)
            why = (
                "this repository's GitHub API budget is spent until "
                f"{datetime.fromtimestamp(reset, timezone.utc):%H:%M:%S} UTC"
            )
        else:
            delay = min(120, 15 * (2 ** min(attempt, 3)))
            why = "GitHub returned a rate limit"
        sys.stdout.write(_quoted(output))
        if monotonic() + delay > deadline:
            print(
                f"::error::{' '.join(cmd)} failed because {why}, and waiting it "
                f"out would pass the {max_wait_seconds}s bound. gh can report "
                "this as 'release not found'. Re-run this job once the budget "
                "resets.",
                file=sys.stderr,
            )
            return done.returncode or 1
        print(
            f"::warning::{why}; running {cmd[0]} again in {delay}s "
            f"(attempt {attempt + 2})",
            file=sys.stderr,
        )
        sleep(delay)
        attempt += 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-wait-seconds", type=int, required=True)
    parser.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if not cmd:
        parser.error("name the command to run after --")
    return run(cmd, args.max_wait_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
