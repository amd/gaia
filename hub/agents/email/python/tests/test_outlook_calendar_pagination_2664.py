# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Microsoft Graph continuation metadata for calendar listing (#2664)."""

import httpx
from gaia_agent_email.outlook_calendar_backend import LiveOutlookCalendarBackend


def test_graph_next_link_is_preserved_for_calendar_truncation_signal():
    next_link = "https://graph.microsoft.com/v1.0/me/calendarView?$skiptoken=page-2"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [{"id": "event-1"}],
                "@odata.nextLink": next_link,
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        backend = LiveOutlookCalendarBackend(lambda: "fake-token", http_client=client)
        result = backend.list_events(
            time_min="2026-08-01T00:00:00Z",
            time_max="2026-08-31T00:00:00Z",
            max_results=25,
        )
    finally:
        client.close()

    assert result["items"][0]["id"] == "event-1"
    assert result["nextPageToken"] == next_link
