# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Microsoft Graph OAuth scope constants for the Email Triage Agent (#1275, #1276).

Single source of truth for the Outlook mailbox + calendar scopes — the agent's
``REQUIRED_CONNECTORS`` microsoft entry, the per-call credential requests in
``outlook_backend._get_outlook_token`` / ``outlook_calendar_backend.
_get_outlook_calendar_token``, and the tests all read from these so they
cannot drift. The string values MUST be a subset of the ``available_scopes``
declared in ``gaia.connectors.catalog.microsoft`` (#1105), or the grant ledger
will refuse the token request.

``Mail.ReadWrite`` (not just ``Mail.Read``) is required because the triage
agent's organize tools flag/move/categorize messages — mirroring why the Gmail
side requests ``gmail.modify`` rather than ``gmail.readonly``. ``Calendars.
ReadWrite`` (not just ``Calendars.Read``) is required because the calendar tools
RSVP (accept/decline) and create events — mirroring why the Google side
requests ``calendar.events`` rather than ``calendar.readonly``.
"""

from __future__ import annotations

# Mail.ReadWrite covers read + organize (flag, move, categorize, delete).
# Mail.Send is needed for draft-send / send-now (reply tools).
SCOPE_MAIL_READWRITE = "https://graph.microsoft.com/Mail.ReadWrite"
SCOPE_MAIL_SEND = "https://graph.microsoft.com/Mail.Send"

# Calendars.ReadWrite covers read (list/get events) + RSVP (accept/decline/
# tentativelyAccept) + create event.
SCOPE_CALENDARS_READWRITE = "https://graph.microsoft.com/Calendars.ReadWrite"

# Tuple of every Graph mail scope the email agent requests for Outlook.
# Surfaced via REQUIRED_CONNECTORS so the AgentUI consent dialog asks the user
# to grant these on first connect; the per-call credential request then asks
# for the same set (a subset would also be accepted by the grant check).
OUTLOOK_MAIL_SCOPES: tuple[str, ...] = (
    SCOPE_MAIL_READWRITE,
    SCOPE_MAIL_SEND,
)

# Tuple of every Graph calendar scope the email agent requests for Outlook.
# Kept separate from OUTLOOK_MAIL_SCOPES so a user who connects Outlook for mail
# only is not forced to grant calendar (and vice versa), mirroring how the
# Google side splits GMAIL_SCOPES from CALENDAR_SCOPES.
OUTLOOK_CALENDAR_SCOPES: tuple[str, ...] = (SCOPE_CALENDARS_READWRITE,)


__all__ = [
    "OUTLOOK_CALENDAR_SCOPES",
    "OUTLOOK_MAIL_SCOPES",
    "SCOPE_CALENDARS_READWRITE",
    "SCOPE_MAIL_READWRITE",
    "SCOPE_MAIL_SEND",
]
