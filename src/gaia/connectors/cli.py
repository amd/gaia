# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
CLI for ``gaia connectors {connect|status|disconnect|grants ...}``.

Each subcommand is a thin wrapper that calls into ``gaia.connectors.api``.
The CLI drives the same primitives the AgentUI router and the SDK use:

- ``connect``      → ``start_authorization`` + ``complete_authorization``
- ``status``       → ``list_connections`` / ``get_connection``
- ``disconnect``   → ``revoke_connection``
- ``grants list``  → ``list_agent_grants``
- ``grants grant`` → ``grant_agent``
- ``grants revoke``→ ``revoke_agent_grant``

Output is plain text on stdout and actionable errors on stderr. Used by
``gaia connectors ...`` from the top-level CLI dispatcher.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Sequence

from gaia.connectors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectorsError,
    complete_authorization,
    get_connection,
    grant_agent,
    list_agent_grants,
    list_connections,
    revoke_agent_grant,
    revoke_connection,
    start_authorization,
)


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``gaia connectors`` and its subcommands."""
    p = subparsers.add_parser(
        "connectors",
        help="Manage external connectors (OAuth, MCP servers) and per-agent grants",
        description=(
            "Manage external connectors (OAuth providers, MCP servers, "
            "future API tokens) and per-agent grants. Connect once, then "
            "grant individual agents the scopes they need."
        ),
    )
    sub = p.add_subparsers(
        dest="connectors_action",
        metavar="<subcommand>",
        help="Subcommand",
    )

    # connect
    p_conn = sub.add_parser("connect", help="Authorize a provider (opens browser)")
    p_conn.add_argument("provider", help="Provider id (e.g. 'google')")
    p_conn.add_argument(
        "--scopes",
        nargs="+",
        help="OAuth scopes to request (provider-specific)",
    )

    # status
    p_status = sub.add_parser(
        "status", help="Show connected providers and their account email"
    )
    p_status.add_argument(
        "provider",
        nargs="?",
        help="Provider id to inspect; default: list all",
    )
    p_status.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON",
    )

    # disconnect
    p_disc = sub.add_parser("disconnect", help="Revoke a stored connection")
    p_disc.add_argument("provider")

    # grants
    p_grants = sub.add_parser("grants", help="Manage per-agent scope grants")
    g = p_grants.add_subparsers(dest="grants_action", metavar="<subcommand>")

    p_gl = g.add_parser("list", help="List agent grants for a provider")
    p_gl.add_argument(
        "provider",
        nargs="?",
        default="google",
        help="Provider id (default: google)",
    )

    p_gg = g.add_parser("grant", help="Grant an agent scopes for a provider")
    p_gg.add_argument("provider")
    p_gg.add_argument(
        "agent_id",
        help="Namespaced agent id, e.g. 'builtin:chat' or 'custom:abc:inbox'",
    )
    p_gg.add_argument(
        "--scopes",
        nargs="+",
        required=True,
        help="OAuth scopes to grant (provider-specific URLs)",
    )

    p_gr = g.add_parser("revoke", help="Revoke an agent's grant for a provider")
    p_gr.add_argument("provider")
    p_gr.add_argument("agent_id")


def handle(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``gaia connectors ...`` command. Returns exit code."""
    action = getattr(args, "connectors_action", None)
    if action is None:
        sys.stderr.write(
            "gaia connectors: missing subcommand. " "Try 'gaia connectors --help'.\n"
        )
        return 2

    try:
        if action == "connect":
            return _handle_connect(args)
        if action == "status":
            return _handle_status(args)
        if action == "disconnect":
            return _handle_disconnect(args)
        if action == "grants":
            return _handle_grants(args)
    except ConfigurationError as e:
        sys.stderr.write(f"Configuration error: {e}\n")
        return 3
    except AuthRequiredError as e:
        sys.stderr.write(f"Authorization required: {e}\n")
        return 4
    except ConnectorsError as e:
        sys.stderr.write(f"Connections error: {e}\n")
        return 5

    sys.stderr.write(f"gaia connectors: unknown subcommand {action!r}\n")
    return 2


def _handle_connect(args: argparse.Namespace) -> int:
    async def _run() -> str:
        info = await start_authorization(args.provider, scopes=args.scopes or [])
        sys.stdout.write(
            f"Open this URL to authorize {args.provider}:\n"
            f"  {info['authorization_url']}\n"
        )
        sys.stdout.flush()
        result = await complete_authorization(info["flow_id"])
        return result.get("account_email") or "<unknown>"

    email = asyncio.run(_run())
    sys.stdout.write(f"Connected as {email}\n")
    return 0


def _handle_status(args: argparse.Namespace) -> int:
    rows = list_connections()
    if args.provider:
        row = get_connection(args.provider)
        rows = [row] if row else []

    if args.as_json:
        sys.stdout.write(json.dumps(rows, indent=2) + "\n")
        return 0

    if not rows:
        sys.stdout.write("No connections.\n")
        return 0

    for row in rows:
        scopes = ", ".join(row.get("scopes", []) or []) or "<none>"
        sys.stdout.write(
            f"{row['provider']}: connected as "
            f"{row.get('account_email') or '<unknown>'} "
            f"(scopes: {scopes})\n"
        )
    return 0


def _handle_disconnect(args: argparse.Namespace) -> int:
    revoke_connection(args.provider)
    sys.stdout.write(f"Disconnected {args.provider}.\n")
    return 0


def _handle_grants(args: argparse.Namespace) -> int:
    sub = getattr(args, "grants_action", None)
    if sub == "list":
        listing = list_agent_grants(args.provider)
        if not listing:
            sys.stdout.write(f"No grants for {args.provider}.\n")
            return 0
        for agent_id, scopes in sorted(listing.items()):
            sys.stdout.write(f"{args.provider} {agent_id}: {', '.join(scopes)}\n")
        return 0
    if sub == "grant":
        grant_agent(args.provider, args.agent_id, args.scopes)
        sys.stdout.write(
            f"Granted {args.provider} → {args.agent_id}: " f"{', '.join(args.scopes)}\n"
        )
        return 0
    if sub == "revoke":
        revoke_agent_grant(args.provider, args.agent_id)
        sys.stdout.write(f"Revoked grant for {args.provider} → {args.agent_id}.\n")
        return 0

    sys.stderr.write(
        "gaia connectors grants: missing subcommand. "
        "Try 'gaia connectors grants --help'.\n"
    )
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Standalone entry point — useful for ``python -m gaia.connectors.cli``
    or hand-driven testing."""
    parser = argparse.ArgumentParser(prog="gaia-connections")
    sub = parser.add_subparsers(dest="action")
    add_subparser(sub)
    args = parser.parse_args(argv)
    return handle(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
