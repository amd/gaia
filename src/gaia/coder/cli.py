# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``gaia-coder`` CLI entry point (§3.1).

Phase 1 landed the full subcommand surface as stubs. Phase 2 (this PR)
replaces the stubs for the four subcommands the **eval harness** drives
(§10.2):

* ``daemon`` — long-lived process that polls an inbox and writes eval
  artifacts for each task.
* ``ask`` — posts a task body to the daemon's inbox.
* ``wait`` — blocks until a given task_id's artifacts are written.
* ``stop`` — signals the running daemon to exit.

All the other subcommands keep their Phase 1 stub handlers; their real
implementations land in Phases 3-8.

**Important:** the Phase 2 daemon is a **deliberate stub**. It does not
run the real self-review / self-correction loop — it only writes
artifact-shaped files to ``$SANDBOX/.eval-artifacts/<task_id>/`` so the
eval plumbing (CLI subprocess, task-to-artifact round-trip, harness
scoring) can be exercised end-to-end before Phases 3-8 land. The stub
artifacts are clearly marked ``stub: true`` in ``pass_results.json`` so
a later run against a real daemon cannot be confused with a stub run.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# Eval-artifact layout (§10.2).
# ---------------------------------------------------------------------------

# The six artifact files the eval harness collects per task. Kept as a
# module-level constant so runner + daemon + scorer all agree on the
# list (and ``test_runner_collects_all_6_artifacts`` can import it).
ARTIFACT_FILENAMES: tuple[str, ...] = (
    "diff.patch",
    "regression_test.py",
    "pass_results.json",
    "confidence.txt",
    "standup.md",
    "trace.jsonl",
)

# Subdirectories under ``$SANDBOX/.eval-artifacts/`` used by the stub
# daemon for IPC. Kept short to make diagnostic ``ls`` output readable.
_EVAL_ARTIFACTS_DIR = ".eval-artifacts"
_INBOX_DIR = ".inbox"
_DAEMON_PID_FILE = ".daemon.pid"
_DONE_MARKER = ".done"

# How often the daemon poll loop wakes up. Kept small so the eval
# harness has a predictable latency ceiling without spinning the CPU.
_DAEMON_POLL_INTERVAL_S = 0.1
# How often ``wait`` polls for the ``.done`` marker. Matches the daemon
# cadence.
_WAIT_POLL_INTERVAL_S = 0.1


# ---------------------------------------------------------------------------
# Stub body shared by every subcommand that has not been promoted past
# Phase 1 yet.
# ---------------------------------------------------------------------------


def _not_yet_implemented(subcommand: str) -> Callable[[argparse.Namespace], int]:
    """Return a handler that prints a stub message and exits ``0``.

    Phase 1 subcommands are intentionally no-ops so the CLI surface can
    be wired, documented, tested, and imported by downstream tasks
    without waiting for the full implementation. Every real
    implementation replaces the returned callable with a real handler in
    its own PR.
    """

    def handler(_args: argparse.Namespace) -> int:
        print(f"gaia-coder {subcommand}: not yet implemented")
        return 0

    handler.__name__ = f"_handle_{subcommand.replace('-', '_')}"
    return handler


# ---------------------------------------------------------------------------
# Paths + helpers used by the Phase 2 subcommands.
# ---------------------------------------------------------------------------


def _artifacts_root(sandbox: Path) -> Path:
    """Return ``$SANDBOX/.eval-artifacts/``, creating it if missing."""
    root = Path(sandbox) / _EVAL_ARTIFACTS_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _inbox_dir(sandbox: Path) -> Path:
    inbox = _artifacts_root(sandbox) / _INBOX_DIR
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


def _pid_file(sandbox: Path) -> Path:
    return _artifacts_root(sandbox) / _DAEMON_PID_FILE


def _parse_task_id_from_body(body: str) -> Optional[str]:
    """Return the ``id:`` field from the task front-matter, or ``None``.

    The front-matter is a YAML-ish block delimited by ``---`` lines at
    the top of the file. We only parse the first ``id: X`` we see; the
    full schema is validated by the suite loader.
    """
    lines = body.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return None
        if stripped.startswith("id:"):
            raw = stripped[len("id:") :].strip()
            # Strip optional quotes.
            if (raw.startswith('"') and raw.endswith('"')) or (
                raw.startswith("'") and raw.endswith("'")
            ):
                raw = raw[1:-1]
            return raw or None
    return None


def _write_stub_artifacts(
    sandbox: Path, task_id: str, task_body: str, options: dict
) -> Path:
    """Write the six stub artifact files for ``task_id`` and mark done.

    The stub daemon does not run a real agent loop; it just produces
    artifact-shaped output so the harness plumbing can be tested. Each
    file is marked ``stub: true`` where the format allows so a later
    real-daemon run cannot be silently confused with a stub run.
    """
    task_dir = _artifacts_root(sandbox) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    # diff.patch — empty valid patch (harness ``git apply --check`` will
    # treat an empty file as "no changes", which applies cleanly).
    (task_dir / "diff.patch").write_text("", encoding="utf-8")

    # regression_test.py — a pytest-compatible no-op so the suite scorer
    # can shell out to pytest against it without syntax errors.
    (task_dir / "regression_test.py").write_text(
        "# Stub regression test written by gaia-coder daemon (Phase 2).\n"
        "def test_stub_regression() -> None:\n"
        "    assert True\n",
        encoding="utf-8",
    )

    # pass_results.json — explicitly stubbed; real Pass-1..7 results
    # land in Phases 4-6.
    pass_results = {
        "stub": True,
        "task_id": task_id,
        "passes": {
            "pass_1_self_review": None,
            "pass_2_tests": None,
            "pass_3_lint": None,
            "pass_4_persona": None,
            "pass_5_security": None,
            "pass_6_license": None,
            "pass_7_standup": None,
        },
    }
    (task_dir / "pass_results.json").write_text(
        json.dumps(pass_results, indent=2), encoding="utf-8"
    )

    # confidence.txt — 50 (mid-range neutral) for the stub.
    (task_dir / "confidence.txt").write_text("50\n", encoding="utf-8")

    # standup.md — a minimal stub that includes the task_id so a human
    # reader can tell which task it came from.
    (task_dir / "standup.md").write_text(
        f"# Stub standup for {task_id}\n\n"
        "_Produced by gaia-coder daemon in Phase 2 stub mode. "
        "Real standups land in Phase 7._\n",
        encoding="utf-8",
    )

    # trace.jsonl — a single line capturing what we did so scorers can
    # tell a stub run apart from a real run.
    trace_event = {
        "event": "stub_daemon_completed",
        "task_id": task_id,
        "timestamp": time.time(),
        "options": options,
        "task_body_bytes": len(task_body.encode("utf-8")),
    }
    (task_dir / "trace.jsonl").write_text(
        json.dumps(trace_event) + "\n", encoding="utf-8"
    )

    # Mark the task complete. The ``.done`` marker is NOT one of the six
    # collected artifacts — it's just the handshake with ``wait``.
    (task_dir / _DONE_MARKER).write_text(
        json.dumps({"task_id": task_id, "completed_at": time.time()}),
        encoding="utf-8",
    )
    return task_dir


# ---------------------------------------------------------------------------
# ``gaia-coder daemon`` — Phase 2 stub daemon.
# ---------------------------------------------------------------------------


def _handle_daemon(args: argparse.Namespace) -> int:
    """Run the stub daemon — poll the inbox and write stub artifacts.

    The daemon exits cleanly on ``SIGTERM`` (from ``gaia-coder stop``)
    or ``SIGINT`` (Ctrl-C). It does not fork/detach — callers that want
    backgrounded daemons should append ``&`` or use ``nohup``; this
    matches the eval harness which uses ``subprocess.Popen`` directly.
    """
    sandbox = Path(args.sandbox).resolve()
    if not sandbox.is_dir():
        print(
            f"gaia-coder daemon: --sandbox must be an existing directory: {sandbox}",
            file=sys.stderr,
        )
        return 2

    # Compose the run options blob that ends up in every task's trace.
    options = {
        "capability_tier": args.capability_tier,
        "no_network_writes": bool(args.no_network_writes),
        "stub": True,
        "sandbox": str(sandbox),
    }

    pid_file = _pid_file(sandbox)
    if pid_file.exists():
        existing_pid = pid_file.read_text(encoding="utf-8").strip()
        # Check if the previous daemon is still alive. ``kill(pid, 0)``
        # raises ``OSError`` if the process is gone, which is the
        # standard liveness probe on POSIX.
        try:
            os.kill(int(existing_pid), 0)
        except (OSError, ValueError):
            # Stale pid file — remove and continue.
            pid_file.unlink(missing_ok=True)
        else:
            print(
                f"gaia-coder daemon: another daemon is already running "
                f"(pid={existing_pid}); run `gaia-coder stop` first.",
                file=sys.stderr,
            )
            return 3

    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    inbox = _inbox_dir(sandbox)

    stop_flag = {"stop": False}

    def _handle_signal(signum: int, _frame: object) -> None:
        # Using a dict-as-flag keeps mypy / lint happy without globals.
        stop_flag["stop"] = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    tier = args.capability_tier
    net = "no-net" if args.no_network_writes else "net-allowed"
    print(
        f"gaia-coder daemon: started (pid={os.getpid()}, tier={tier}, "
        f"{net}, sandbox={sandbox}) — stub mode",
        flush=True,
    )

    try:
        while not stop_flag["stop"]:
            # Drain any pending task files in the inbox. We process one
            # per iteration so the stop flag is honoured promptly even
            # under heavy load.
            pending = sorted(inbox.glob("*.md"))
            if pending:
                task_file = pending[0]
                task_id = task_file.stem
                task_body = task_file.read_text(encoding="utf-8")
                try:
                    _write_stub_artifacts(sandbox, task_id, task_body, options)
                finally:
                    task_file.unlink(missing_ok=True)
                print(
                    f"gaia-coder daemon: completed task {task_id} (stub)",
                    flush=True,
                )
                continue
            time.sleep(_DAEMON_POLL_INTERVAL_S)
    finally:
        pid_file.unlink(missing_ok=True)
        print("gaia-coder daemon: exiting", flush=True)
    return 0


# ---------------------------------------------------------------------------
# ``gaia-coder ask`` — post a task body to the daemon inbox.
# ---------------------------------------------------------------------------


def _handle_ask(args: argparse.Namespace) -> int:
    """Read a task body from ``--body`` or stdin, write to inbox.

    Prints the assigned ``task_id`` to stdout on success. The eval
    runner captures that stdout line and passes it to ``wait``.
    """
    sandbox = Path(args.sandbox).resolve() if args.sandbox else Path.cwd()
    if not sandbox.is_dir():
        print(
            f"gaia-coder ask: --sandbox must be an existing directory: {sandbox}",
            file=sys.stderr,
        )
        return 2

    if args.body == "-":
        body = sys.stdin.read()
    else:
        body = args.body

    if not body.strip():
        print("gaia-coder ask: empty task body", file=sys.stderr)
        return 2

    task_id = _parse_task_id_from_body(body) or f"task-{uuid.uuid4().hex[:12]}"
    inbox = _inbox_dir(sandbox)
    inbox_file = inbox / f"{task_id}.md"
    inbox_file.write_text(body, encoding="utf-8")
    # The eval runner reads this line with a plain splitlines() / rstrip
    # — keep it on a single line and terminated by newline.
    print(task_id)
    return 0


# ---------------------------------------------------------------------------
# ``gaia-coder wait`` — block until ``.done`` marker appears.
# ---------------------------------------------------------------------------


def _handle_wait(args: argparse.Namespace) -> int:
    """Poll ``$SANDBOX/.eval-artifacts/<task_id>/.done`` until it exists.

    Exits 0 on completion, 124 on timeout (matches GNU ``timeout``
    convention so CI can distinguish timeout-vs-error in exit codes).
    """
    sandbox = Path(args.sandbox).resolve() if args.sandbox else Path.cwd()
    task_dir = _artifacts_root(sandbox) / args.task_id
    done_marker = task_dir / _DONE_MARKER
    deadline = time.monotonic() + args.timeout_min * 60.0
    while time.monotonic() < deadline:
        if done_marker.exists():
            print(str(task_dir))
            return 0
        time.sleep(_WAIT_POLL_INTERVAL_S)
    print(
        f"gaia-coder wait: timed out after {args.timeout_min} min "
        f"(task_id={args.task_id})",
        file=sys.stderr,
    )
    return 124


# ---------------------------------------------------------------------------
# ``gaia-coder stop`` — signal the running daemon to exit.
# ---------------------------------------------------------------------------


def _handle_stop(args: argparse.Namespace) -> int:
    """Signal the running daemon, optionally waiting for exit."""
    sandbox = Path(args.sandbox).resolve() if args.sandbox else Path.cwd()
    pid_file = _pid_file(sandbox)
    if not pid_file.exists():
        # Nothing to stop — treat as success so teardown paths are
        # idempotent. The runner already tolerates this.
        print("gaia-coder stop: no daemon running")
        return 0

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except ValueError:
        print(f"gaia-coder stop: corrupt pid file at {pid_file}", file=sys.stderr)
        pid_file.unlink(missing_ok=True)
        return 3

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        print(f"gaia-coder stop: daemon (pid={pid}) already gone")
        return 0

    # Wait for the daemon to clean up its pid file. Short deadline —
    # the daemon's shutdown path is just ``unlink + print``.
    deadline = time.monotonic() + max(1.0, args.wait_s)
    while time.monotonic() < deadline:
        if not pid_file.exists():
            print(f"gaia-coder stop: daemon (pid={pid}) stopped")
            return 0
        time.sleep(0.05)

    print(
        f"gaia-coder stop: daemon (pid={pid}) did not exit within "
        f"{args.wait_s:.1f}s — you may need to SIGKILL it manually",
        file=sys.stderr,
    )
    return 4


# ---------------------------------------------------------------------------
# Subcommand inventory (§3.1) + parser builder.
# ---------------------------------------------------------------------------

# Subcommands that still use the generic Phase 1 stub. The Phase 2
# eval-focused subcommands (daemon, ask, wait, stop) are attached below
# with real handlers and custom arguments.
_STUB_SUBCOMMANDS: tuple[tuple[str, str], ...] = (
    ("status", "Print a snapshot of the agent's current state."),
    ("note", "Append a line to the learnings log."),
    ("critical", "Post a critical-severity EM inbox item (soft interrupt)."),
    ("inbox", "Read or drain the EM inbox."),
    ("feedback", "Submit a feedback record to the self-correction loop."),
    ("promote", "Promote the agent to a higher capability tier."),
    ("demote", "Demote the agent to a lower capability tier."),
    ("trust", "Show the current trust contract snapshot."),
    ("audit", "Tail the append-only audit log."),
    ("spend", "Report cloud-spend against budget ceilings."),
    ("egress", "Show egress policy status and recent denials."),
    ("introspect", "Introspect the running agent (state machine, tools, etc.)."),
    ("skill", "Skills catalog management."),
    ("doctor", "Run self-diagnostics."),
    ("rag", "Manage the amd/gaia RAG index."),
)


def _build_parser(
    subcommands: Iterable[tuple[str, str]] = _STUB_SUBCOMMANDS,
) -> argparse.ArgumentParser:
    """Build the ``gaia-coder`` top-level argparse parser."""
    parser = argparse.ArgumentParser(
        prog="gaia-coder",
        description=(
            "gaia-coder: engineering-facing coding agent for amd/gaia. "
            "Phase 2 scaffold — daemon/ask/wait/stop are real (stub "
            "artifacts); other subcommands are Phase 1 stubs. See "
            "docs/plans/coder-agent.mdx for the full spec."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        dest="subcommand",
        metavar="<subcommand>",
        help="Run `gaia-coder <subcommand> -h` for subcommand help.",
    )

    # --- daemon (Phase 2, real stub daemon) ---
    daemon_parser = subparsers.add_parser(
        "daemon",
        help="Run the long-lived gaia-coder daemon.",
        description=(
            "Run the long-lived gaia-coder daemon. In Phase 2 this is a "
            "stub that polls an inbox and writes artifact-shaped files "
            "for each task; the real agent loop replaces it in Phases 3-8."
        ),
    )
    daemon_parser.add_argument(
        "--sandbox",
        required=True,
        help="Path to the sandbox working directory (usually a git worktree).",
    )
    daemon_parser.add_argument(
        "--capability-tier",
        type=int,
        default=0,
        help="Capability tier (§4.2). Eval harness pins this to 0 or 1.",
    )
    daemon_parser.add_argument(
        "--no-network-writes",
        action="store_true",
        help="Forbid git push / gh pr create / any network-write tool. "
        "Eval harness always sets this; real runs only set it in CI.",
    )
    daemon_parser.set_defaults(handler=_handle_daemon)

    # --- ask (Phase 2, reads body from stdin with body='-') ---
    ask_parser = subparsers.add_parser(
        "ask",
        help="Post a question or task to the daemon's inbox.",
        description=(
            "Post a task body to the daemon's inbox. Use '-' as the body "
            "to read from stdin (e.g. `gaia-coder ask - < T04.md`). "
            "Prints the assigned task_id to stdout on success."
        ),
    )
    ask_parser.add_argument(
        "body",
        help="Task body. Use '-' to read from stdin.",
    )
    ask_parser.add_argument(
        "--sandbox",
        default=None,
        help="Path to the sandbox (defaults to CWD). Must match the "
        "daemon's --sandbox for the task to be picked up.",
    )
    ask_parser.set_defaults(handler=_handle_ask)

    # --- wait (Phase 2) ---
    wait_parser = subparsers.add_parser(
        "wait",
        help="Block until a task_id's artifacts are written.",
        description=(
            "Block until $SANDBOX/.eval-artifacts/<task_id>/.done exists "
            "(i.e. the daemon has finished the task). Exits 0 on "
            "completion, 124 on timeout."
        ),
    )
    wait_parser.add_argument(
        "--task-id",
        required=True,
        help="Task ID returned by `gaia-coder ask`.",
    )
    wait_parser.add_argument(
        "--timeout-min",
        type=float,
        default=20.0,
        help="Timeout in minutes (default: 20). Fractional values "
        "allowed — useful for tests (e.g. --timeout-min 0.05 = 3s).",
    )
    wait_parser.add_argument(
        "--sandbox",
        default=None,
        help="Path to the sandbox (defaults to CWD).",
    )
    wait_parser.set_defaults(handler=_handle_wait)

    # --- stop (Phase 2) ---
    stop_parser = subparsers.add_parser(
        "stop",
        help="Signal the running daemon to exit cleanly.",
        description=(
            "Send SIGTERM to the daemon listed in "
            "$SANDBOX/.eval-artifacts/.daemon.pid and wait briefly for "
            "it to exit. Idempotent: returns 0 if no daemon is running."
        ),
    )
    stop_parser.add_argument(
        "--sandbox",
        default=None,
        help="Path to the sandbox (defaults to CWD).",
    )
    stop_parser.add_argument(
        "--wait-s",
        type=float,
        default=5.0,
        help="Seconds to wait for the daemon to exit (default: 5.0).",
    )
    stop_parser.set_defaults(handler=_handle_stop)

    # --- Phase 1 stubs (unchanged) ---
    for name, help_text in subcommands:
        sub = subparsers.add_parser(
            name,
            help=help_text,
            description=help_text
            + " (Phase 1 stub — prints 'not yet implemented' and exits 0.)",
        )
        sub.set_defaults(handler=_not_yet_implemented(name))

    return parser


def main(argv: list[str] | None = None) -> int:
    """``gaia-coder`` entry point.

    Args:
        argv: Optional explicit argument vector (for tests). When ``None``,
            ``sys.argv[1:]`` is used.

    Returns:
        Process exit code.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "subcommand", None):
        parser.print_help()
        return 0

    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
