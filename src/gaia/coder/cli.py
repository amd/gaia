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
* **doctor** — §15.6 bootstrap-gate self-check over the bound repo.
* **status** — at-a-glance snapshot (EM, tier, dev-mode, repo
  binding, pending counts, recent audit rows).
* **audit** — tail the append-only audit log (``--limit`` /
  ``--since``).
* **spend** — cost rollup from ``spend.db`` (``--day`` / ``--month``
  / ``--total``).
* **introspect** — inspect ``tools`` / ``state-machine`` / ``mixins``
  / ``em`` / ``repo``.
* **egress** — print loaded egress policy or a discoverable hint
  (engine itself lands in §6.7).
* **skill** — nested ``list`` / ``show`` / ``enable`` / ``disable``
  over the §4.7 skills catalog.

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
            raw = stripped[len("id:") :].strip()
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
    feedback_db = Path(args.feedback_db) if args.feedback_db else _feedback_db_path()
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
    print(
        f"hard precondition (§7.1):   {'met' if status.editable_install else 'NOT met'}"
    )
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
# Rich-or-plain rendering shim — used by every "snapshot" command below.
# Lazy-imported in one place so handlers can stay rendering-agnostic.
# ---------------------------------------------------------------------------


def _get_rich_console():
    """Return a ``rich.console.Console`` if the dep is available, else ``None``.

    ``rich`` is a soft dep: it ships in the REPL extras but a slim install
    can still run ``gaia-coder doctor``/``status``/``audit``/etc. with
    plain ``print``. Mirror :class:`gaia.coder.repl.UI`'s pattern so the
    user never sees a different shape based on which extras are present.
    """
    try:
        from rich.console import Console
    except ImportError:  # pragma: no cover — soft dep
        return None
    return Console()


def _print_table(
    title: str,
    columns: Iterable[str],
    rows: Iterable[Iterable[str]],
) -> None:
    """Render a titled table to stdout using ``rich`` when present, else plain.

    Plain layout is one column-pipe row plus a separator — matches the
    REPL's table fallback so eyeballing audit/status output looks the same
    in both surfaces.
    """
    cols = list(columns)
    materialised = [list(r) for r in rows]
    console = _get_rich_console()
    if console is not None:
        from rich.table import Table

        table = Table(title=title, show_lines=False)
        for col in cols:
            table.add_column(col)
        for row in materialised:
            table.add_row(*[str(c) for c in row])
        console.print(table)
        return
    print(title)
    print(" | ".join(cols))
    print("-" * max(40, sum(len(c) for c in cols) + 3 * len(cols)))
    for row in materialised:
        print(" | ".join(str(c) for c in row))


def _repo_binding_toml_path() -> Path:
    """Return ``$GAIA_CODER_HOME/repo_binding.toml``.

    Co-located with ``em.toml`` per §15.6 — no network state lives in
    repo binding; the file is plain TOML the EM hand-rolls during the
    bootstrap procedure.
    """
    return resolve_config_dir() / "repo_binding.toml"


def _spend_db_path() -> Path:
    return resolve_config_dir() / "spend.db"


def _egress_toml_path() -> Path:
    return resolve_config_dir() / "egress.toml"


def _skills_catalog_path() -> Path:
    """Resolve the active skills catalog path.

    A user-writable copy under ``$GAIA_CODER_HOME/skills/catalog.toml``
    takes precedence; without one, fall back to the package-shipped
    template at ``src/gaia/coder/skills/catalog.toml``. ``skill enable``
    / ``skill disable`` always write to the user path (seeding from the
    package template on first write) so the package data stays read-only.
    """
    override = resolve_config_dir() / "skills" / "catalog.toml"
    if override.exists():
        return override
    pkg = Path(__file__).resolve().parent / "skills" / "catalog.toml"
    return pkg


def _writable_skills_catalog_path() -> Path:
    """Return the user-writable catalog path under ``$GAIA_CODER_HOME``.

    ``skill enable`` / ``skill disable`` mutate this path so the package
    template is never modified in-place.
    """
    return resolve_config_dir() / "skills" / "catalog.toml"


# ---------------------------------------------------------------------------
# `doctor` — §15.6 bootstrap-gate self-check.
# ---------------------------------------------------------------------------


def _handle_doctor(_args: argparse.Namespace) -> int:
    """Run the §15.6 bootstrap-gate checks against the bound repo.

    Exit 0 if every check passes (``green``); exit 1 if any check fails
    so a CI-style ``gaia-coder doctor`` invocation surfaces a non-zero
    status. Also exits 1 when the repo binding itself is absent — the
    gate cannot be evaluated, which is itself a "not green" outcome.
    """
    from gaia.coder import repo_binding as rb_mod

    binding_path = _repo_binding_toml_path()
    if not binding_path.exists():
        print(
            f"doctor: no repo binding at {binding_path}. "
            "Create one per §15.6 of docs/plans/coder-agent.mdx, then re-run.",
            file=sys.stderr,
        )
        return 1

    try:
        binding = rb_mod.load_repo_binding(binding_path)
    except rb_mod.RepoBindingError as exc:
        print(f"doctor: {exc}", file=sys.stderr)
        return 1

    try:
        result = rb_mod.doctor(binding)
    except rb_mod.DoctorCheckError as exc:
        # Structural failure that prevented any checking — surface clearly.
        print(f"doctor: setup error — {exc}", file=sys.stderr)
        return 1

    rows = [(c.name, c.status.upper(), c.detail) for c in result.checks]
    _print_table(
        f"gaia-coder doctor — {binding.repo} @ {result.checked_at}",
        ("check", "status", "detail"),
        rows,
    )

    if result.green:
        print(f"doctor: green ({len(result.checks)}/{len(result.checks)} passed).")
        return 0

    print(file=sys.stderr)
    print("doctor: NOT GREEN — actionable next steps:", file=sys.stderr)
    for check in result.failed():
        print(f"  • {check.name}: {check.detail}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# `status` — at-a-glance snapshot of agent state.
# ---------------------------------------------------------------------------


def _count_pending(db_path: Path, table: str) -> int:
    """Count rows where ``state='pending'`` in ``db_path:table``.

    Cheap one-shot SQLite query — no full row materialisation. Used by
    ``status`` to render the pending-feedback / pending-EM-inbox numbers.
    """
    if not db_path.exists():
        return 0
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE state = 'pending'")
        row = cur.fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else 0


def _last_audit_rows(audit_db: Path, limit: int = 5) -> list:
    """Return the most-recent ``limit`` audit rows (newest first), or ``[]``."""
    if not audit_db.exists():
        return []
    from gaia.coder.stores import audit as audit_store

    conn = audit_store.open_store(audit_db)
    try:
        rows = audit_store.list_rows(conn)
    finally:
        conn.close()
    return list(reversed(rows[-limit:]))


def _handle_status(_args: argparse.Namespace) -> int:
    """Print a snapshot of every load-bearing piece of agent state."""
    from gaia.coder import dev_mode
    from gaia.coder import repo_binding as rb_mod
    from gaia.coder import trust as trust_mod

    em_path = _em_toml_path()
    if em_path.exists():
        em_cfg = trust_mod.load_em_config(em_path)
        em_summary = (
            f"@{em_cfg.em_handle} via {em_cfg.em_channel}"
            f"{' (' + em_cfg.persona_name + ')' if em_cfg.persona_name else ''}"
        )
        tier_int = em_cfg.current_tier
        tier_label = trust_mod.CapabilityTier(tier_int).label
        tier_summary = f"Tier {tier_int} ({tier_label})"
    else:
        em_summary = "(none — run `gaia-coder trust --bootstrap` first, §4.1)"
        tier_summary = "n/a (no EM bound)"

    dm_status = dev_mode.detect_dev_mode(
        em_cfg_path=em_path if em_path.exists() else None,
    )
    dm_session = dev_mode.session_state(session_path=_session_path())
    dm_session_on = bool(dm_session.get("dev_mode_session"))
    dm_enabled = dev_mode.is_enabled(
        em_cfg_path=em_path if em_path.exists() else None,
        session_path=_session_path(),
    )
    dm_summary = (
        f"{'ON' if dm_enabled else 'OFF'} "
        f"(precondition={'met' if dm_status.editable_install else 'unmet'}, "
        f"em-allowlist={'on' if dm_status.em_allowlist else 'off'}, "
        f"session={'on' if dm_session_on else 'off'})"
    )

    binding_path = _repo_binding_toml_path()
    if binding_path.exists():
        try:
            binding = rb_mod.load_repo_binding(binding_path)
            repo_summary = f"{binding.repo} (App ID {binding.github_app_id})"
        except rb_mod.RepoBindingError as exc:
            repo_summary = f"(invalid binding: {exc})"
    else:
        repo_summary = "(no repo binding manifest at ~/.gaia/coder/repo_binding.toml)"

    pending_feedback = _count_pending(_feedback_db_path(), "feedback")
    pending_inbox = _count_pending(_inbox_db_path(), "em_inbox")

    print("gaia-coder — status snapshot")
    print(f"  EM:               {em_summary}")
    print(f"  Tier:             {tier_summary}")
    print(f"  Dev mode:         {dm_summary}")
    print(f"  Repo binding:     {repo_summary}")
    print(f"  Pending feedback: {pending_feedback}")
    print(f"  Pending inbox:    {pending_inbox}")
    print(f"  Config dir:       {resolve_config_dir()}")

    audit_rows = _last_audit_rows(_audit_db_path(), limit=5)
    if not audit_rows:
        print("  Audit (last 5):   (no rows)")
    else:
        print("  Audit (last 5):")
        for r in audit_rows:
            err = f"  ERR={r.error}" if r.error else ""
            stage = r.stage or "-"
            print(f"    {r.occurred_at}  [{stage}] " f"{r.tool_name}{err}")
    return 0


# ---------------------------------------------------------------------------
# `audit` — tail the append-only audit log.
# ---------------------------------------------------------------------------


def _handle_audit(args: argparse.Namespace) -> int:
    """Print the most-recent ``--limit`` audit rows, optionally since a date."""
    from gaia.coder.stores import audit as audit_store

    db_path = _audit_db_path()
    if not db_path.exists():
        print(
            "audit: no audit log yet — start a session with `gaia-coder` "
            "to record tool calls."
        )
        return 0

    conn = audit_store.open_store(db_path)
    try:
        rows = audit_store.list_rows(conn)
    finally:
        conn.close()

    if args.since:
        # ``occurred_at`` is ISO-8601 UTC; lexical comparison is correct
        # for any ``YYYY-MM-DD`` (or denser) prefix the user supplies.
        rows = [r for r in rows if r.occurred_at >= args.since]

    if args.limit > 0:
        rows = rows[-args.limit :]

    if not rows:
        scope = f" since {args.since}" if args.since else ""
        print(f"audit: no rows{scope}.")
        return 0

    table_rows = []
    for r in rows:
        err_or_dur = (
            r.error
            if r.error
            else (f"{r.duration_ms}ms" if r.duration_ms is not None else "")
        )
        table_rows.append(
            (
                r.occurred_at,
                r.stage or "-",
                r.tool_name,
                err_or_dur,
                f"loop v{r.loop_version}",
            )
        )
    _print_table(
        f"gaia-coder audit ({len(rows)} rows)",
        ("occurred_at", "stage", "tool", "error/dur", "loop"),
        table_rows,
    )
    return 0


# ---------------------------------------------------------------------------
# `spend` — cost rollup from spend.db (§6.6).
# ---------------------------------------------------------------------------


def _spend_window_iso(scope: str) -> Optional[str]:
    """Return the inclusive lower bound (ISO-8601 UTC) for a spend scope.

    ``day`` → midnight today, ``month`` → first-of-month, ``total`` → ``None``
    (no lower bound; sum every row).
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    if scope == "day":
        floor = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif scope == "month":
        floor = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return None
    return floor.isoformat()


def _handle_spend(args: argparse.Namespace) -> int:
    """Print a cost rollup against the chosen scope (default: ``--day``)."""
    from gaia.coder.stores import spend as spend_store

    db_path = _spend_db_path()
    if not db_path.exists():
        print("no spend recorded — start a session with `gaia-coder` to log usage")
        return 0

    if args.month:
        scope = "month"
    elif args.total:
        scope = "total"
    else:
        scope = "day"

    floor = _spend_window_iso(scope)

    conn = spend_store.open_store(db_path)
    try:
        rows = spend_store.list_rows(conn)
    finally:
        conn.close()

    if floor is not None:
        rows = [r for r in rows if r.occurred_at >= floor]

    if not rows:
        print("no spend recorded — start a session with `gaia-coder` to log usage")
        return 0

    by_model: dict[str, dict] = {}
    total_usd = 0.0
    total_in = 0
    total_out = 0
    for r in rows:
        agg = by_model.setdefault(
            r.model,
            {"calls": 0, "input": 0, "output": 0, "usd": 0.0},
        )
        agg["calls"] += 1
        agg["input"] += r.input_tokens
        agg["output"] += r.output_tokens
        agg["usd"] += r.usd
        total_in += r.input_tokens
        total_out += r.output_tokens
        total_usd += r.usd

    rows_out = [
        (
            model,
            str(agg["calls"]),
            f"{agg['input']:,}",
            f"{agg['output']:,}",
            f"${agg['usd']:.4f}",
        )
        for model, agg in sorted(by_model.items())
    ]
    rows_out.append(
        (
            "TOTAL",
            str(len(rows)),
            f"{total_in:,}",
            f"{total_out:,}",
            f"${total_usd:.4f}",
        )
    )
    title = f"gaia-coder spend — scope={scope}"
    if floor:
        title += f" (since {floor})"
    _print_table(
        title,
        ("model", "calls", "input_tok", "output_tok", "usd"),
        rows_out,
    )
    return 0


# ---------------------------------------------------------------------------
# `introspect` — print runtime self-knowledge.
# (tools / state-machine / mixins / em / repo)
# ---------------------------------------------------------------------------


def _introspect_tools() -> int:
    """List every registered tool with its one-line description.

    The ``@tool`` decorator only fires when each mixin's ``register_*``
    method runs (see :meth:`gaia.coder.agent.Agent.__init__`). Construct
    transient mixin instances here so the registry is populated without
    needing a live :class:`CoderLLM` (which would require an API key).
    """
    from gaia.coder.tool_schema import build_anthropic_tools
    from gaia.coder.tools import CLIToolsMixin, SearchToolsMixin

    # ``SearchToolsMixin`` inherits ``FileToolsMixin`` and its
    # ``register_search_tools`` already calls ``register_file_tools``, so
    # this single call covers the file + search tool families.
    SearchToolsMixin().register_search_tools()
    CLIToolsMixin().register_cli_tools()
    try:
        from gaia.coder.tools.github import GitHubToolsMixin

        GitHubToolsMixin().register_github_tools()
    except ImportError:  # pragma: no cover — optional ``gh`` dep
        pass

    tools = build_anthropic_tools()
    if not tools:
        print("introspect tools: no tools registered.")
        return 0
    rows = [(t["name"], t.get("description", "").splitlines()[0]) for t in tools]
    _print_table(
        f"gaia-coder tools ({len(tools)} registered)",
        ("name", "summary"),
        rows,
    )
    return 0


def _introspect_state_machine() -> int:
    """Print the §15.3 ReAct loop as a Mermaid ``stateDiagram-v2`` block."""
    from gaia.coder.loop import introspect_state_machine

    snapshot = introspect_state_machine()
    print(snapshot["mermaid"])
    return 0


def _introspect_mixins() -> int:
    """Print :class:`gaia.coder.agent.Agent`'s MRO + each class's source file."""
    import inspect

    from gaia.coder.agent import Agent

    rows = []
    for cls in Agent.__mro__:
        try:
            src = inspect.getsourcefile(cls) or "(builtin)"
        except TypeError:
            src = "(builtin)"
        rows.append((cls.__module__ + "." + cls.__name__, src or ""))
    _print_table(
        f"gaia-coder Agent MRO ({len(rows)} classes)",
        ("class", "source_file"),
        rows,
    )
    return 0


def _introspect_em() -> int:
    """Print the bound EM config TOML verbatim, or a discoverable hint."""
    em_path = _em_toml_path()
    if not em_path.exists():
        print(
            f"introspect em: no EM config at {em_path}. "
            "Run `gaia-coder trust --bootstrap ...` first (§4.1)."
        )
        return 0
    print(em_path.read_text(encoding="utf-8"), end="")
    return 0


def _introspect_repo() -> int:
    """Print the repo binding manifest TOML, or "(no repo binding manifest)"."""
    binding_path = _repo_binding_toml_path()
    if not binding_path.exists():
        print("(no repo binding manifest — see §15.6 of docs/plans/coder-agent.mdx)")
        return 0
    print(binding_path.read_text(encoding="utf-8"), end="")
    return 0


_INTROSPECT_THINGS: dict[str, Callable[[], int]] = {
    "tools": _introspect_tools,
    "state-machine": _introspect_state_machine,
    "mixins": _introspect_mixins,
    "em": _introspect_em,
    "repo": _introspect_repo,
}


def _handle_introspect(args: argparse.Namespace) -> int:
    """Dispatch ``gaia-coder introspect <thing>`` to the matching helper."""
    handler = _INTROSPECT_THINGS.get(args.thing)
    if handler is None:
        # argparse `choices=` already enforces this; defensive fallback.
        choices = ", ".join(sorted(_INTROSPECT_THINGS))
        print(
            f"introspect: unknown thing {args.thing!r}. Choose: {choices}.",
            file=sys.stderr,
        )
        return 2
    return handler()


# ---------------------------------------------------------------------------
# `egress` — print egress policy status.
# ---------------------------------------------------------------------------


def _handle_egress(_args: argparse.Namespace) -> int:
    """Print the loaded egress policy TOML or a discoverable hint.

    The egress engine itself is not yet wired (see §6.7); this command
    is the inspection surface so the EM can sanity-check the policy file
    is in the expected location and shape ahead of the engine landing.
    """
    egress_path = _egress_toml_path()
    if not egress_path.exists():
        print("no egress policy configured — see docs/plans/coder-agent.mdx §6.7")
        return 0
    print(f"# Egress policy at {egress_path}")
    print(egress_path.read_text(encoding="utf-8"), end="")
    return 0


# ---------------------------------------------------------------------------
# `skill` — skills catalog management (§4.7).
# ---------------------------------------------------------------------------


def _load_skills_catalog(path: Path) -> dict:
    """Parse the skills catalog TOML at ``path``.

    Returns a dict with at minimum a ``skills`` list. Raises
    :class:`RuntimeError` on TOML decode errors so the operator sees
    the malformed file rather than a silently-empty list.
    """
    import tomllib

    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(
            f"skills catalog at {path} is not valid TOML: {exc}"
        ) from exc


def _serialise_skills_catalog(data: dict) -> str:
    """Render a skills-catalog dict back to TOML.

    Hand-rolled because ``tomli_w`` is not in the project's dependency
    set and the catalog schema is small and well-known. Mirrors
    :func:`gaia.coder.trust.save_em_config`'s approach.
    """
    lines: list[str] = []
    skills = (data.get("skill") or data.get("skills")) or []
    if not skills:
        lines.append("skill = []")
        return "\n".join(lines) + "\n"
    for entry in skills:
        lines.append("[[skill]]")
        for key in ("name", "path", "priority", "description"):
            if key in entry and entry[key] is not None:
                v = str(entry[key]).replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{key} = "{v}"')
        if "enabled" in entry:
            lines.append(f"enabled = {'true' if entry['enabled'] else 'false'}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _skills_no_catalog_message(path: Path) -> str:
    return (
        f"no skills catalog at {path} — see §4.7 of "
        "docs/plans/coder-agent.mdx for the catalog schema, or copy the "
        "package template at src/gaia/coder/skills/catalog.toml into place."
    )


def _handle_skill_list(_args: argparse.Namespace) -> int:
    """List every skill in the catalog with its enabled/disabled state."""
    path = _skills_catalog_path()
    if not path.exists():
        print(_skills_no_catalog_message(path))
        return 0
    data = _load_skills_catalog(path)
    skills = (data.get("skill") or data.get("skills")) or []
    if not skills:
        print(f"skills: catalog at {path} has no entries.")
        return 0
    rows = [
        (
            entry.get("name", "<unnamed>"),
            entry.get("priority", "-"),
            "yes" if entry.get("enabled", True) else "no",
            (entry.get("description") or "")[:60],
        )
        for entry in skills
    ]
    _print_table(
        f"gaia-coder skills ({len(skills)} entries) @ {path}",
        ("name", "priority", "enabled", "description"),
        rows,
    )
    return 0


def _handle_skill_show(args: argparse.Namespace) -> int:
    """Print one skill's full record."""
    path = _skills_catalog_path()
    if not path.exists():
        print(_skills_no_catalog_message(path))
        return 0
    data = _load_skills_catalog(path)
    for entry in (data.get("skill") or data.get("skills")) or []:
        if entry.get("name") == args.name:
            print(json.dumps(entry, indent=2, default=str))
            return 0
    print(f"skill show: no skill named {args.name!r}.", file=sys.stderr)
    return 1


def _set_skill_enabled(name: str, enabled: bool) -> int:
    """Common body for ``skill enable`` / ``skill disable``.

    Always writes to ``$GAIA_CODER_HOME/skills/catalog.toml`` (seeding
    from the package template on first write) so the package's data is
    never modified in-place.
    """
    user_path = _writable_skills_catalog_path()
    pkg_path = Path(__file__).resolve().parent / "skills" / "catalog.toml"

    source_path: Optional[Path] = None
    if user_path.exists():
        source_path = user_path
    elif pkg_path.exists():
        source_path = pkg_path
    if source_path is None:
        print(_skills_no_catalog_message(user_path))
        return 0

    data = _load_skills_catalog(source_path)
    skills = list((data.get("skill") or data.get("skills")) or [])
    found = False
    for entry in skills:
        if entry.get("name") == name:
            entry["enabled"] = enabled
            found = True
            break
    if not found:
        print(
            f"skill {'enable' if enabled else 'disable'}: " f"no skill named {name!r}.",
            file=sys.stderr,
        )
        return 1

    user_path.parent.mkdir(parents=True, exist_ok=True)
    user_path.write_text(
        _serialise_skills_catalog({"skills": skills}),
        encoding="utf-8",
    )
    state = "enabled" if enabled else "disabled"
    print(f"skill {name!r} {state} (catalog: {user_path}).")
    return 0


def _handle_skill_enable(args: argparse.Namespace) -> int:
    return _set_skill_enabled(args.name, True)


def _handle_skill_disable(args: argparse.Namespace) -> int:
    return _set_skill_enabled(args.name, False)


def _handle_skill_root(args: argparse.Namespace) -> int:
    parser = args._skill_parser
    parser.print_help()
    return 0


# ---------------------------------------------------------------------------
# Parser construction
# ---------------------------------------------------------------------------


#: Subcommands that remain generic stubs pending their owning phase.
#: Empty today — every previously-stubbed subcommand has a real handler
#: above. Kept as an extension point so future phases can land a stub
#: without touching the parser body.
_PENDING_STUB_SUBCOMMANDS: tuple[tuple[str, str], ...] = ()


def _build_parser(
    stub_subcommands: Iterable[tuple[str, str]] = _PENDING_STUB_SUBCOMMANDS,
) -> argparse.ArgumentParser:
    """Build the unified ``gaia-coder`` argparse parser."""
    parser = argparse.ArgumentParser(
        prog="gaia-coder",
        description=(
            "gaia-coder: engineering-facing coding agent for amd/gaia. "
            "Run with no subcommand for an interactive REPL "
            "(Claude-Code-style). "
            "See docs/plans/coder-agent.mdx for the full spec."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Top-level logging flags. Mutate the root logger before any handler
    # runs so per-handler logs are emitted at the requested level.
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging on the root logger.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress all logging below WARNING.",
    )
    parser.add_argument(
        "--log-file",
        dest="log_file",
        default=None,
        help="Append a copy of every log record to this file.",
    )
    subparsers = parser.add_subparsers(
        dest="subcommand",
        metavar="<subcommand>",
        help="Run `gaia-coder <subcommand> -h` for subcommand help.",
    )

    # --- repl (default; also explicit for flag control) --------------
    repl_parser = subparsers.add_parser(
        "repl",
        help="Launch the interactive coding REPL (default if no subcommand).",
        description=(
            "Drop into a Claude-Code-style interactive session. The agent "
            "is bound to the current repo (use --repo-root to override), "
            "auto-loads CLAUDE.md / AGENTS.md / GAIA.md as system context, "
            "and exposes the file / shell / search / GitHub tool registry "
            "to the LLM. Slash commands inside the REPL: /help, /tools, "
            "/cost, /save, /load, /trust, /feedback, /quit."
        ),
    )
    repl_parser.add_argument(
        "--yes",
        "-y",
        dest="auto_yes",
        action="store_true",
        help="Auto-approve every tool call (use only in trusted environments).",
    )
    repl_parser.add_argument(
        "--model",
        default=None,
        help="Override the LLM model (defaults to gaia.eval.config.DEFAULT_CLAUDE_MODEL).",
    )
    repl_parser.add_argument(
        "--repo-root",
        dest="repo_root",
        default=None,
        help="Bind the agent to this repo root (defaults to $PWD).",
    )
    repl_parser.add_argument(
        "--no-github",
        dest="no_github",
        action="store_true",
        help="Skip registering the gh CLI tool family.",
    )
    repl_parser.add_argument(
        "--resume",
        default=None,
        help="Resume a saved session by id (see /sessions inside the REPL).",
    )
    repl_parser.set_defaults(handler=_handle_repl)

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
        help="List pending and recently-answered EM inbox rows.",
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
    feedback_parser.add_argument("body", help="Feedback text (the EM's critique).")
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
    self_fix_process.add_argument("--feedback-db", dest="feedback_db", default=None)
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
    debug_sub = debug_parser.add_subparsers(dest="debug_action", metavar="<action>")
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

    # --- doctor (§15.6 bootstrap-gate self-check) ---------------------
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run the §15.6 bootstrap-gate checks and print a verdict.",
        description=(
            "Verify the GitHub App install, private-key keyring slot, "
            "webhook signature round-trip, and the existence of a `coder` "
            "branch. Exits 0 when every check passes, 1 otherwise."
        ),
    )
    doctor_parser.set_defaults(handler=_handle_doctor)

    # --- status (at-a-glance snapshot) --------------------------------
    status_parser = subparsers.add_parser(
        "status",
        help="Print a snapshot of the agent's current state.",
        description=(
            "One-shot snapshot: bound EM, capability tier, dev-mode "
            "status, repo binding, pending feedback / inbox counts, and "
            "the last 5 audit-log entries."
        ),
    )
    status_parser.set_defaults(handler=_handle_status)

    # --- audit (tail audit.log.db) ------------------------------------
    audit_parser = subparsers.add_parser(
        "audit",
        help="Tail the append-only audit log.",
        description="Print the last --limit audit rows, optionally since a date.",
    )
    audit_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of rows to print (default 50; ≤0 disables truncation).",
    )
    audit_parser.add_argument(
        "--since",
        default=None,
        help="ISO-8601 date prefix (e.g. 2026-04-01) — only show rows on or after.",
    )
    audit_parser.set_defaults(handler=_handle_audit)

    # --- spend (cost rollup against budget) ---------------------------
    spend_parser = subparsers.add_parser(
        "spend",
        help="Report cost rollup from spend.db (§6.6).",
        description=(
            "Aggregate spend by model. --day (default) covers UTC today, "
            "--month covers the calendar month, --total covers every "
            "logged call."
        ),
    )
    spend_scope = spend_parser.add_mutually_exclusive_group()
    spend_scope.add_argument(
        "--day", action="store_true", help="Spend for UTC today (default)."
    )
    spend_scope.add_argument(
        "--month", action="store_true", help="Spend for the current UTC month."
    )
    spend_scope.add_argument(
        "--total", action="store_true", help="Spend across every logged call."
    )
    spend_parser.set_defaults(handler=_handle_spend)

    # --- introspect <thing> -------------------------------------------
    introspect_parser = subparsers.add_parser(
        "introspect",
        help="Introspect the running agent (tools / state-machine / mixins / em / repo).",
        description=(
            "Print runtime self-knowledge for one of: "
            "tools, state-machine, mixins, em, repo."
        ),
    )
    introspect_parser.add_argument(
        "thing",
        choices=tuple(_INTROSPECT_THINGS),
        help="What to introspect.",
    )
    introspect_parser.set_defaults(handler=_handle_introspect)

    # --- egress (policy status) ---------------------------------------
    egress_parser = subparsers.add_parser(
        "egress",
        help="Show egress policy status (§6.7).",
        description=(
            "Print the loaded egress policy TOML if one exists at "
            "$GAIA_CODER_HOME/egress.toml. The egress engine itself is "
            "not yet wired — this command is the inspection surface."
        ),
    )
    egress_parser.set_defaults(handler=_handle_egress)

    # --- skill (catalog management, §4.7) -----------------------------
    skill_parser = subparsers.add_parser(
        "skill",
        help="Skills catalog management (§4.7).",
        description=(
            "Nested sub-surface for the §4.7 skills catalog. "
            "`list` enumerates entries, `show <name>` prints one record, "
            "`enable <name>` / `disable <name>` flip the per-skill flag."
        ),
    )
    skill_sub = skill_parser.add_subparsers(dest="skill_action", metavar="<action>")

    skill_list_parser = skill_sub.add_parser(
        "list", help="List every skill in the catalog."
    )
    skill_list_parser.set_defaults(handler=_handle_skill_list)

    skill_show_parser = skill_sub.add_parser(
        "show", help="Print one skill's full record."
    )
    skill_show_parser.add_argument("name", help="Skill name.")
    skill_show_parser.set_defaults(handler=_handle_skill_show)

    skill_enable_parser = skill_sub.add_parser(
        "enable", help="Enable one skill (sets enabled = true in the catalog)."
    )
    skill_enable_parser.add_argument("name", help="Skill name.")
    skill_enable_parser.set_defaults(handler=_handle_skill_enable)

    skill_disable_parser = skill_sub.add_parser(
        "disable", help="Disable one skill (sets enabled = false in the catalog)."
    )
    skill_disable_parser.add_argument("name", help="Skill name.")
    skill_disable_parser.set_defaults(handler=_handle_skill_disable)

    skill_parser.set_defaults(
        handler=_handle_skill_root,
        _skill_parser=skill_parser,
    )

    # --- generic pending stubs ----------------------------------------
    for name, help_text in stub_subcommands:
        sub = subparsers.add_parser(
            name,
            help=help_text,
            description=help_text + " (stub — prints 'not yet implemented'.)",
        )
        sub.set_defaults(handler=_not_yet_implemented(name))

    return parser


def _handle_repl(args: argparse.Namespace) -> int:
    """Launch the interactive REPL.

    Imported lazily so a user running ``gaia-coder trust`` or another
    one-shot command does not pay the ``rich`` / ``prompt_toolkit`` /
    ``anthropic`` import cost just to print a tier summary.
    """
    from gaia.coder.repl import run_repl

    repo_root = Path(args.repo_root).resolve() if args.repo_root else None
    return run_repl(
        auto_yes=getattr(args, "auto_yes", False),
        model=args.model,
        repo_root=repo_root,
        include_github=not getattr(args, "no_github", False),
        resume=getattr(args, "resume", None),
    )


def _configure_logging(args: argparse.Namespace) -> None:
    """Apply ``-v`` / ``-q`` / ``--log-file`` to the root logger.

    Default level is ``INFO`` so the REPL surfaces tool calls and the
    occasional warning. ``--verbose`` flips to ``DEBUG``; ``--quiet``
    flips to ``WARNING``. ``--log-file`` adds a :class:`logging.FileHandler`
    in addition to the default stderr handler.
    """
    import logging

    if getattr(args, "quiet", False):
        level = logging.WARNING
    elif getattr(args, "verbose", False):
        level = logging.DEBUG
    else:
        level = logging.INFO

    root = logging.getLogger()
    root.setLevel(level)
    # Replace any pre-existing root handler so repeated invocations
    # (tests, embedded use) don't accumulate stderr handlers.
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(fmt))
    root.addHandler(console_handler)
    log_file = getattr(args, "log_file", None)
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
            file_handler.setFormatter(logging.Formatter(fmt))
            root.addHandler(file_handler)
        except OSError as e:
            print(f"warning: --log-file {log_file!r} failed: {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """``gaia-coder`` entry point.

    With no subcommand: launches the interactive REPL. With a subcommand:
    dispatches to the matching handler (trust / feedback / self-fix / ...).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args)

    if not getattr(args, "subcommand", None):
        # No subcommand → REPL is the default surface (daily-driver mode).
        return _handle_repl(
            argparse.Namespace(
                auto_yes=False,
                model=None,
                repo_root=None,
                no_github=False,
                resume=None,
            )
        )

    handler: Callable[[argparse.Namespace], int] = args.handler
    return handler(args)


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
