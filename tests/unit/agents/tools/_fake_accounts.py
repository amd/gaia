# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fake connected accounts for the mail and calendar tool tests.

:func:`install` stands in for ``gaia.connectors.api.list_accounts`` and
``get_connection(provider, account=...)``, so the tools enumerate accounts the
way they do in production, without a keyring or an account list on disk.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from gaia.agents.tools._accounts import ToolAccount, make_tags, tag_key
from gaia.connectors.accounts import Account
from gaia.connectors.errors import UnknownAccountError


def _rows(connections: Dict[str, Any], provider: str) -> List[Dict[str, Any]]:
    value = connections.get(provider)
    if not value:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _slot(row: Dict[str, Any], position: int) -> str:
    if row.get("slot"):
        return row["slot"]
    return "default" if position == 0 else f"id:{row.get('account_email') or position}"


def accounts_of(connections: Dict[str, Any], provider: str) -> List[Account]:
    rows = _rows(connections, provider)
    default = next((i for i, r in enumerate(rows) if r.get("is_default")), 0)
    return [
        Account(
            connector_id=provider,
            account_id=row.get("account_id") or row.get("account_email") or "default",
            email=row.get("account_email") or "",
            status=row.get("status", "ok"),
            is_default=position == default,
            slot=_slot(row, position),
        )
        for position, row in enumerate(rows)
    ]


def install(
    monkeypatch,
    connections: Dict[str, Any],
    grants: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Serve ``connections`` (provider -> a connection row, or a list of rows,
    oldest first) as the stored accounts. Read live, so a test may change it.

    A row may carry ``status`` (``ok``, ``reauth_required``, ``error``),
    ``account_id``, ``slot`` and ``is_default``; the first row is the default
    otherwise. ``grants`` (provider -> {agent: scopes}) replaces the ledger.
    """
    import gaia.connectors.api as api
    import gaia.connectors.grants as grants_mod

    def get_connection(provider, account=None):
        rows = _rows(connections, provider)
        for row, acct in zip(rows, accounts_of(connections, provider)):
            named = account in (acct.account_id, acct.email, acct.slot)
            if (account is None and acct.is_default) or named:
                return row if acct.status == "ok" else None
        if account is None:
            return None
        raise UnknownAccountError(
            provider, account, [a.email for a in accounts_of(connections, provider)]
        )

    monkeypatch.setattr(api, "list_accounts", lambda p: accounts_of(connections, p))
    monkeypatch.setattr(api, "get_connection", get_connection)
    if grants is not None:
        monkeypatch.setattr(
            grants_mod, "list_agent_grants", lambda p: dict(grants.get(p) or {})
        )
    return connections


def tag(provider: str, account_id: str = "default", slot: str = "default") -> str:
    """The id tag the tools give the account with ``account_id`` (a row's
    ``account_id``, else its address); a legacy one is tagged by ``slot``."""
    return make_tags(provider, [tag_key(account_id, slot)])[0]


def tool_account(
    email: str = "me@example.com",
    provider: str = "microsoft",
    is_default: bool = True,
) -> ToolAccount:
    """An account as the tools see it, for a test that injects its backend."""
    return ToolAccount(
        provider=provider,
        email=email,
        ref=email,
        tag=tag(provider, email),
        is_default=is_default,
    )
