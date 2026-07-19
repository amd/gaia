# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
OAuth scope constants for the Email Triage Agent (#962).

Single source of truth — REQUIRED_CONNECTORS, the per-tool credential calls,
the test fixtures, and the catalog ``available_scopes`` declaration all read
from these constants so they cannot drift.
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
