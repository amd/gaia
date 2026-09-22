# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which mailbox scope the flagship declares, and which one it requests.

Two different lists, and the difference is the point.

``DECLARED_SCOPES`` is what a *new consent* asks for. It feeds
``REQUIRED_CONNECTORS``, which is what both ``gaia connectors connect
--grant-agent`` and the Agent UI consent screen render — so a first-time user
is asked to read their mail and nothing more.

``READ_CAPABLE_SCOPES`` is what actually *confers read*, narrowest first. The
grant ledger does exact subset matching with no scope implication, so a user
whose Google connection already carries ``gmail.modify`` would be told to
reconnect (a browser flow) if the agent insisted on ``gmail.readonly``.
Resolving the request scope against the connection instead reuses what is
already there.

This table only ever *narrows* what is requested, which is why it lives here
rather than as scope-implication logic inside ``gaia.connectors.grants`` —
that would change the security semantics of every connector for every agent.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple

GOOGLE_CONNECTOR_ID = "google"
MICROSOFT_CONNECTOR_ID = "microsoft"

SCOPE_GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"

# Total mailbox control, including permanent delete. Named here only so an
# error can say what is being refused; it is in no table below and in no
# connector's available_scopes, which is what actually keeps it unreachable.
SCOPE_GMAIL_FULL_MAILBOX = "https://mail.google.com/"

SCOPE_MAIL_READ = "https://graph.microsoft.com/Mail.Read"
SCOPE_MAIL_READWRITE = "https://graph.microsoft.com/Mail.ReadWrite"

DECLARED_SCOPES: Dict[str, Tuple[str, ...]] = {
    GOOGLE_CONNECTOR_ID: (SCOPE_GMAIL_READONLY,),
    # Unchanged: narrowing to Mail.Read would re-consent every existing
    # Outlook user for no benefit to a Gmail backend.
    MICROSOFT_CONNECTOR_ID: (SCOPE_MAIL_READWRITE,),
}

READ_CAPABLE_SCOPES: Dict[str, Tuple[str, ...]] = {
    GOOGLE_CONNECTOR_ID: (SCOPE_GMAIL_READONLY, SCOPE_GMAIL_MODIFY),
    MICROSOFT_CONNECTOR_ID: (SCOPE_MAIL_READ, SCOPE_MAIL_READWRITE),
}


def resolve_request_scope(
    provider: str,
    *,
    connection_scopes: Iterable[str],
    granted_scopes: Iterable[str],
) -> Optional[str]:
    """The narrowest read scope the connection *and* the ledger both carry.

    ``None`` means this provider cannot be read with what is on hand — the
    caller names the state and its remedy rather than trying the call.
    """
    have = set(connection_scopes or ()) & set(granted_scopes or ())
    for scope in READ_CAPABLE_SCOPES.get(provider, ()):
        if scope in have:
            return scope
    return None


__all__ = [
    "DECLARED_SCOPES",
    "GOOGLE_CONNECTOR_ID",
    "MICROSOFT_CONNECTOR_ID",
    "READ_CAPABLE_SCOPES",
    "SCOPE_GMAIL_FULL_MAILBOX",
    "SCOPE_GMAIL_MODIFY",
    "SCOPE_GMAIL_READONLY",
    "SCOPE_MAIL_READ",
    "SCOPE_MAIL_READWRITE",
    "resolve_request_scope",
]
