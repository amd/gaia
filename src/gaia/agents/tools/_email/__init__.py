# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Mailbox backends for :mod:`gaia.agents.tools.email_tools`.

Read-only for now. Write verbs (organize, draft, send) land with the action
ledger in a later phase — see ``docs/plans/email-triage-skill.mdx``.
"""

from gaia.agents.tools._email.graph import (
    GRAPH_API_BASE,
    MailboxAuthError,
    MailboxError,
    OutlookReadBackend,
    message_summary,
)

__all__ = [
    "GRAPH_API_BASE",
    "MailboxAuthError",
    "MailboxError",
    "OutlookReadBackend",
    "message_summary",
]
