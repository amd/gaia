# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
OAuth scope constants for the Email Triage Agent (#962).

Single source of truth — REQUIRED_CONNECTORS, the per-tool credential calls,
the test fixtures, and the catalog ``available_scopes`` declaration all read
from these constants so they cannot drift.

Request vs. enforce (#2730 D1): ``ALL_SCOPES`` (mail + calendar) is what
every connect path REQUESTS at consent, so a user who says yes to everything
is never asked twice. ``REQUIRED_SCOPES`` (mail only) is what the daemon's
forward-out mint ENFORCES — calendar is optional, not required, so a user who
declines it (or connects with an older, mail-only grant) still gets a working
mailbox instead of losing mail too. Calendar tools fail loudly, naming the
exact missing scope, when the enforcement gap bites at use time.
"""

from __future__ import annotations

# Public namespace this agent uses for grant-ledger lookups. MUST agree with
# the registration in ``gaia.agents.registry`` (the registry uses
# ``installed:email`` for built-in agents). The same string is used as the key
# for the email-agent grant-migration message in ``gaia.connectors.formatting``.
AGENT_NAMESPACED_ID = "installed:email"


# ---------------------------------------------------------------------------
# Gmail scopes
# ---------------------------------------------------------------------------

SCOPE_GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
SCOPE_GMAIL_SEND = "https://www.googleapis.com/auth/gmail.send"

# Tuple of every Gmail scope the email agent may request. Calendar tools
# request CALENDAR_SCOPES instead so a user who declines calendar can still
# use read/organize/reply.
GMAIL_SCOPES: tuple[str, ...] = (
    SCOPE_GMAIL_MODIFY,
    SCOPE_GMAIL_SEND,
)

# Deliberately NEVER requested (#2533) and NEVER included in GMAIL_SCOPES /
# ALL_SCOPES / google.py's available_scopes: this is Gmail's full-mailbox
# scope, required for DELETE /messages/{id} (permanent, unrecoverable
# delete). Granting it would give every GAIA agent full-mailbox delete
# access for the sake of one rare operation gmail.modify already lets the
# agent approximate via Trash. Named here so LiveGmailBackend can reference
# it in the actionable error it raises instead of attempting a call that
# Google would 403 (ACCESS_TOKEN_SCOPE_INSUFFICIENT) after the user already
# confirmed something irreversible.
SCOPE_GMAIL_FULL_MAILBOX = "https://mail.google.com/"


# ---------------------------------------------------------------------------
# Calendar scopes
# ---------------------------------------------------------------------------

SCOPE_CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
SCOPE_CALENDAR_READ = "https://www.googleapis.com/auth/calendar.readonly"

CALENDAR_SCOPES: tuple[str, ...] = (
    SCOPE_CALENDAR_EVENTS,
    SCOPE_CALENDAR_READ,
)


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

# Surfaced via REQUIRED_CONNECTORS — the AgentUI consent dialog asks the user
# to grant ALL of these on first connect, so the agent can then request
# narrower per-call subsets without re-prompting.
ALL_SCOPES: tuple[str, ...] = GMAIL_SCOPES + CALENDAR_SCOPES

# What the daemon's forward-out mint enforces (#2730 D1) — mail only. Calendar
# is requested (ALL_SCOPES) but never gates the mint, so a calendar-less
# connection still forwards a working mailbox instead of losing mail too.
REQUIRED_SCOPES: tuple[str, ...] = GMAIL_SCOPES
