# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Preserve Microsoft Graph's organizer flag for invite grounding (#2787)."""

import pytest
from gaia_agent_email.outlook_calendar_backend import graph_event_to_google


@pytest.mark.parametrize("is_organizer", [False, True])
def test_graph_is_organizer_becomes_organizer_self(is_organizer):
    event = {
        "id": "evt-1",
        "subject": "Vendor review",
        "organizer": {
            "emailAddress": {
                "address": "vendor@example.com",
            }
        },
        "isOrganizer": is_organizer,
    }

    translated = graph_event_to_google(event)

    assert translated["organizer"] == {
        "email": "vendor@example.com",
        "self": is_organizer,
    }
