# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
CLI for ``gaia connectors {list|connect|configure|test|disconnect|grants|activations ...}``.

Subcommands:
- ``list``        → catalog entries with configured/not status
- ``connect``     → OAuth PKCE browser flow (oauth_pkce type)
- ``configure``   → configure via the handler dispatcher (KEY=VALUE or --json)
- ``test``        → health check for a configured connector
- ``disconnect``  → remove credentials and reset connector state
- ``grants list|grant|revoke`` → per-agent scope grants ledger (every connector type)
- ``activations list|activate|deactivate`` → per-agent MCP tool-visibility ledger (mcp_server only)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Sequence

from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectorsError,
)


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Register ``gaia connectors`` and its subcommands."""
    p = subparsers.add_parser(
        "connectors",
        help="Manage external connectors (OAuth, MCP servers) and per-agent grants",
        description=(
            "Manage external connectors (OAuth providers, MCP servers, "
            "API tokens) and per-agent grants. Configure once, then grant "
            "individual agents the scopes they need."
        ),
    )
    sub = p.add_subparsers(
        dest="connectors_action",
        metavar="<subcommand>",
        help="Subcommand",
    )

    # list
    p_list = sub.add_parser(
        "list", help="List all connectors in the catalog with their status"
    )
    p_list.add_argument(
        "connector_id",
        nargs="?",
        help="Connector id to inspect; default: list all",
    )
    p_list.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON",
    )

    # status (alias for list — backward compatibility)
    p_status = sub.add_parser("status", help="Alias for 'list'")
    p_status.add_argument("connector_id", nargs="?")
    p_status.add_argument("--json", action="store_true", dest="as_json")

    # connect (OAuth PKCE)
    p_conn = sub.add_parser(
        "connect", help="Authorize an OAuth connector (opens browser)"
    )
    p_conn.add_argument("connector_id", help="Connector id (e.g. 'google')")
    p_conn.add_argument(
        "--scopes",
        nargs="+",
        help="OAuth scopes to request (connector-specific)",
    )
    p_conn.add_argument(
        "--grant-agent",
        dest="grant_agent",
        help=(
            "Also grant the requested --scopes to this namespaced agent id "
            "(e.g. 'installed:email') in the same flow — one command instead of a "
            "separate `grants grant`, and the scopes always match. Requires "
            "--scopes."
        ),
    )

    # configure (generic dispatcher + OAuth-client convenience flags)
    p_cfg = sub.add_parser(
        "configure",
        help="Configure a connector (MCP API keys, OAuth client creds, etc.)",
    )
    p_cfg.add_argument("connector_id", help="Connector id")
    p_cfg.add_argument(
        "--client-id",
        dest="client_id",
        help=(
            "OAuth client id for an oauth_pkce connector (e.g. the Google "
            "Desktop-app client). Persists to the keyring; requires --client-secret. "
            "Run 'gaia connectors connect <id>' afterward to complete login."
        ),
    )
    p_cfg.add_argument(
        "--client-secret",
        dest="client_secret",
        help="OAuth client secret (paired with --client-id; stored encrypted in the keyring)",
    )
    p_cfg.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        dest="config_pairs",
        help="Config key=value pair (repeatable, e.g. --set GITHUB_TOKEN=ghp_…)",
    )
    p_cfg.add_argument(
        "--json",
        metavar="JSON_OBJECT",
        dest="config_json",
        help="Config as a JSON object (alternative to --set)",
    )

    # test
    p_test = sub.add_parser("test", help="Run health check for a configured connector")
    p_test.add_argument("connector_id", help="Connector id")

    # disconnect
    p_disc = sub.add_parser(
        "disconnect", help="Remove credentials and reset a connector's state"
    )
    p_disc.add_argument("connector_id")

    # grants
    p_grants = sub.add_parser("grants", help="Manage per-agent scope grants")
    g = p_grants.add_subparsers(dest="grants_action", metavar="<subcommand>")

    p_gl = g.add_parser("list", help="List agent grants for a connector")
    p_gl.add_argument(
        "connector_id",
        nargs="?",
        default="google",
        help="Connector id (default: google)",
    )

    p_gg = g.add_parser("grant", help="Grant an agent scopes for a connector")
    p_gg.add_argument("connector_id")
    p_gg.add_argument(
        "agent_id",
        help="Namespaced agent id, e.g. 'builtin:chat' or 'custom:abc:inbox'",
    )
    p_gg.add_argument(
        "--scopes",
        nargs="+",
        required=True,
        help="Scopes to grant (connector-specific)",
    )

    p_gr = g.add_parser("revoke", help="Revoke an agent's grant for a connector")
    p_gr.add_argument("connector_id")
    p_gr.add_argument("agent_id")

    # activations (issue #1005)
    p_acts = sub.add_parser(
        "activations",
        help="Manage per-agent MCP tool-visibility activations (mcp_server only)",
        description=(
            "Activations gate which agents see an MCP server's tools in "
            "their prompt. A tool is visible to an agent only if the agent "
            "both has a grant (credential access) and an activation (tool "
            "visibility) for the connector. Activations apply to "
            "mcp_server connectors only — OAuth connectors have no MCP "
            "tool surface and are rejected with exit code 3 (use "
            "'gaia connectors grants' to control OAuth access per agent)."
        ),
    )
    a = p_acts.add_subparsers(dest="activations_action", metavar="<subcommand>")

    p_al = a.add_parser("list", help="List agent activations for a connector")
    p_al.add_argument(
        "connector_id",
        nargs="?",
        help="Connector id (lists every connector when omitted)",
    )
    p_al.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON",
    )

    p_aa = a.add_parser("activate", help="Activate a connector for an agent")
    p_aa.add_argument("connector_id")
    p_aa.add_argument(
        "agent_id",
        help="Namespaced agent id, e.g. 'builtin:chat' or 'custom:abc:inbox'",
    )
    p_aa.add_argument(
        "--scopes",
        nargs="+",
        help=(
            "Scopes to auto-grant if no grant exists yet. Required when "
            "the agent has no prior grant for this connector."
        ),
    )

    p_ad = a.add_parser("deactivate", help="Deactivate a connector for an agent")
    p_ad.add_argument("connector_id")
    p_ad.add_argument("agent_id")


def handle(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``gaia connectors ...`` command. Returns exit code."""
    action = getattr(args, "connectors_action", None)
    if action is None:
        sys.stderr.write(
            "gaia connectors: missing subcommand. Try 'gaia connectors --help'.\n"
        )
        return 2

    try:
        if action in ("list", "status"):
            return _handle_list(args)
        if action == "connect":
            return _handle_connect(args)
        if action == "configure":
            return _handle_configure(args)
        if action == "test":
            return _handle_test(args)
        if action == "disconnect":
            return _handle_disconnect(args)
        if action == "grants":
            return _handle_grants(args)
        if action == "activations":
            return _handle_activations(args)
    except ConfigurationError as e:
        sys.stderr.write(f"Configuration error: {e}\n")
        return 3
    except AuthRequiredError as e:
        sys.stderr.write(f"Authorization required: {e}\n")
        return 4
    except ConnectorsError as e:
        sys.stderr.write(f"Connectors error: {e}\n")
        return 5

    sys.stderr.write(f"gaia connectors: unknown subcommand {action!r}\n")
    return 2


def _handle_list(args: argparse.Namespace) -> int:
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.mcp_server import is_mcp_server_configured
    from gaia.connectors.registry import REGISTRY
    from gaia.connectors.store import peek_connection

    specs = REGISTRY.all()
    connector_id = getattr(args, "connector_id", None)
    if connector_id:
        try:
            specs = [REGISTRY.get(connector_id)]
        except KeyError:
            sys.stderr.write(f"gaia connectors: unknown connector {connector_id!r}\n")
            return 1

    # Derive configured/account/scopes live from the source-of-truth
    # store per type — keyring blob for OAuth, mcp_servers.json for MCP.
    # TODO: when a 3rd connector type lands, push this into a
    # Handler.summary(spec) -> {configured, account_id, scopes} method
    # so this list-call collapses to one polymorphic call. The same
    # if/elif lives in routers/connectors.py:_connector_summary; the
    # two should refactor together.
    rows = []
    for spec in specs:
        configured = False
        account_id = None
        scopes: list = []
        if spec.type == "oauth_pkce":
            blob = peek_connection(spec.oauth_provider_ref or spec.id)
            if blob is not None:
                configured = True
                account_id = blob.get("account_email")
                scopes = list(blob.get("scopes", []))
        elif spec.type == "mcp_server":
            configured = is_mcp_server_configured(spec.id)

        rows.append(
            {
                "id": spec.id,
                "display_name": spec.display_name,
                "type": spec.type,
                "category": spec.category,
                "tier": spec.tier,
                "configured": configured,
                "account_id": account_id,
                "scopes": scopes,
            }
        )

    if getattr(args, "as_json", False):
        sys.stdout.write(json.dumps(rows, indent=2) + "\n")
        return 0

    if not rows:
        sys.stdout.write("No connectors in catalog.\n")
        return 0

    for row in rows:
        status = "configured" if row["configured"] else "not configured"
        acct = f" ({row['account_id']})" if row.get("account_id") else ""
        sys.stdout.write(f"{row['id']:<30}  [{row['type']}]  {status}{acct}\n")
    return 0


def _handle_connect(args: argparse.Namespace) -> int:
    from gaia.connectors.api import complete_authorization, start_authorization

    grant_agent = getattr(args, "grant_agent", None)
    scopes = args.scopes or []
    if grant_agent and not scopes:
        sys.stderr.write(
            "gaia connectors connect: --grant-agent requires --scopes (there is "
            "nothing to grant without them).\n"
        )
        return 2
    # One-flow connect + grant: authorize the scopes AND grant them to the agent
    # so the two can never drift (the Agent UI does this via the same path).
    grant_agents = {grant_agent: list(scopes)} if grant_agent else None

    async def _run() -> str:
        info = await start_authorization(
            args.connector_id, scopes=scopes, grant_agents=grant_agents
        )
        sys.stdout.write(
            f"Open this URL to authorize {args.connector_id}:\n"
            f"  {info['authorization_url']}\n"
            "Sign-in completes via a callback to 127.0.0.1 on THIS machine. On a "
            "remote/headless box, open the URL in a browser here, or forward the "
            "callback port over SSH — a browser on another machine cannot reach "
            "this loopback and the flow will time out.\n"
        )
        sys.stdout.flush()
        result = await complete_authorization(info["flow_id"])
        return result.get("account_email") or "<unknown>"

    email = asyncio.run(_run())
    msg = f"Connected as {email}"
    if grant_agent:
        msg += f"; granted {args.connector_id} → {grant_agent}: {', '.join(scopes)}"
    sys.stdout.write(msg + "\n")
    return 0


def _handle_configure(args: argparse.Namespace) -> int:
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.handler import configure

    client_id = getattr(args, "client_id", None)
    client_secret = getattr(args, "client_secret", None)

    # OAuth-client convenience path (#1084): --client-id / --client-secret
    # persist the application's OAuth client creds to the same keyring slot the
    # provider reads from, completing OAuth *config* without the Agent UI. The
    # interactive browser login stays a separate `gaia connectors connect`.
    if client_id is not None or client_secret is not None:
        return _handle_configure_client_credentials(args, client_id, client_secret)

    config: dict = {}
    if getattr(args, "config_json", None):
        try:
            config = json.loads(args.config_json)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"gaia connectors configure: invalid JSON: {e}\n")
            return 2
    for pair in getattr(args, "config_pairs", None) or []:
        if "=" not in pair:
            sys.stderr.write(
                f"gaia connectors configure: --set requires KEY=VALUE, got {pair!r}\n"
            )
            return 2
        key, _, value = pair.partition("=")
        config[key.strip()] = value

    async def _run():
        return await configure(args.connector_id, config)

    try:
        result = asyncio.run(_run())
    except KeyError:
        sys.stderr.write(
            f"gaia connectors configure: unknown connector {args.connector_id!r}\n"
        )
        return 1

    sys.stdout.write(f"Configured {args.connector_id}.\n")
    if result.get("authorization_url"):
        sys.stdout.write(f"Complete OAuth flow at:\n  {result['authorization_url']}\n")
    return 0


def _handle_configure_client_credentials(
    args: argparse.Namespace,
    client_id: str | None,
    client_secret: str | None,
) -> int:
    """Persist an oauth_pkce connector's OAuth *client* credentials (#1084).

    Writes ``client_id`` / ``client_secret`` to the keyring slot the provider
    resolves from (``store.peek_provider_credentials``) and evicts the cached
    provider so the next construction re-reads them. Does NOT start the PKCE
    flow — the browser login stays a separate ``gaia connectors connect``.

    Both flags are required together: Google rejects token requests that omit
    the secret even for Desktop PKCE clients, so a half-configured client would
    fail loudly later instead of here. Mixing with ``--set`` / ``--json`` is a
    usage error to avoid an ambiguous double-write.
    """
    if not client_id:
        sys.stderr.write(
            "gaia connectors configure: --client-secret requires --client-id.\n"
        )
        return 2
    if not client_secret:
        sys.stderr.write(
            "gaia connectors configure: --client-id requires --client-secret "
            "(Google requires the secret even for Desktop-app PKCE clients).\n"
        )
        return 2
    if getattr(args, "config_pairs", None) or getattr(args, "config_json", None):
        sys.stderr.write(
            "gaia connectors configure: --client-id/--client-secret cannot be "
            "combined with --set/--json. Use one configuration style at a time.\n"
        )
        return 2

    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.providers import _registry as _provider_registry
    from gaia.connectors.registry import REGISTRY
    from gaia.connectors.store import save_provider_credentials

    try:
        spec = REGISTRY.get(args.connector_id)
    except KeyError:
        sys.stderr.write(
            f"gaia connectors configure: unknown connector {args.connector_id!r}\n"
        )
        return 1

    if spec.type != "oauth_pkce":
        sys.stderr.write(
            f"gaia connectors configure: --client-id/--client-secret apply only to "
            f"oauth_pkce connectors; {args.connector_id!r} is type {spec.type!r}.\n"
        )
        return 2

    provider_id = spec.oauth_provider_ref or spec.id
    save_provider_credentials(
        provider_id,
        client_id=client_id,
        client_secret=client_secret,
    )
    # Evict any cached provider instance so the next get_provider() re-reads the
    # freshly persisted creds instead of a stale id/secret.
    _provider_registry.pop(provider_id, None)

    sys.stdout.write(
        f"Configured {args.connector_id}. OAuth client credentials saved to the "
        "keyring.\n"
        f"Next: run 'gaia connectors connect {args.connector_id}' to sign in and "
        "authorize.\n"
    )
    return 0


def _handle_test(args: argparse.Namespace) -> int:
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.handler import health_check

    async def _run():
        return await health_check(args.connector_id)

    try:
        result = asyncio.run(_run())
    except KeyError:
        sys.stderr.write(
            f"gaia connectors test: unknown connector {args.connector_id!r}\n"
        )
        return 1

    ok = result.get("ok", False)
    detail = result.get("detail", "")
    status = "OK" if ok else "FAIL"
    sys.stdout.write(f"{args.connector_id}: {status}  {detail}\n")
    return 0 if ok else 1


def _handle_disconnect(args: argparse.Namespace) -> int:
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.handler import disconnect

    async def _run():
        await disconnect(args.connector_id)

    try:
        asyncio.run(_run())
    except KeyError:
        sys.stderr.write(
            f"gaia connectors disconnect: unknown connector {args.connector_id!r}\n"
        )
        return 1

    sys.stdout.write(f"Disconnected {args.connector_id}.\n")
    return 0


def _handle_grants(args: argparse.Namespace) -> int:
    from gaia.connectors.grants import (
        grant_agent,
        list_agent_grants,
        revoke_agent_grant,
    )

    sub = getattr(args, "grants_action", None)
    if sub == "list":
        listing = list_agent_grants(args.connector_id)
        if not listing:
            sys.stdout.write(f"No grants for {args.connector_id}.\n")
            return 0
        for agent_id, scopes in sorted(listing.items()):
            sys.stdout.write(f"{args.connector_id} {agent_id}: {', '.join(scopes)}\n")
        return 0
    if sub == "grant":
        grant_agent(args.connector_id, args.agent_id, args.scopes)
        sys.stdout.write(
            f"Granted {args.connector_id} → {args.agent_id}: "
            f"{', '.join(args.scopes)}\n"
        )
        return 0
    if sub == "revoke":
        revoke_agent_grant(args.connector_id, args.agent_id)
        sys.stdout.write(f"Revoked grant for {args.connector_id} → {args.agent_id}.\n")
        return 0

    sys.stderr.write(
        "gaia connectors grants: missing subcommand. "
        "Try 'gaia connectors grants --help'.\n"
    )
    return 2


def _handle_activations(args: argparse.Namespace) -> int:
    """Handle ``gaia connectors activations {list|activate|deactivate}``."""
    from gaia.connectors.activations import (
        list_agent_activations,
        load_activations,
    )
    from gaia.connectors.api import activate, deactivate

    sub = getattr(args, "activations_action", None)
    if sub == "list":
        connector_id = getattr(args, "connector_id", None)
        if connector_id:
            listing = {connector_id: list_agent_activations(connector_id)}
        else:
            listing = load_activations()

        if getattr(args, "as_json", False):
            sys.stdout.write(json.dumps(listing, indent=2, sort_keys=True) + "\n")
            return 0

        any_rows = False
        for cid, agents in sorted(listing.items()):
            for agent_id, active in sorted(agents.items()):
                state = "active" if active else "inactive"
                sys.stdout.write(f"{cid} {agent_id}: {state}\n")
                any_rows = True
        if not any_rows:
            sys.stdout.write("No activations.\n")
        return 0

    if sub == "activate":
        scopes = getattr(args, "scopes", None) or None
        auto_granted = activate(
            args.connector_id, args.agent_id, scopes_for_grant=scopes
        )
        if auto_granted:
            sys.stdout.write(
                f"Auto-granted scopes {', '.join(scopes or [])} "
                f"to {args.agent_id} for {args.connector_id}.\n"
            )
        sys.stdout.write(f"Activated {args.connector_id} for {args.agent_id}.\n")
        return 0

    if sub == "deactivate":
        # Use the api wrapper so the MCP-only type guard fires uniformly
        # across CLI/SDK/HTTP. The bare ``deactivate_agent`` ledger call
        # would bypass it.
        deactivate(args.connector_id, args.agent_id)
        sys.stdout.write(f"Deactivated {args.connector_id} for {args.agent_id}.\n")
        return 0

    sys.stderr.write(
        "gaia connectors activations: missing subcommand. "
        "Try 'gaia connectors activations --help'.\n"
    )
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    """Standalone entry point — useful for ``python -m gaia.connectors.cli``."""
    parser = argparse.ArgumentParser(prog="gaia-connectors")
    sub = parser.add_subparsers(dest="action")
    add_subparser(sub)
    args = parser.parse_args(argv)
    return handle(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
