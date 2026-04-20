# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``gaia-coder`` CLI entry point (§3.1).

This module is the *unified* CLI surface for gaia-coder. It wires real
handlers for every subcommand whose underlying module has landed, and
keeps a small set of deliberate stubs for the ones still owed to later
phases.

Subcommand inventory (wired handlers in **bold**):

* **trust** / **promote** / **demote** — §4.2 tier contract (Phase 5,
  data layer in :mod:`gaia.coder.trust`).
* **ask** / **note** / **critical** / **inbox** — §4.5 EM inbox verbs
  (Phase 5, data layer in :mod:`gaia.coder.inbox`). ``ask`` also
  doubles as the Phase 2 eval-harness "post a task body to the daemon"
  shim when ``--sandbox`` is supplied — see the handler for the
  dispatch rule.
* **daemon** / **wait** / **stop** — Phase 2 stub daemon used by the
  eval harness (:mod:`gaia.eval.runners.coder_cli`).
* **feedback** — Phase 6 real handler: enqueue a row to ``feedback.db``
  via :mod:`gaia.coder.stores.feedback`.
* **self-fix** — Phase 6 nested sub-surface (``process`` runs one
  :class:`gaia.coder.self_fix.FeedbackLoopDriver` iteration).
* **dev-mode** — Phase 7 nested sub-surface (``enable`` / ``disable``
  / ``status`` over :mod:`gaia.coder.dev_mode`).
* **debug** — Phase 8 scaffold (nested ``repro`` / ``bisect`` /
  ``hypothesise`` / ``probe`` / ``localise`` / ``propose`` /
  ``postmortem`` subcommands — stubs until Phase 11 production swap).
* **rag** — Phase 10 nested sub-surface (``status`` / ``refresh`` /
  ``rebuild`` over :mod:`gaia.coder.rag_freshness`). When no RAG
  backend is wired (the default today), a noop provider/runner is
  used so the CLI still reports a deterministic "no corpora bound"
  shape.
* **status** / **audit** / **spend** / **egress** / **introspect** /
  **skill** / **doctor** — stubs pending their owning phase.

Also exported for :mod:`gaia.eval.runners.coder_cli`:
:data:`ARTIFACT_FILENAMES` — the six artifact files the eval harness
collects per task (§10.2).

Argparse (not click) matches the existing ``gaia`` CLI in
``src/gaia/cli.py``.

**Config directory.** State lives under ``$GAIA_CODER_HOME`` if set,
otherwise under ``~/.gaia/coder/``. Tests drive the env var to isolate
real user state from pytest runs.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import textwrap
import time
import uuid
from pathlib import Path
from typing import Callable, Iterable, Optional

# ---------------------------------------------------------------------------
# Eval-artifact layout (§10.2).
# ---------------------------------------------------------------------------

#: The six artifact files the eval harness collects per task. Kept as a
#: module-level constant so runner + daemon + scorer all agree on the
#: list (and ``test_runner_collects_all_6_artifacts`` can import it).
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

# Poll cadences for the Phase 2 stub daemon + ``wait``. Small enough for
# predictable latency in eval runs without burning CPU.
_DAEMON_POLL_INTERVAL_S = 0.1
_WAIT_POLL_INTERVAL_S = 0.1


# ---------------------------------------------------------------------------
# Config-dir resolution
# ---------------------------------------------------------------------------


def resolve_config_dir() -> Path:
    """Return the gaia-coder config/state directory, creating it if missing.

    Honours ``GAIA_CODER_HOME`` so tests can redirect state to a tmp dir;
    otherwise defaults to ``~/.gaia/coder/`` per the spec.
    """
    override = os.environ.get("GAIA_CODER_HOME")
    if override:
        path = Path(override)
    else:
        path = Path.home() / ".gaia" / "coder"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _em_toml_path() -> Path:
    return resolve_config_dir() / "em.toml"


def _inbox_db_path() -> Path:
    return resolve_config_dir() / "em_inbox.db"


def _feedback_db_path() -> Path:
    return resolve_config_dir() / "feedback.db"


def _memory_db_path() -> Path:
    return resolve_config_dir() / "memory.db"


def _audit_db_path() -> Path:
    return resolve_config_dir() / "audit.log.db"


def _session_path() -> Path:
    return resolve_config_dir() / "session.json"


# ---------------------------------------------------------------------------
# Stub body for subcommands that have not landed yet.
# ---------------------------------------------------------------------------


def _not_yet_implemented(subcommand: str) -> Callable[[argparse.Namespace], int]:
    """Return a handler that prints a stub message and exits ``0``.

    Used for subcommands whose implementation is genuinely deferred to
    a later phase (e.g. ``status``, ``spend``, ``egress``). The handler
    is never used for a subcommand whose real module already exists —
    those are wired below.
    """

    def handler(_args: argparse.Namespace) -> int:
        print(f"gaia-coder {subcommand}: not yet implemented")
        return 0

    handler.__name__ = f"_handle_{subcommand.replace('-', '_').replace(' ', '_')}"
    return handler


# ---------------------------------------------------------------------------
# `trust` — §4.2 tier summary + --history + --bootstrap
# ---------------------------------------------------------------------------


def _bootstrap_prompt() -> str:
    """The §4.1 bootstrap question shown when ``em.toml`` is absent."""
    return textwrap.dedent("""\
        gaia-coder has no Engineering Manager bound yet.

        Who is my engineering manager? (GitHub handle and preferred contact channel.)

        Example:
            gaia-coder trust --bootstrap \\
                --em-handle kovtcharov-amd \\
                --em-channel github-issue-comment

        I will do no work until you answer. See §4.1 of
        docs/plans/coder-agent.mdx for the full contract.
        """).rstrip()


def _render_tier_summary(
    cfg,
    *,
    history: Optional[list] = None,
) -> str:
    """Render the §4.2 ``gaia-coder trust`` snapshot for *cfg*.

    Matches the template literally — tests assert on these labels.
    """
    from gaia.coder.trust import TIER_CAPABILITIES, CapabilityTier

    tier_int = cfg.current_tier
    tier_enum = CapabilityTier(tier_int)
    lines: list[str] = []
    lines.append(f"Tier:          {tier_int}  ({tier_enum.label})")
    lines.append(f"EM:            @{cfg.em_handle}")
    if cfg.persona_name:
        lines.append(f"Persona name:  {cfg.persona_name}")
    dev_mode_state = "ON" if cfg.dev_mode_self_edit else "OFF"
    dev_reason = (
        f" ({cfg.dev_mode_enabled_reason})"
        if cfg.dev_mode_self_edit and cfg.dev_mode_enabled_reason
        else ""
    )
    lines.append(f"Dev mode:      {dev_mode_state}{dev_reason}")
    auto_merge = cfg.auto_merge_classes or []
    lines.append(f"Auto-merge:    {auto_merge}")

    if history:
        promos = [h for h in history if h["event"] == "promote"]
        demos = [h for h in history if h["event"] == "demote"]
        if promos:
            last = promos[-1]
            lines.append(
                f"Last promotion: Tier {last['from_tier']} → "
                f"{last['to_tier']} on {last['occurred_at']} — "
                f'"{last["reason"]}"'
            )
        else:
            lines.append("Last promotion: none on record.")
        if demos:
            last = demos[-1]
            lines.append(
                f"Last demotion:  Tier {last['from_tier']} → "
                f"{last['to_tier']} on {last['occurred_at']} — "
                f'"{last["reason"]}"'
            )
        else:
            lines.append("Last demotion:  none on record.")
    lines.append("")

    lines.append("At this tier you may:")
    for t in range(0, tier_int + 1):
        lines.append(f"  \u2713 {TIER_CAPABILITIES[t]} (Tier {t})")

    may_not = [(t, desc) for t, desc in TIER_CAPABILITIES.items() if t > tier_int]
    if may_not:
        lines.append("")
        lines.append("At this tier you may NOT yet:")
        for t, desc in may_not:
            lines.append(f"  \u2717 {desc} (Tier {t})")

    lines.append("")
    lines.append("Run `gaia-coder trust --history` for the full audit trail.")
    return "\n".join(lines)


def _handle_trust(args: argparse.Namespace) -> int:
    """Handler for ``gaia-coder trust`` (Phase 5)."""
    from gaia.coder import trust as trust_mod
    from gaia.coder.stores import audit as audit_store

    cfg_path = _em_toml_path()

    if args.bootstrap:
        if not args.em_handle or not args.em_channel:
            print(
                "--bootstrap requires --em-handle and --em-channel "
                "(see §4.1 for the contract).",
                file=sys.stderr,
            )
            return 2
        cfg = trust_mod.EMConfig(
            em_handle=args.em_handle,
            em_channel=args.em_channel,
            persona_name=args.persona_name,
        )
        trust_mod.save_em_config(cfg_path, cfg)
        print(f"Bootstrapped EM config at {cfg_path}.")
        print(_render_tier_summary(cfg))
        return 0

    if not cfg_path.exists():
        print(_bootstrap_prompt())
        return 0

    cfg = trust_mod.load_em_config(cfg_path)

    if args.history:
        conn = audit_store.open_store(_audit_db_path())
        try:
            history = trust_mod.tier_history(conn)
        finally:
            conn.close()
        if not history:
            print("No tier changes on record.")
            return 0
        for event in history:
            print(
                f"{event['occurred_at']}  {event['event']:<7}  "
                f"Tier {event['from_tier']} → {event['to_tier']}  "
                f"by @{event['em_handle']} — {event['reason']}"
            )
        return 0

    conn = audit_store.open_store(_audit_db_path())
    try:
        history = trust_mod.tier_history(conn)
    finally:
        conn.close()
    print(_render_tier_summary(cfg, history=history))
    return 0


def _handle_promote(args: argparse.Namespace) -> int:
    """Promote with EM-signature validation (§4.2)."""
    from gaia.coder import trust as trust_mod
    from gaia.coder.stores import audit as audit_store

    cfg_path = _em_toml_path()
    if not cfg_path.exists():
        print(
            "No EM config — run `gaia-coder trust --bootstrap --em-handle ... "
            "--em-channel ...` first (§4.1).",
            file=sys.stderr,
        )
        return 2
    cfg = trust_mod.load_em_config(cfg_path)
    conn = audit_store.open_store(_audit_db_path())
    try:
        try:
            updated = trust_mod.promote(
                cfg,
                args.to_tier,
                args.reason,
                args.em_signature,
                audit_conn=conn,
            )
        except trust_mod.TrustError as e:
            print(f"Promotion rejected: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    trust_mod.save_em_config(cfg_path, updated)
    print(
        f"Promoted to tier {updated.current_tier} "
        f"({trust_mod.CapabilityTier(updated.current_tier).label})."
    )
    return 0


def _handle_demote(args: argparse.Namespace) -> int:
    """Demote immediately; no signature required (§4.2)."""
    from gaia.coder import trust as trust_mod
    from gaia.coder.stores import audit as audit_store

    cfg_path = _em_toml_path()
    if not cfg_path.exists():
        print("No EM config — nothing to demote.", file=sys.stderr)
        return 2
    cfg = trust_mod.load_em_config(cfg_path)
    conn = audit_store.open_store(_audit_db_path())
    try:
        try:
            updated = trust_mod.demote(
                cfg,
                args.reason,
                audit_conn=conn,
                to_tier=args.to_tier,
            )
        except trust_mod.TrustError as e:
            print(f"Demotion rejected: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()

    trust_mod.save_em_config(cfg_path, updated)
    print(
        f"Demoted to tier {updated.current_tier} "
        f"({trust_mod.CapabilityTier(updated.current_tier).label})."
    )
    return 0


# ---------------------------------------------------------------------------
# EM inbox verbs — `ask` (default path) / `note` / `critical` (§4.5)
# ---------------------------------------------------------------------------


def _enqueue_em_message(severity: str, body: str, *, channel: str = "cli") -> str:
    """Shared body for the three EM-input verbs.

    Auto-acks every message per §4.5 and returns the inbox id so the caller
    can chain further action.
    """
    from gaia.coder import inbox as inbox_mod
    from gaia.coder import trust as trust_mod
    from gaia.coder.stores import em_inbox

    cfg_path = _em_toml_path()
    handle = "unknown-em"
    if cfg_path.exists():
        cfg = trust_mod.load_em_config(cfg_path)
        handle = cfg.em_handle

    conn = em_inbox.open_store(_inbox_db_path())
    try:
        msg_id = inbox_mod.enqueue(
            conn,
            severity=severity,
            body=body,
            from_handle=handle,
            channel=channel,
        )
        inbox_mod.auto_ack(
            conn,
            msg_id,
            eta_minutes=5,
            dispatch=lambda _ch, text: print(text),
        )
    finally:
        conn.close()
    return msg_id


def _handle_note(args: argparse.Namespace) -> int:
    """``gaia-coder note "…"`` — info-severity inbox entry."""
    body = " ".join(args.message)
    if not body.strip():
        print("note: message is required", file=sys.stderr)
        return 2
    msg_id = _enqueue_em_message("info", body)
    print(f"[queued as {msg_id}]")
    return 0


def _handle_critical(args: argparse.Namespace) -> int:
    """``gaia-coder critical "…"`` — critical-severity inbox entry (soft interrupt)."""
    body = " ".join(args.message)
    if not body.strip():
        print("critical: message is required", file=sys.stderr)
        return 2
    msg_id = _enqueue_em_message("critical", body)
    print(f"[queued as {msg_id}]")
    return 0


def _handle_inbox(args: argparse.Namespace) -> int:
    """List pending + recent inbox rows (§4.5)."""
    from gaia.coder import inbox as inbox_mod
    from gaia.coder.stores import em_inbox

    if not _inbox_db_path().exists():
        print("Inbox is empty (no messages yet).")
        return 0

    conn = em_inbox.open_store(_inbox_db_path())
    try:
        pending = inbox_mod.poll_at_breakpoint(conn)
        recent_rows = inbox_mod.recent(conn, limit=args.limit)
    finally:
        conn.close()

    if pending:
        print(f"Pending ({len(pending)}):")
        for r in pending:
            print(f"  [{r.severity}] {r.received_at}  {r.body!s:.80}  (id={r.id})")
    else:
        print("Pending: none.")

    print()
    print(f"Recent ({len(recent_rows)}):")
    for r in recent_rows:
        print(
            f"  [{r.state}/{r.severity}] {r.received_at}  "
            f"{r.body!s:.60}  (id={r.id[:8]}…)"
        )
    return 0


# ---------------------------------------------------------------------------
# Phase 2 daemon helpers — used by `ask --sandbox`, `daemon`, `wait`, `stop`.
# ---------------------------------------------------------------------------


def _artifacts_root(sandbox: Path) -> Path:
    """Return ``$SANDBOX/.eval-artifacts/``, creating it if missing."""
    root = Path(sandbox) / _EVAL_ARTIFACTS_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def _daemon_inbox_dir(sandbox: Path) -> Path:
    inbox = _artifacts_root(sandbox) / _INBOX_DIR
    inbox.mkdir(parents=True, exist_ok=True)
    return inbox


def _pid_file(sandbox: Path) -> Path:
    return _artifacts_root(sandbox) / _DAEMON_PID_FILE


def _parse_task_id_from_body(body: str) -> Optional[str]:
    """Return the ``id:`` field from the task front-matter, or ``None``."""
    lines = body.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return None
        if stripped.startswith("id:"):
            raw = stripped[len("id:"):].strip()
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

    The Phase 2 stub daemon is clearly marked ``stub: true`` in every
    machine-readable artifact so a later real-daemon run cannot be
    silently confused with a stub run.
    """
    task_dir = _artifacts_root(sandbox) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    (task_dir / "diff.patch").write_text("", encoding="utf-8")

    (task_dir / "regression_test.py").write_text(
        "# Stub regression test written by gaia-coder daemon (Phase 2).\n"
        "def test_stub_regression() -> None:\n"
        "    assert True\n",
        encoding="utf-8",
    )

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

    (task_dir / "confidence.txt").write_text("50\n", encoding="utf-8")

    (task_dir / "standup.md").write_text(
        f"# Stub standup for {task_id}\n\n"
        "_Produced by gaia-coder daemon in Phase 2 stub mode. "
        "Real standups land in Phase 7._\n",
        encoding="utf-8",
    )

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

    (task_dir / _DONE_MARKER).write_text(
        json.dumps({"task_id": task_id, "completed_at": time.time()}),
        encoding="utf-8",
    )
    return task_dir


def _handle_daemon(args: argparse.Namespace) -> int:
    """Run the Phase 2 stub daemon — poll the inbox and write stub artifacts.

    Exits cleanly on SIGTERM (``gaia-coder stop``) or SIGINT (Ctrl-C).
    """
    sandbox = Path(args.sandbox).resolve()
    if not sandbox.is_dir():
        print(
            f"gaia-coder daemon: --sandbox must be an existing directory: {sandbox}",
            file=sys.stderr,
        )
        return 2

    options = {
        "capability_tier": args.capability_tier,
        "no_network_writes": bool(args.no_network_writes),
        "stub": True,
        "sandbox": str(sandbox),
    }

    pid_file = _pid_file(sandbox)
    if pid_file.exists():
        existing_pid = pid_file.read_text(encoding="utf-8").strip()
        try:
            os.kill(int(existing_pid), 0)
        except (OSError, ValueError):
            pid_file.unlink(missing_ok=True)
        else:
            print(
                f"gaia-coder daemon: another daemon is already running "
                f"(pid={existing_pid}); run `gaia-coder stop` first.",
                file=sys.stderr,
            )
            return 3

    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    inbox = _daemon_inbox_dir(sandbox)

    stop_flag = {"stop": False}

    def _handle_signal(signum: int, _frame: object) -> None:
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


def _handle_wait(args: argparse.Namespace) -> int:
    """Poll ``$SANDBOX/.eval-artifacts/<task_id>/.done`` until it exists.

    Exits 0 on completion, 124 on timeout (GNU ``timeout`` convention).
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


def _handle_stop(args: argparse.Namespace) -> int:
    """Signal the Phase 2 daemon to exit; idempotent."""
    sandbox = Path(args.sandbox).resolve() if args.sandbox else Path.cwd()
    pid_file = _pid_file(sandbox)
    if not pid_file.exists():
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
# `ask` — dual-mode: Phase-5 EM inbox OR Phase-2 daemon shim.
# ---------------------------------------------------------------------------


def _handle_ask(args: argparse.Namespace) -> int:
    """``gaia-coder ask`` dispatches on ``--sandbox``.

    * Without ``--sandbox`` (Phase 5): the positional ``message`` args are
      joined with spaces and enqueued to ``em_inbox.db`` as a question.
    * With ``--sandbox`` (Phase 2 eval harness): the positional is the
      task body; a single ``-`` means "read body from stdin". The body
      is written to ``$SANDBOX/.eval-artifacts/.inbox/<task_id>.md`` and
      the task_id is printed to stdout for the runner to capture.
    """
    if getattr(args, "sandbox", None):
        sandbox = Path(args.sandbox).resolve()
        if not sandbox.is_dir():
            print(
                f"gaia-coder ask: --sandbox must be an existing directory: {sandbox}",
                file=sys.stderr,
            )
            return 2
        if len(args.message) == 1 and args.message[0] == "-":
            body = sys.stdin.read()
        else:
            body = " ".join(args.message)
        if not body.strip():
            print("gaia-coder ask: empty task body", file=sys.stderr)
            return 2
        task_id = _parse_task_id_from_body(body) or f"task-{uuid.uuid4().hex[:12]}"
        inbox = _daemon_inbox_dir(sandbox)
        (inbox / f"{task_id}.md").write_text(body, encoding="utf-8")
        print(task_id)
        return 0

    body = " ".join(args.message)
    if not body.strip():
        print("ask: message is required", file=sys.stderr)
        return 2
    msg_id = _enqueue_em_message("question", body)
    print(f"[queued as {msg_id}]")
    return 0


# ---------------------------------------------------------------------------
# `feedback` — Phase 6 real handler (writes to feedback.db).
# ---------------------------------------------------------------------------

_SEVERITY_CHOICES: tuple[str, ...] = ("low", "med", "high", "critical")


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _handle_feedback(args: argparse.Namespace) -> int:
    """Enqueue a pending feedback row to ``feedback.db`` (§7.3)."""
    from gaia.coder.stores import feedback as feedback_store

    db_path = Path(args.db_path) if args.db_path else _feedback_db_path()

    row_id = args.id or f"fb-{uuid.uuid4().hex[:12]}"
    from_handle = args.from_handle or "unknown-em"

    row = feedback_store.FeedbackRow(
        id=row_id,
        received_at=_utc_now_iso(),
        from_handle=from_handle,
        channel="cli",
        severity=args.severity,
        body=args.body,
        context_url=args.on,
    )

    conn = feedback_store.open_store(db_path)
    try:
        feedback_store.insert_row(conn, row)
    finally:
        conn.close()

    result = {
        "id": row.id,
        "state": row.state,
        "severity": row.severity,
        "context_url": row.context_url,
        "from_handle": row.from_handle,
        "db_path": str(db_path),
    }
    print(json.dumps(result))
    return 0


# ---------------------------------------------------------------------------
# `self-fix` — Phase 6 nested sub-surface.
# ---------------------------------------------------------------------------


def _handle_self_fix_process(args: argparse.Namespace) -> int:
    """Run one :meth:`FeedbackLoopDriver.process_pending_feedback` iteration.

    Deliberately no-ops when the queue is empty — exit 0 so the
    command is safe to poll on a cron. When a row is pending, the
    driver runs the full §7.4 pipeline; the caller must have configured
    ``--repo-root`` + ``--feedback-db`` + ``--memory-db`` or the handler
    falls back to the canonical ``~/.gaia/coder/`` paths.
    """
    from gaia.coder.self_fix import FeedbackLoopDriver, LoopDriverConfig

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd()
    feedback_db = (
        Path(args.feedback_db) if args.feedback_db else _feedback_db_path()
    )
    memory_db = Path(args.memory_db) if args.memory_db else _memory_db_path()

    config = LoopDriverConfig(
        repo_root=repo_root,
        feedback_db_path=feedback_db,
        memory_db_path=memory_db,
        em_config={},
    )
    driver = FeedbackLoopDriver(config)
    result = driver.process_pending_feedback()
    summary = {
        "final_state": result.final_state,
        "feedback_id": result.feedback_id,
        "pr_number": result.pr.number if result.pr else None,
        "pr_url": result.pr.url if result.pr else None,
        "regression_test_path": result.regression_test_path,
    }
    print(json.dumps(summary))
    return 0


def _handle_self_fix_root(args: argparse.Namespace) -> int:
    """``gaia-coder self-fix`` with no nested action — print help and exit 0.

    The nested parser is stored on ``args._self_fix_parser`` so the
    root handler can reuse argparse's own formatting.
    """
    parser = args._self_fix_parser
    parser.print_help()
    return 0


# ---------------------------------------------------------------------------
# `dev-mode` — Phase 7 nested sub-surface.
# ---------------------------------------------------------------------------


def _handle_dev_mode_enable(args: argparse.Namespace) -> int:
    """Enable dev mode (session or permanent). Requires ``em.toml`` + reason."""
    from gaia.coder import dev_mode
    from gaia.coder import trust as trust_mod
    from gaia.coder.stores import audit as audit_store

    cfg_path = _em_toml_path()
    if not cfg_path.exists():
        print(
            "No EM config — run `gaia-coder trust --bootstrap ...` first (§4.1).",
            file=sys.stderr,
        )
        return 2
    em_cfg = trust_mod.load_em_config(cfg_path)
    reason = args.reason
    if not reason:
        print(
            "dev-mode enable: --reason is required (audit-trail invariant, §7.1).",
            file=sys.stderr,
        )
        return 2

    audit_conn = audit_store.open_store(_audit_db_path())
    try:
        try:
            if args.permanent:
                dev_mode.enable_permanent(
                    em_cfg,
                    reason,
                    em_cfg_path=cfg_path,
                    audit_conn=audit_conn,
                )
                print(f"dev-mode: ENABLED permanently (reason={reason!r})")
            else:
                dev_mode.enable_session(
                    em_cfg,
                    reason,
                    session_path=_session_path(),
                    audit_conn=audit_conn,
                )
                print(f"dev-mode: ENABLED for this session (reason={reason!r})")
        except dev_mode.DevModeError as e:
            print(f"dev-mode enable rejected: {e}", file=sys.stderr)
            return 1
    finally:
        audit_conn.close()
    return 0


def _handle_dev_mode_disable(args: argparse.Namespace) -> int:
    """Disable dev mode (session or permanent)."""
    from gaia.coder import dev_mode
    from gaia.coder import trust as trust_mod
    from gaia.coder.stores import audit as audit_store

    cfg_path = _em_toml_path()
    em_handle = ""
    em_cfg = None
    if cfg_path.exists():
        em_cfg = trust_mod.load_em_config(cfg_path)
        em_handle = em_cfg.em_handle

    audit_conn = audit_store.open_store(_audit_db_path())
    try:
        if args.permanent:
            if em_cfg is None:
                print(
                    "dev-mode disable --permanent requires em.toml (§4.1).",
                    file=sys.stderr,
                )
                return 2
            dev_mode.disable_permanent(
                em_cfg,
                em_cfg_path=cfg_path,
                audit_conn=audit_conn,
            )
            print("dev-mode: DISABLED permanently")
        else:
            dev_mode.disable_session(
                session_path=_session_path(),
                audit_conn=audit_conn,
                em_handle=em_handle,
            )
            print("dev-mode: DISABLED for this session")
    finally:
        audit_conn.close()
    return 0


def _handle_dev_mode_status(_args: argparse.Namespace) -> int:
    """Print the current dev-mode status (hard precondition + soft flags)."""
    from gaia.coder import dev_mode

    cfg_path = _em_toml_path() if _em_toml_path().exists() else None
    status = dev_mode.detect_dev_mode(em_cfg_path=cfg_path)
    session = dev_mode.session_state(session_path=_session_path())
    session_flag = bool(session.get("dev_mode_session"))
    enabled = dev_mode.is_enabled(
        em_cfg_path=cfg_path,
        session_path=_session_path(),
    )

    print(f"dev-mode enabled:           {'ON' if enabled else 'OFF'}")
    print(f"hard precondition (§7.1):   {'met' if status.editable_install else 'NOT met'}")
    print(f"em.toml allowlist flag:     {'on' if status.em_allowlist else 'off'}")
    print(f"session flag:               {'on' if session_flag else 'off'}")
    if status.origin_url:
        print(f"origin:                     {status.origin_url}")
    if status.repo_root:
        print(f"repo root:                  {status.repo_root}")
    if status.reason:
        print(f"reason (precondition):      {status.reason}")
    return 0


def _handle_dev_mode_root(args: argparse.Namespace) -> int:
    parser = args._dev_mode_parser
    parser.print_help()
    return 0


# ---------------------------------------------------------------------------
# `debug` — Phase 8 scaffold (state-machine sub-surface).
# ---------------------------------------------------------------------------


_DEBUG_SUBCOMMANDS: tuple[tuple[str, str], ...] = (
    ("repro", "Run reproduction step (§5.9 DebugState.REPRODUCE)."),
    ("bisect", "Run git bisect step (§5.9 DebugState.BISECT)."),
    (
        "hypothesise",
        "Advance to the hypothesise step (§5.9 DebugState.HYPOTHESISE).",
    ),
    ("probe", "Probe the system under test (§5.9 DebugState.PROBE)."),
    (
        "localise",
        "Localise the bug to a specific file:line (§5.9 DebugState.LOCALISE_BUG).",
    ),
    ("propose", "Propose a fix (§5.9 DebugState.PROPOSE_FIX)."),
    ("postmortem", "Write the postmortem (§5.9 DebugState.POSTMORTEM)."),
)


def _handle_debug_stub(name: str) -> Callable[[argparse.Namespace], int]:
    """Generate a stub handler for one Phase-8 debug subcommand.

    Phase 8 landed the :class:`DebugSubLoop` state machine; the CLI
    surface is scaffolded here so ``gaia-coder debug --help`` lists the
    full state set. The real CLI wiring into :class:`DebugSubLoop`
    lands in the production swap (§5.9).
    """

    def handler(_args: argparse.Namespace) -> int:
        print(
            f"gaia-coder debug {name}: not yet wired "
            "(DebugSubLoop is a Python-only surface — see §5.9)."
        )
        return 0

    handler.__name__ = f"_handle_debug_{name}"
    return handler


def _handle_debug_root(args: argparse.Namespace) -> int:
    parser = args._debug_parser
    parser.print_help()
    return 0


# ---------------------------------------------------------------------------
# `rag` — Phase 10 nested sub-surface (status / refresh / rebuild).
# ---------------------------------------------------------------------------


def _noop_status_provider():  # type: ignore[no-untyped-def]
    """Return an empty per-corpus status map.

    Used when no live RAG backend is bound to the CLI. The resulting
    :class:`RagStatusReport` reports every corpus as having zero
    documents and an unknown ``last_indexed_at`` — the watchdog then
    fires at ``severity='warn'`` because the contract expects at least
    one successful reindex within the configured window.
    """
    return {}


def _noop_reindex_runner(name: str, mode: str) -> dict:
    """Return a deterministic "no backend bound" payload for the CLI wrapper."""
    return {"corpus": name, "mode": mode, "documents_indexed": 0, "stub": True}


def _handle_rag_status(_args: argparse.Namespace) -> int:
    from gaia.coder.rag_freshness import FreshnessContract, rag_status

    contract = FreshnessContract.default()
    report = rag_status(contract, _noop_status_provider)
    print(json.dumps(report.to_dict(), indent=2))
    return 0


def _handle_rag_refresh(args: argparse.Namespace) -> int:
    from gaia.coder.rag_freshness import FreshnessContract, rag_refresh

    contract = FreshnessContract.default()
    try:
        result = rag_refresh(contract, _noop_reindex_runner, corpus=args.corpus)
    except KeyError as e:
        print(f"gaia-coder rag refresh: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


def _handle_rag_rebuild(args: argparse.Namespace) -> int:
    from gaia.coder.rag_freshness import FreshnessContract, rag_rebuild

    contract = FreshnessContract.default()
    try:
        result = rag_rebuild(contract, _noop_reindex_runner, corpus=args.corpus)
    except KeyError as e:
        print(f"gaia-coder rag rebuild: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


def _handle_rag_root(args: argparse.Namespace) -> int:
    parser = args._rag_parser
    parser.print_help()
    return 0


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


#: Subcommands that remain generic stubs pending their owning phase.
_PENDING_STUB_SUBCOMMANDS: tuple[tuple[str, str], ...] = (
    ("status", "Print a snapshot of the agent's current state."),
    ("audit", "Tail the append-only audit log."),
    ("spend", "Report cloud-spend against budget ceilings."),
    ("egress", "Show egress policy status and recent denials."),
    ("introspect", "Introspect the running agent (state machine, tools, etc.)."),
    ("skill", "Skills catalog management."),
    ("doctor", "Run self-diagnostics."),
)


def _build_parser(
    stub_subcommands: Iterable[tuple[str, str]] = _PENDING_STUB_SUBCOMMANDS,
) -> argparse.ArgumentParser:
    """Build the unified ``gaia-coder`` argparse parser."""
    parser = argparse.ArgumentParser(
        prog="gaia-coder",
        description=(
            "gaia-coder: engineering-facing coding agent for amd/gaia. "
            "See docs/plans/coder-agent.mdx for the full spec."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        dest="subcommand",
        metavar="<subcommand>",
        help="Run `gaia-coder <subcommand> -h` for subcommand help.",
    )

    # --- trust ---------------------------------------------------------
    trust = subparsers.add_parser(
        "trust",
        help="Show the current trust contract snapshot.",
        description=(
            "Print the §4.2 tier summary for the bound EM. "
            "Pass --history for the audit trail. "
            "Pass --bootstrap to record a new EM on first run (§4.1)."
        ),
    )
    trust.add_argument("--history", action="store_true")
    trust.add_argument("--bootstrap", action="store_true")
    trust.add_argument("--em-handle", dest="em_handle")
    trust.add_argument("--em-channel", dest="em_channel")
    trust.add_argument("--persona-name", dest="persona_name", default=None)
    trust.set_defaults(handler=_handle_trust)

    # --- promote -------------------------------------------------------
    promote = subparsers.add_parser(
        "promote",
        help="Promote the agent to a higher capability tier.",
        description="Requires --to-tier, --reason, and --em-signature (§4.2).",
    )
    promote.add_argument("--to-tier", dest="to_tier", type=int, required=True)
    promote.add_argument("--reason", required=True)
    promote.add_argument("--em-signature", dest="em_signature", required=True)
    promote.set_defaults(handler=_handle_promote)

    # --- demote --------------------------------------------------------
    demote = subparsers.add_parser(
        "demote",
        help="Demote the agent to a lower capability tier.",
        description="Immediate; no signature required (§4.2).",
    )
    demote.add_argument("--reason", default="")
    demote.add_argument("--to-tier", dest="to_tier", type=int, default=None)
    demote.set_defaults(handler=_handle_demote)

    # --- ask (Phase 5 EM inbox default + Phase 2 daemon shim) ---------
    ask = subparsers.add_parser(
        "ask",
        help="Ask the EM inbox a question, or post a task body to the eval daemon.",
        description=(
            "Without --sandbox: enqueue a question to the EM inbox "
            "(Phase 5, §4.5). With --sandbox: post a task body to the "
            "eval daemon's inbox and print the task_id (Phase 2, §10.2). "
            "Use '-' as the body to read from stdin in --sandbox mode."
        ),
    )
    ask.add_argument(
        "message",
        nargs="+",
        help="Question or task body (joined by spaces; use '-' for stdin).",
    )
    ask.add_argument("--sandbox", default=None)
    ask.set_defaults(handler=_handle_ask)

    # --- note / critical ----------------------------------------------
    for name, help_text, handler in (
        ("note", "Append an info-severity note to the EM inbox.", _handle_note),
        (
            "critical",
            "Post a critical-severity EM inbox item (soft interrupt).",
            _handle_critical,
        ),
    ):
        sp = subparsers.add_parser(name, help=help_text, description=help_text)
        sp.add_argument("message", nargs="+", help="Message body (space-joined).")
        sp.set_defaults(handler=handler)

    # --- inbox ---------------------------------------------------------
    inbox_parser = subparsers.add_parser(
        "inbox",
        help="Read or drain the EM inbox.",
        description="List pending and recently-answered inbox rows.",
    )
    inbox_parser.add_argument("--limit", type=int, default=20)
    inbox_parser.set_defaults(handler=_handle_inbox)

    # --- daemon (Phase 2 stub) ----------------------------------------
    daemon_parser = subparsers.add_parser(
        "daemon",
        help="Run the long-lived gaia-coder daemon (Phase 2 stub).",
        description=(
            "Run the long-lived gaia-coder daemon. Phase 2 ships a "
            "stub that polls an inbox and writes artifact-shaped files "
            "for each task; the real agent loop replaces it in Phases 3-8."
        ),
    )
    daemon_parser.add_argument("--sandbox", required=True)
    daemon_parser.add_argument(
        "--capability-tier", dest="capability_tier", type=int, default=0
    )
    daemon_parser.add_argument("--no-network-writes", action="store_true")
    daemon_parser.set_defaults(handler=_handle_daemon)

    # --- wait ----------------------------------------------------------
    wait_parser = subparsers.add_parser(
        "wait",
        help="Block until a task_id's artifacts are written.",
        description=(
            "Block until $SANDBOX/.eval-artifacts/<task_id>/.done exists. "
            "Exits 0 on completion, 124 on timeout."
        ),
    )
    wait_parser.add_argument("--task-id", dest="task_id", required=True)
    wait_parser.add_argument(
        "--timeout-min", dest="timeout_min", type=float, default=20.0
    )
    wait_parser.add_argument("--sandbox", default=None)
    wait_parser.set_defaults(handler=_handle_wait)

    # --- stop (Phase 2 daemon only) -----------------------------------
    stop_parser = subparsers.add_parser(
        "stop",
        help="Signal the running daemon to exit cleanly.",
        description=(
            "SIGTERM the daemon listed in "
            "$SANDBOX/.eval-artifacts/.daemon.pid. Idempotent."
        ),
    )
    stop_parser.add_argument("--sandbox", default=None)
    stop_parser.add_argument("--wait-s", dest="wait_s", type=float, default=5.0)
    stop_parser.set_defaults(handler=_handle_stop)

    # --- feedback (Phase 6) -------------------------------------------
    feedback_parser = subparsers.add_parser(
        "feedback",
        help="Submit a feedback record to the self-correction loop (§7.3).",
        description=(
            "Enqueue a pending feedback row to feedback.db. The row is "
            "picked up by `gaia-coder self-fix process` (or a daemon) "
            "which runs the full §7.4 self-correction pipeline."
        ),
    )
    feedback_parser.add_argument(
        "body", help="Feedback text (the EM's critique)."
    )
    feedback_parser.add_argument(
        "--severity",
        required=True,
        choices=_SEVERITY_CHOICES,
        help="Severity: one of " + " / ".join(_SEVERITY_CHOICES),
    )
    feedback_parser.add_argument(
        "--on", default=None, help="Context URL (PR/issue/commit)."
    )
    feedback_parser.add_argument(
        "--from-handle",
        dest="from_handle",
        default=None,
        help="Author handle (defaults to the bound EM).",
    )
    feedback_parser.add_argument(
        "--db-path",
        dest="db_path",
        default=None,
        help="Override feedback.db path (defaults to $GAIA_CODER_HOME/feedback.db).",
    )
    feedback_parser.add_argument(
        "--id",
        default=None,
        help="Explicit feedback id (defaults to a random UUID-ish value).",
    )
    feedback_parser.set_defaults(handler=_handle_feedback)

    # --- self-fix (Phase 6 nested) ------------------------------------
    self_fix_parser = subparsers.add_parser(
        "self-fix",
        help="Self-correction loop controls.",
        description=(
            "Nested sub-surface for the §7.4 self-correction loop. "
            "Use `gaia-coder self-fix process` to drive one pending "
            "feedback row through triage → plan → fix → PR."
        ),
    )
    self_fix_sub = self_fix_parser.add_subparsers(
        dest="self_fix_action", metavar="<action>"
    )
    self_fix_process = self_fix_sub.add_parser(
        "process",
        help="Process one pending feedback row (no-op if queue is empty).",
    )
    self_fix_process.add_argument("--repo-root", dest="repo_root", default=None)
    self_fix_process.add_argument(
        "--feedback-db", dest="feedback_db", default=None
    )
    self_fix_process.add_argument("--memory-db", dest="memory_db", default=None)
    self_fix_process.set_defaults(handler=_handle_self_fix_process)
    self_fix_parser.set_defaults(
        handler=_handle_self_fix_root,
        _self_fix_parser=self_fix_parser,
    )

    # --- dev-mode (Phase 7 nested) ------------------------------------
    dev_mode_parser = subparsers.add_parser(
        "dev-mode",
        help="Enable/disable/inspect agent self-edit permission (§7.1).",
        description=(
            "Enable or disable dev mode. Use --permanent to flip the "
            "em.toml flag; without it, the change is session-only. "
            "`status` prints the hard-precondition + soft-flag state."
        ),
    )
    dev_mode_sub = dev_mode_parser.add_subparsers(
        dest="dev_mode_action", metavar="<action>"
    )
    dev_enable = dev_mode_sub.add_parser(
        "enable", help="Enable dev mode (session or --permanent)."
    )
    dev_enable.add_argument("--reason", required=True)
    dev_enable.add_argument("--permanent", action="store_true")
    dev_enable.set_defaults(handler=_handle_dev_mode_enable)

    dev_disable = dev_mode_sub.add_parser(
        "disable", help="Disable dev mode (session or --permanent)."
    )
    dev_disable.add_argument("--permanent", action="store_true")
    dev_disable.set_defaults(handler=_handle_dev_mode_disable)

    dev_status = dev_mode_sub.add_parser(
        "status", help="Print the hard-precondition + soft-flag state."
    )
    dev_status.set_defaults(handler=_handle_dev_mode_status)

    dev_mode_parser.set_defaults(
        handler=_handle_dev_mode_root,
        _dev_mode_parser=dev_mode_parser,
    )

    # --- debug (Phase 8 scaffold) -------------------------------------
    debug_parser = subparsers.add_parser(
        "debug",
        help="Debug sub-loop scaffolding (§5.9).",
        description=(
            "Nested sub-surface exposing every DebugSubLoop state. Each "
            "action is a scaffold stub — production wiring lands with "
            "the Phase 11 production swap."
        ),
    )
    debug_sub = debug_parser.add_subparsers(
        dest="debug_action", metavar="<action>"
    )
    for name, help_text in _DEBUG_SUBCOMMANDS:
        sp = debug_sub.add_parser(name, help=help_text, description=help_text)
        sp.set_defaults(handler=_handle_debug_stub(name))
    debug_parser.set_defaults(
        handler=_handle_debug_root,
        _debug_parser=debug_parser,
    )

    # --- rag (Phase 10 nested) ----------------------------------------
    rag_parser = subparsers.add_parser(
        "rag",
        help="RAG freshness + reindex controls (§6.9).",
        description=(
            "Nested sub-surface over the §6.9 freshness contract. "
            "`status` prints per-corpus verdicts; `refresh` / `rebuild` "
            "trigger incremental / full reindex."
        ),
    )
    rag_sub = rag_parser.add_subparsers(dest="rag_action", metavar="<action>")
    rag_status_parser = rag_sub.add_parser(
        "status", help="Print per-corpus freshness + watchdog state."
    )
    rag_status_parser.set_defaults(handler=_handle_rag_status)

    rag_refresh_parser = rag_sub.add_parser(
        "refresh",
        help="Incremental reindex for one corpus or all of them.",
    )
    rag_refresh_parser.add_argument("--corpus", default=None)
    rag_refresh_parser.set_defaults(handler=_handle_rag_refresh)

    rag_rebuild_parser = rag_sub.add_parser(
        "rebuild",
        help="Full rebuild for one corpus or all of them.",
    )
    rag_rebuild_parser.add_argument("--corpus", default=None)
    rag_rebuild_parser.set_defaults(handler=_handle_rag_rebuild)

    rag_parser.set_defaults(handler=_handle_rag_root, _rag_parser=rag_parser)

    # --- generic pending stubs ----------------------------------------
    for name, help_text in stub_subcommands:
        sub = subparsers.add_parser(
            name,
            help=help_text,
            description=help_text + " (stub — prints 'not yet implemented'.)",
        )
        sub.set_defaults(handler=_not_yet_implemented(name))

    return parser


def main(argv: list[str] | None = None) -> int:
    """``gaia-coder`` entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "subcommand", None):
        parser.print_help()
        return 0

    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
