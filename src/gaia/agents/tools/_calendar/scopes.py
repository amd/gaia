# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The one scope each provider's calendar tools need, read and write alike."""

from __future__ import annotations

from typing import Dict

GOOGLE_CONNECTOR_ID = "google"
MICROSOFT_CONNECTOR_ID = "microsoft"

SCOPE_GOOGLE_CALENDAR_EVENTS = "https://www.googleapis.com/auth/calendar.events"
SCOPE_GRAPH_CALENDARS_READWRITE = "https://graph.microsoft.com/Calendars.ReadWrite"

CALENDAR_SCOPES: Dict[str, str] = {
    GOOGLE_CONNECTOR_ID: SCOPE_GOOGLE_CALENDAR_EVENTS,
    MICROSOFT_CONNECTOR_ID: SCOPE_GRAPH_CALENDARS_READWRITE,
}

__all__ = [
    "CALENDAR_SCOPES",
    "GOOGLE_CONNECTOR_ID",
    "MICROSOFT_CONNECTOR_ID",
    "SCOPE_GOOGLE_CALENDAR_EVENTS",
    "SCOPE_GRAPH_CALENDARS_READWRITE",
]
