# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which mailbox scope the flagship declares, and which one it requests.

Three different lists, and the differences are the point.

``DECLARED_SCOPES`` is what a *new consent* asks for. It feeds
``REQUIRED_CONNECTORS``, which is what both ``gaia connectors connect
--grant-agent`` and the Agent UI consent screen render. One consent has to cover
reading and writing mail, so Google declares ``gmail.modify``, which allows
read, compose and send but never permanent delete.

``READ_CAPABLE_SCOPES`` is what actually *confers read*, narrowest first. The
grant ledger does exact subset matching with no scope implication, so a
connection made when the flagship only read mail, carrying just
``gmail.readonly``, must keep reading without a reconnect. Resolving the request
scope against the connection reuses what is already there.

``WRITE_SCOPES`` is what each write needs, all of it, per operation. Reads and
writes resolve separately: a read never asks for more than a read needs, and a
write on a read-only connection gets an error naming the reconnect.

These tables only ever *narrow* what is requested, which is why they live here
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
SCOPE_MAIL_SEND = "https://graph.microsoft.com/Mail.Send"

# What a write does to the mailbox. Graph splits these across two scopes, and
# every send here reads or creates a draft first, so a send needs both.
OPERATION_DRAFT = "draft"
OPERATION_SEND = "send"

DECLARED_SCOPES: Dict[str, Tuple[str, ...]] = {
    GOOGLE_CONNECTOR_ID: (SCOPE_GMAIL_MODIFY,),
    MICROSOFT_CONNECTOR_ID: (SCOPE_MAIL_READWRITE, SCOPE_MAIL_SEND),
}

READ_CAPABLE_SCOPES: Dict[str, Tuple[str, ...]] = {
    GOOGLE_CONNECTOR_ID: (SCOPE_GMAIL_READONLY, SCOPE_GMAIL_MODIFY),
    MICROSOFT_CONNECTOR_ID: (SCOPE_MAIL_READ, SCOPE_MAIL_READWRITE),
}

WRITE_SCOPES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    GOOGLE_CONNECTOR_ID: {
        OPERATION_DRAFT: (SCOPE_GMAIL_MODIFY,),
        OPERATION_SEND: (SCOPE_GMAIL_MODIFY,),
    },
    MICROSOFT_CONNECTOR_ID: {
        OPERATION_DRAFT: (SCOPE_MAIL_READWRITE,),
        OPERATION_SEND: (SCOPE_MAIL_READWRITE, SCOPE_MAIL_SEND),
    },
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


def missing_write_scopes(
    provider: str, operation: str, *, held_scopes: Iterable[str]
) -> Tuple[str, ...]:
    """The scopes *operation* needs that *held_scopes* lack, in table order."""
    held = set(held_scopes or ())
    return tuple(s for s in WRITE_SCOPES[provider][operation] if s not in held)


__all__ = [
    "DECLARED_SCOPES",
    "GOOGLE_CONNECTOR_ID",
    "MICROSOFT_CONNECTOR_ID",
    "OPERATION_DRAFT",
    "OPERATION_SEND",
    "READ_CAPABLE_SCOPES",
    "SCOPE_GMAIL_FULL_MAILBOX",
    "SCOPE_GMAIL_MODIFY",
    "SCOPE_GMAIL_READONLY",
    "SCOPE_MAIL_READ",
    "SCOPE_MAIL_READWRITE",
    "SCOPE_MAIL_SEND",
    "WRITE_SCOPES",
    "missing_write_scopes",
    "resolve_request_scope",
]
