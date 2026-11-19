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

Python callers import it instead: ``run_command`` for a gh command, and
``wait_out_rate_limit`` for any call that raises ``RateLimited``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Sequence, TextIO, TypeVar

from github_release_retry import RateLimited, _is_rate_limit_error, core_budget_reset

T = TypeVar("T")

# The reset epoch can lag the server's clock by a second or two.
_RESET_SLACK_SECONDS = 5


class RateLimitWaitExceeded(RuntimeError):
    """GitHub kept rate-limiting the call past the wait bound."""


class _CommandRateLimited(RateLimited):
    def __init__(self, done: subprocess.CompletedProcess, **kwargs) -> None:
        super().__init__((done.stdout or "") + (done.stderr or ""), **kwargs)
        self.done = done


def _quoted(text: str) -> str:
    """Show an earlier attempt's output without GitHub parsing its ::error:: lines."""
    return "".join(f"  | {line}\n" for line in text.splitlines())


def wait_out_rate_limit(
    operation: Callable[[], T],
    max_wait_seconds: int,
    *,
    label: str,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
    now: Callable[[], float] | None = None,
) -> T:
    """Return ``operation()``, calling it again while it raises ``RateLimited``.

    The wait is the retry-after or budget reset the error names, else a backoff
    from 15 s to 120 s. A wait that would pass ``max_wait_seconds`` raises
    ``RateLimitWaitExceeded``; any other exception propagates at once.
    """
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    now = now or time.time
    deadline = monotonic() + max_wait_seconds
    attempt = 0
    while True:
        try:
            return operation()
        except RateLimited as exc:
            if exc.retry_after is not None:
                delay = max(1, exc.retry_after)
                why = f"GitHub asked to retry after {delay}s"
            elif exc.reset is not None:
                delay = max(1, int(exc.reset - now()) + _RESET_SLACK_SECONDS)
                why = (
                    "this repository's GitHub API budget is spent until "
                    f"{datetime.fromtimestamp(exc.reset, timezone.utc):%H:%M:%S} UTC"
                )
            else:
                delay = min(120, 15 * (2 ** min(attempt, 3)))
                why = "GitHub returned a rate limit"
            if monotonic() + delay > deadline:
                raise RateLimitWaitExceeded(
                    f"{label} was rate limited: {why}, and waiting it out would "
                    f"pass the {max_wait_seconds}s bound. Re-run this job once "
                    "the budget resets."
                ) from exc
            print(
                f"::warning::{why}; running {label} again in {delay}s "
                f"(attempt {attempt + 2})",
                file=sys.stderr,
            )
            sleep(delay)
            attempt += 1


def _command_attempt(
    cmd: Sequence[str],
    runner: Callable[..., subprocess.CompletedProcess],
    budget_reset: Callable[[], int | None],
    log: TextIO,
    **run_kwargs,
) -> Callable[[], subprocess.CompletedProcess]:
    def attempt() -> subprocess.CompletedProcess:
        done = runner(
            list(cmd), capture_output=True, text=True, check=False, **run_kwargs
        )
        if done.returncode == 0:
            return done
        reset = budget_reset()
        output = (done.stdout or "") + (done.stderr or "")
        if reset is None and not _is_rate_limit_error(output):
            return done
        log.write(_quoted(output))
        raise _CommandRateLimited(done, reset=reset)

    return attempt


def run_command(
    cmd: Sequence[str],
    max_wait_seconds: int,
    *,
    timeout: float | None = None,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    budget_reset: Callable[[], int | None] = core_budget_reset,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
    now: Callable[[], float] | None = None,
) -> subprocess.CompletedProcess:
    """Run ``cmd`` like ``subprocess.run(capture_output=True, text=True)``.

    Returns the first success or the first failure that is not a rate limit.
    Raises ``RateLimitWaitExceeded`` when the rate limit outlasts the bound, and
    ``subprocess.TimeoutExpired`` when one attempt passes ``timeout``. Earlier
    attempts' output goes to stderr, so the caller's stdout stays its own.
    """
    attempt = _command_attempt(
        cmd,
        runner or subprocess.run,
        budget_reset,
        sys.stderr,
        encoding="utf-8",
        timeout=timeout,
    )
    try:
        return wait_out_rate_limit(
            attempt,
            max_wait_seconds,
            label=" ".join(cmd),
            sleep=sleep,
            monotonic=monotonic,
            now=now,
        )
    except RateLimitWaitExceeded as exc:
        raise RateLimitWaitExceeded(
            f"{exc} gh can report a spent budget as 'release not found'."
        ) from exc.__cause__


def run(
    cmd: Sequence[str],
    max_wait_seconds: int,
    *,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    budget_reset: Callable[[], int | None] = core_budget_reset,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
    now: Callable[[], float] | None = None,
) -> int:
    attempt = _command_attempt(
        cmd, runner or subprocess.run, budget_reset, sys.stdout
    )
    try:
        done = wait_out_rate_limit(
            attempt,
            max_wait_seconds,
            label=" ".join(cmd),
            sleep=sleep,
            monotonic=monotonic,
            now=now,
        )
    except RateLimitWaitExceeded as exc:
        print(
            f"::error::{exc} gh can report a spent budget as 'release not found'.",
            file=sys.stderr,
        )
        return exc.__cause__.done.returncode or 1
    sys.stdout.write(done.stdout or "")
    sys.stderr.write(done.stderr or "")
    return done.returncode


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
