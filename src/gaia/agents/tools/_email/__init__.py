# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Mailbox backends for :mod:`gaia.agents.tools.email_tools`.

Gmail and Microsoft Graph, each emitting the same provider-neutral summary
shape so the tools above never learn which mailbox answered.

Read-only for now. Write verbs (organize, draft, send) land with the action
ledger in a later phase — see ``docs/plans/email-triage-skill.mdx``.
"""

from gaia.agents.tools._email.errors import MailboxAuthError, MailboxError
from gaia.agents.tools._email.gmail import GMAIL_API_BASE, GmailReadBackend
from gaia.agents.tools._email.graph import (
    GRAPH_API_BASE,
    OutlookReadBackend,
    message_summary,
)

__all__ = [
    "GMAIL_API_BASE",
    "GRAPH_API_BASE",
    "GmailReadBackend",
    "MailboxAuthError",
    "MailboxError",
    "OutlookReadBackend",
    "message_summary",
]
