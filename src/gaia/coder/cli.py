# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``gaia-coder`` CLI entry point (§3.1).

Phase 5 wires the EM-facing verbs — ``trust``, ``promote``, ``demote``,
``ask``, ``note``, ``critical``, ``inbox``, ``spend`` — over the data-layer
primitives in :mod:`gaia.coder.trust`, :mod:`gaia.coder.inbox`, and
:mod:`gaia.coder.intent`. Every other subcommand stays a stub (Phase 6+)
and prints ``"<subcommand>: not yet implemented"``.

Argparse (not click) is used to match the existing ``gaia`` CLI in
``src/gaia/cli.py``.

**Config directory.** All state lives under ``$GAIA_CODER_HOME`` if set,
otherwise under ``~/.gaia/coder/``. Tests drive the env var to isolate
real user state from pytest runs.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path
from typing import Callable, Iterable, Optional

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


def _audit_db_path() -> Path:
    return resolve_config_dir() / "audit.log.db"


# ---------------------------------------------------------------------------
# Stub body shared by Phase-1 subcommands we have not yet implemented.
# ---------------------------------------------------------------------------


def _not_yet_implemented(subcommand: str) -> Callable[[argparse.Namespace], int]:
    """Return a handler that prints a stub message and exits ``0``."""

    def handler(_args: argparse.Namespace) -> int:
        print(f"gaia-coder {subcommand}: not yet implemented")
        return 0

    handler.__name__ = f"_handle_{subcommand.replace('-', '_')}"
    return handler


# ---------------------------------------------------------------------------
# `trust` — §4.2 tier summary + --history
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

    Matches the template literally: ``Tier:``, ``EM:``, ``At this tier you
    may:`` — tests assert on these labels. The "At this tier you may NOT"
    section follows §4.2 too.
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

    # Last promotion / demotion from the audit history, if passed in.
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

    # "At this tier you may:" — cumulative from Tier 0 up to current.
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
    """Handler for ``gaia-coder trust``.

    Three modes:

    1. ``--bootstrap`` — write an initial ``em.toml`` from
       ``--em-handle`` / ``--em-channel`` / optional ``--persona-name``.
    2. ``--history`` — dump the audit log's tier-change rows as JSON lines.
    3. Default — render the §4.2 summary, or the bootstrap prompt if no
       ``em.toml`` exists.
    """
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


# ---------------------------------------------------------------------------
# `promote` / `demote`
# ---------------------------------------------------------------------------


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
# `ask` / `note` / `critical`
# ---------------------------------------------------------------------------


def _enqueue_em_message(severity: str, body: str, *, channel: str = "cli") -> str:
    """Shared body for the three EM-input verbs.

    Auto-acks every message per §4.5 and returns the inbox id so the caller
    can chain further action (the ``ask`` handler uses it to run the intent
    classifier).
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
        # CLI dispatch = print. The §4.5 SLA (< 5s) is dominated by this
        # stdout write, which is microseconds.
        inbox_mod.auto_ack(
            conn,
            msg_id,
            eta_minutes=5,
            dispatch=lambda _ch, text: print(text),
        )
    finally:
        conn.close()
    return msg_id


def _handle_ask(args: argparse.Namespace) -> int:
    """``gaia-coder ask "…"`` — enqueue question + optional intent dispatch."""
    body = " ".join(args.message)
    if not body.strip():
        print("ask: message is required", file=sys.stderr)
        return 2
    msg_id = _enqueue_em_message("question", body)
    print(f"[queued as {msg_id}]")
    return 0


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


# ---------------------------------------------------------------------------
# `inbox`
# ---------------------------------------------------------------------------


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
# Subcommand registration
# ---------------------------------------------------------------------------

_STUB_SUBCOMMANDS: tuple[tuple[str, str], ...] = (
    ("daemon", "Run the long-lived gaia-coder daemon."),
    ("status", "Print a snapshot of the agent's current state."),
    ("feedback", "Submit a feedback record to the self-correction loop."),
    ("audit", "Tail the append-only audit log."),
    ("spend", "Report cloud-spend against budget ceilings."),
    ("egress", "Show egress policy status and recent denials."),
    ("introspect", "Introspect the running agent (state machine, tools, etc.)."),
    ("skill", "Skills catalog management."),
    ("doctor", "Run self-diagnostics."),
    ("rag", "Manage the amd/gaia RAG index."),
)


def _build_parser(
    stub_subcommands: Iterable[tuple[str, str]] = _STUB_SUBCOMMANDS,
) -> argparse.ArgumentParser:
    """Build the ``gaia-coder`` top-level argparse parser."""
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

    # --- trust ---
    trust = subparsers.add_parser(
        "trust",
        help="Show the current trust contract snapshot.",
        description=(
            "Print the §4.2 tier summary for the bound EM. "
            "Pass --history for the audit trail. "
            "Pass --bootstrap to record a new EM on first run (§4.1)."
        ),
    )
    trust.add_argument(
        "--history",
        action="store_true",
        help="Dump tier-change audit log oldest-first.",
    )
    trust.add_argument(
        "--bootstrap",
        action="store_true",
        help="Create em.toml from the flags below (§4.1).",
    )
    trust.add_argument("--em-handle", dest="em_handle", help="GitHub handle")
    trust.add_argument(
        "--em-channel", dest="em_channel", help="Preferred contact channel"
    )
    trust.add_argument(
        "--persona-name",
        dest="persona_name",
        default=None,
        help="Optional name the agent signs standups with.",
    )
    trust.set_defaults(handler=_handle_trust)

    # --- promote ---
    promote = subparsers.add_parser(
        "promote",
        help="Promote the agent to a higher capability tier.",
        description="Requires --to-tier, --reason, and --em-signature (§4.2).",
    )
    promote.add_argument("--to-tier", dest="to_tier", type=int, required=True)
    promote.add_argument("--reason", required=True)
    promote.add_argument(
        "--em-signature",
        dest="em_signature",
        required=True,
        help="EM's GitHub handle as signature; must match em.toml.em_handle.",
    )
    promote.set_defaults(handler=_handle_promote)

    # --- demote ---
    demote = subparsers.add_parser(
        "demote",
        help="Demote the agent to a lower capability tier.",
        description="Immediate; no signature required (§4.2).",
    )
    demote.add_argument("--reason", default="")
    demote.add_argument(
        "--to-tier",
        dest="to_tier",
        type=int,
        default=None,
        help="Explicit target (defaults to current-1).",
    )
    demote.set_defaults(handler=_handle_demote)

    # --- ask / note / critical ---
    for name, help_text, handler in (
        ("ask", "Post a question to the EM inbox.", _handle_ask),
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

    # --- inbox ---
    inbox = subparsers.add_parser(
        "inbox",
        help="Read or drain the EM inbox.",
        description="List pending and recently-answered inbox rows.",
    )
    inbox.add_argument("--limit", type=int, default=20)
    inbox.set_defaults(handler=_handle_inbox)

    # --- stubs ---
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
