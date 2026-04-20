# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``gaia-coder`` CLI entry point (§3.1).

Phase 1 scaffolding: every subcommand is registered with a stub body that
prints ``"<subcommand>: not yet implemented"`` and exits ``0``. The real
implementations land in later phases.

Argparse is used (matching the existing ``gaia`` CLI in
``src/gaia/cli.py``) rather than ``click``, which is not currently in the
project's dependency set. Switching frameworks is out of scope for Phase
1 and not required for the stub subcommand surface.

Subcommand inventory (must match §3.1):

* ``daemon`` — long-lived process that owns the heartbeat and queues.
* ``status`` — one-shot snapshot of the agent's current state.
* ``ask`` — fire a single question at the EM inbox.
* ``note`` — append a line to the learnings log.
* ``critical`` — post a ``critical``-severity EM inbox item (soft
  interrupt; see §4.5).
* ``inbox`` — read / drain the EM inbox.
* ``feedback`` — submit a feedback record (§7.3).
* ``promote`` / ``demote`` — EM-signed tier changes (§4.2).
* ``trust`` — show the current trust contract snapshot (§4.2).
* ``audit`` — tail the append-only audit log.
* ``spend`` — cloud-spend report (§6.6).
* ``egress`` — egress-policy status and recent denials (§6.7).
* ``introspect`` — introspect the running agent (§7.7).
* ``skill`` — skills catalog management (§4.7).
* ``doctor`` — self-diagnostics.
* ``rag`` — RAG index management (§6.9).
"""

from __future__ import annotations

import argparse
import sys
from typing import Callable, Iterable

# ---------------------------------------------------------------------------
# Stub body shared by every Phase 1 subcommand.
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
# Subcommand inventory (§3.1). Tuple entries are (name, help-text).
# ---------------------------------------------------------------------------

_SUBCOMMANDS: tuple[tuple[str, str], ...] = (
    ("daemon", "Run the long-lived gaia-coder daemon."),
    ("status", "Print a snapshot of the agent's current state."),
    ("ask", "Post a question to the EM inbox."),
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
    subcommands: Iterable[tuple[str, str]] = _SUBCOMMANDS,
) -> argparse.ArgumentParser:
    """Build the ``gaia-coder`` top-level argparse parser.

    Kept as a function (rather than a module-level constant) so it is
    cheap to rebuild in tests and so each subcommand's handler can be
    bound without side effects at import time.
    """
    parser = argparse.ArgumentParser(
        prog="gaia-coder",
        description=(
            "gaia-coder: engineering-facing coding agent for amd/gaia. "
            "Phase 1 scaffold — every subcommand is a stub. See "
            "docs/plans/coder-agent.mdx for the full spec."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(
        dest="subcommand",
        metavar="<subcommand>",
        help="Run `gaia-coder <subcommand> -h` for subcommand help.",
    )

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
