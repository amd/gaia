# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Wait for this machine's model slot, then run one command while holding it.

Parallel fix sessions share one Lemonade and one GPU. ``gaia eval`` already
refuses to start beside another eval (``gaia.eval.eval_lock``); this waits its
turn on the same lock instead of failing, so each session can queue its repro
or verification and run it alone.

    python util/machine_turn.py --label "fix #123 repro" -- gaia eval agent --category rag_quality
    python util/machine_turn.py -- python -m pytest tests/integration/test_x.py

The command runs with ``GAIA_EVAL_NO_LOCK=1`` because this process already
holds the lock: a ``gaia eval`` inside it would otherwise refuse to start.
Exits with the command's own exit code, or 124 (as ``timeout(1)`` does) if
the wait timed out. On Ctrl-C or SIGTERM it stops the command and waits for
it before releasing the lock, so the next session never loads a model beside
an orphaned run. (A hard kill, including any kill on Windows, can't be caught.)
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from gaia.daemon.lock import try_lock, unlock  # noqa: E402
from gaia.eval.eval_lock import (  # noqa: E402
    BYPASS_ENV,
    HOLDER_FILE,
    LOCK_FILE,
    _holder_description,
)

TIMED_OUT = 124
_STOP_GRACE_S = 30.0


def wait_for_turn(label: str, timeout_s: float, poll_s: float) -> int:
    """Block until the machine lock is free, take it, and return its fd.

    Raises:
        TimeoutError: The lock was still held after *timeout_s*.
    """
    deadline = time.monotonic() + timeout_s
    announced = None
    while True:
        fd = os.open(str(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
        if try_lock(fd):
            HOLDER_FILE.write_text(
                json.dumps(
                    {"pid": os.getpid(), "command": label, "started": time.time()}
                ),
                encoding="utf-8",
            )
            return fd
        os.close(fd)
        holder = _holder_description()
        if holder != announced:
            print(f"[machine_turn] waiting for {holder} to finish", flush=True)
            announced = holder
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"{LOCK_FILE} was still held by {holder} after {timeout_s:.0f}s. "
                "Check that run is alive, or raise --timeout."
            )
        time.sleep(poll_s)


def _raise_on_sigterm(signum, _frame):
    raise SystemExit(128 + signum)


def _restore_handler(signum: int, handler) -> None:
    # None means the old handler came from C (embedding); Python can't reinstall it.
    if handler is None:
        handler = (
            signal.default_int_handler if signum == signal.SIGINT else signal.SIG_DFL
        )
    signal.signal(signum, handler)


def _deliver_held_stop(signum: int, sigint_handler) -> None:
    if signum != signal.SIGINT:
        _raise_on_sigterm(signum, None)
    elif callable(sigint_handler):
        sigint_handler(signum, None)
    elif sigint_handler != signal.SIG_IGN:
        raise KeyboardInterrupt


def _run_to_completion(command: list[str], env: dict[str, str]) -> int:
    """Run *command*; on Ctrl-C or SIGTERM, stop it before returning."""
    # A stop that lands while the child is being created is held until the
    # child is in hand, or it would escape before there is a process to stop.
    held: list[int] = []

    def hold(signum, _frame):
        held.append(signum)

    previous_int = signal.signal(signal.SIGINT, hold)
    previous_term = signal.signal(signal.SIGTERM, hold)
    proc = None
    try:
        proc = subprocess.Popen(command, env=env)
        _restore_handler(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, _raise_on_sigterm)
        for signum in held:
            _deliver_held_stop(signum, previous_int)
        # The queued command may run for hours; short waits keep Ctrl-C
        # deliverable on Windows, where an untimed wait blocks it.
        while True:
            try:
                return proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=_STOP_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=_STOP_GRACE_S)
        raise
    finally:
        _restore_handler(signal.SIGINT, previous_int)
        _restore_handler(signal.SIGTERM, previous_term)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--label", default="", help="what the holder file says is running"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=12 * 3600,
        help="seconds to wait (default 12 h)",
    )
    parser.add_argument(
        "--poll", type=float, default=30.0, help="seconds between checks"
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("give the command to run after --")

    label = args.label or " ".join(command)
    try:
        fd = wait_for_turn(label, args.timeout, args.poll)
    except TimeoutError as e:
        print(f"[machine_turn] {e}", file=sys.stderr)
        return TIMED_OUT
    try:
        print(f"[machine_turn] your turn: {label}", flush=True)
        env = {**os.environ, BYPASS_ENV: "1"}
        return _run_to_completion(command, env)
    finally:
        HOLDER_FILE.unlink(missing_ok=True)
        unlock(fd)
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
