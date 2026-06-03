# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Microsoft Graph OAuth scope constants for the Email Triage Agent (#1275).

Single source of truth for the Outlook mailbox scopes — the agent's
``REQUIRED_CONNECTORS`` microsoft entry, the per-call credential request in
``outlook_backend._get_outlook_token``, and the tests all read from these so
they cannot drift. The string values MUST be a subset of the
``available_scopes`` declared in ``gaia.connectors.catalog.microsoft`` (#1105),
or the grant ledger will refuse the token request.

``Mail.ReadWrite`` (not just ``Mail.Read``) is required because the triage
agent's organize tools flag/move/categorize messages — mirroring why the Gmail
side requests ``gmail.modify`` rather than ``gmail.readonly``.
"""

from __future__ import annotations

# Mail.ReadWrite covers read + organize (flag, move, categorize, delete).
# Mail.Send is needed for draft-send / send-now (reply tools).
SCOPE_MAIL_READWRITE = "https://graph.microsoft.com/Mail.ReadWrite"
SCOPE_MAIL_SEND = "https://graph.microsoft.com/Mail.Send"

# Tuple of every Graph mail scope the email agent requests for Outlook.
# Surfaced via REQUIRED_CONNECTORS so the AgentUI consent dialog asks the user
# to grant these on first connect; the per-call credential request then asks
# for the same set (a subset would also be accepted by the grant check).
OUTLOOK_MAIL_SCOPES: tuple[str, ...] = (
    SCOPE_MAIL_READWRITE,
    SCOPE_MAIL_SEND,
)


__all__ = [
    "OUTLOOK_MAIL_SCOPES",
    "SCOPE_MAIL_READWRITE",
    "SCOPE_MAIL_SEND",
]
