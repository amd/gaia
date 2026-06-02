# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Behavioral tests for ``LiveOutlookCalendarBackend`` (the MS Graph calendar
backend, #1276) against an ``httpx.MockTransport``.

The backend translates Microsoft Graph ``event`` JSON into the Google Calendar
v3 shape so the email agent's existing calendar tools (``calendar_tools.py``)
operate on it interchangeably with ``LiveCalendarBackend`` — the same seam the
mail side uses for Outlook (#1275). These tests assert:

1. ``LiveOutlookCalendarBackend`` satisfies the ``CalendarBackend`` Protocol and
   hits the correct Graph endpoints (``/me/calendarView`` when a window is
   given, ``/me/events`` otherwise; ``/me/events/{id}/accept|decline`` for
   RSVP), surfacing every non-2xx as an actionable ``ConnectorsError`` (NOT a
   silent empty result), with no Authorization-header leakage.
2. The agent's own calendar tool (``list_calendar_events_impl``) runs unchanged
   against the Outlook calendar backend and produces normal output — proving the
   Google-shaped contract holds end to end.

All network + token resolution is mocked; there are NO live Graph or OAuth
calls.
"""

from __future__ import annotations

import json
from typing import Callable, List, Tuple
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from gaia.agents.email.calendar_backend import CalendarBackend
from gaia.agents.email.outlook_calendar_backend import (
    LiveOutlookCalendarBackend,
    _get_outlook_calendar_token,
    graph_event_to_google,
)
from gaia.agents.email.tools.calendar_tools import list_calendar_events_impl
from gaia.connectors.errors import AuthRequiredError, ConnectorsError

# ---------------------------------------------------------------------------
# Test harness — mirrors test_outlook_backend.py
# ---------------------------------------------------------------------------


class _Recorder:
    """Records every request the backend makes, hands back canned responses."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]):
        self.requests: List[httpx.Request] = []
        self._handler = handler

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def _backend(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    token_fn: Callable[[], str] = lambda: "GRAPH-CAL-TOKEN-1",
) -> Tuple[LiveOutlookCalendarBackend, _Recorder, List[str]]:
    rec = _Recorder(handler)
    transport = httpx.MockTransport(rec)
    client = httpx.Client(transport=transport)

    token_calls: List[str] = []

    def _wrapped() -> str:
        tok = token_fn()
        token_calls.append(tok)
        return tok

    backend = LiveOutlookCalendarBackend(_wrapped, http_client=client)
    return backend, rec, token_calls


def _ok(body: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def _graph_event(
    *,
    event_id: str = "AAMkEVT1",
    subject: str = "Project sync",
    start_dt: str = "2026-06-10T15:00:00.0000000",
    end_dt: str = "2026-06-10T16:00:00.0000000",
    time_zone: str = "UTC",
    is_all_day: bool = False,
    location_name: str = "Conf Room A",
    organizer_name: str = "Alice Example",
    organizer_addr: str = "alice@example.com",
    web_link: str = "https://outlook.live.com/calendar/item/AAMkEVT1",
) -> dict:
    """Build a minimal MS Graph ``event`` resource (the shape Graph returns)."""
    return {
        "id": event_id,
        "subject": subject,
        "isAllDay": is_all_day,
        "start": {"dateTime": start_dt, "timeZone": time_zone},
        "end": {"dateTime": end_dt, "timeZone": time_zone},
        "location": {"displayName": location_name},
        "organizer": {
            "emailAddress": {"name": organizer_name, "address": organizer_addr}
        },
        "webLink": web_link,
    }


# ---------------------------------------------------------------------------
# Protocol conformance — the interchangeability contract
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_outlook_calendar_backend_satisfies_calendar_protocol(self):
        backend, _, _ = _backend(lambda r: _ok({}))
        # Structural runtime_checkable Protocol — the calendar tools depend on
        # this so they use Outlook and Google calendars interchangeably.
        assert isinstance(backend, CalendarBackend)


# ---------------------------------------------------------------------------
# Read translation — Graph event JSON -> Google Calendar v3 shape
# ---------------------------------------------------------------------------


class TestReadTranslation:
    def test_list_events_with_window_uses_calendarview(self):
        backend, rec, _ = _backend(lambda r: _ok({"value": []}))
        backend.list_events(
            time_min="2026-06-01T00:00:00Z",
            time_max="2026-06-30T00:00:00Z",
            max_results=5,
        )
        path = urlparse(str(rec.requests[0].url)).path
        # A bounded window expands recurring series into instances -> calendarView.
        assert path.endswith("/me/calendarView")
        params = parse_qs(urlparse(str(rec.requests[0].url)).query)
        assert params["startDateTime"] == ["2026-06-01T00:00:00Z"]
        assert params["endDateTime"] == ["2026-06-30T00:00:00Z"]
        assert params["$top"] == ["5"]

    def test_list_events_without_window_uses_events_endpoint(self):
        backend, rec, _ = _backend(lambda r: _ok({"value": []}))
        backend.list_events(max_results=7)
        path = urlparse(str(rec.requests[0].url)).path
        assert path.endswith("/me/events")
        params = parse_qs(urlparse(str(rec.requests[0].url)).query)
        assert params["$top"] == ["7"]

    def test_list_events_returns_google_items_envelope(self):
        backend, _, _ = _backend(
            lambda r: _ok({"value": [_graph_event(), _graph_event(event_id="E2")]})
        )
        out = backend.list_events(
            time_min="2026-06-01T00:00:00Z", time_max="2026-06-30T00:00:00Z"
        )
        # The calendar tool reads ``data.get("items", [])`` — Google envelope.
        assert "items" in out
        assert len(out["items"]) == 2

    def test_event_translates_to_google_shape(self):
        backend, _, _ = _backend(lambda r: _ok({"value": [_graph_event()]}))
        out = backend.list_events(
            time_min="2026-06-01T00:00:00Z", time_max="2026-06-30T00:00:00Z"
        )
        ev = out["items"][0]
        assert ev["id"] == "AAMkEVT1"
        assert ev["summary"] == "Project sync"
        # Timed event -> start/end carry ``dateTime``.
        assert ev["start"]["dateTime"] == "2026-06-10T15:00:00.0000000"
        assert ev["end"]["dateTime"] == "2026-06-10T16:00:00.0000000"
        # Location + organizer translated to the Google field names.
        assert ev["location"] == "Conf Room A"
        assert ev["organizer"]["email"] == "alice@example.com"

    def test_all_day_event_maps_to_date_not_datetime(self):
        backend, _, _ = _backend(
            lambda r: _ok(
                {
                    "value": [
                        _graph_event(
                            is_all_day=True,
                            start_dt="2026-06-10T00:00:00.0000000",
                            end_dt="2026-06-11T00:00:00.0000000",
                        )
                    ]
                }
            )
        )
        out = backend.list_events(
            time_min="2026-06-01T00:00:00Z", time_max="2026-06-30T00:00:00Z"
        )
        ev = out["items"][0]
        # All-day -> Google uses ``date`` (YYYY-MM-DD), not ``dateTime``.
        assert ev["start"].get("date") == "2026-06-10"
        assert "dateTime" not in ev["start"]
        assert ev["end"].get("date") == "2026-06-11"

    def test_translated_event_consumed_by_calendar_tool(self):
        # The translated shape must satisfy what list_calendar_events_impl reads.
        backend, _, _ = _backend(
            lambda r: _ok(
                {
                    "value": [
                        _graph_event(
                            subject="1:1 with manager",
                            location_name="Office",
                            organizer_addr="boss@example.com",
                        )
                    ]
                }
            )
        )
        out = list_calendar_events_impl(
            backend, time_min="2026-06-01T00:00:00Z", time_max="2026-06-30T00:00:00Z"
        )
        assert len(out["events"]) == 1
        tool_ev = out["events"][0]
        assert tool_ev["summary"] == "1:1 with manager"
        assert tool_ev["start"] == "2026-06-10T15:00:00.0000000"
        assert tool_ev["location"] == "Office"
        assert tool_ev["organizer"] == "boss@example.com"
        assert tool_ev["missing_organizer"] is False

    def test_empty_calendar_returns_empty_items_not_raise(self):
        backend, _, _ = _backend(lambda r: _ok({"value": []}))
        out = backend.list_events(
            time_min="2026-06-01T00:00:00Z", time_max="2026-06-30T00:00:00Z"
        )
        assert out["items"] == []

    def test_get_event_translates_single_event(self):
        backend, rec, _ = _backend(lambda r: _ok(_graph_event(event_id="E9")))
        ev = backend.get_event(event_id="E9")
        assert ev["id"] == "E9"
        assert ev["summary"] == "Project sync"
        assert rec.requests[0].url.path.endswith("/me/events/E9")

    def test_graph_event_to_google_handles_missing_organizer(self):
        # An event with no organizer must not crash and must surface as absent.
        ev = graph_event_to_google({"id": "x", "subject": "s", "isAllDay": False})
        assert ev["id"] == "x"
        assert ev["organizer"] == {}


# ---------------------------------------------------------------------------
# list_calendars
# ---------------------------------------------------------------------------


class TestListCalendars:
    def test_list_calendars_hits_calendars_endpoint(self):
        backend, rec, _ = _backend(
            lambda r: _ok(
                {
                    "value": [
                        {"id": "cal1", "name": "Calendar", "isDefaultCalendar": True}
                    ]
                }
            )
        )
        cals = backend.list_calendars()
        assert rec.requests[0].url.path.endswith("/me/calendars")
        assert len(cals) == 1
        assert cals[0]["id"] == "cal1"


# ---------------------------------------------------------------------------
# RSVP -> Graph action endpoints (accept / decline / tentativelyAccept)
# ---------------------------------------------------------------------------


class TestRsvp:
    def test_accept_posts_to_accept_endpoint(self):
        backend, rec, _ = _backend(lambda r: httpx.Response(202))
        backend.update_event_rsvp(
            event_id="E1", attendee_email="me@outlook.com", response_status="accepted"
        )
        assert rec.requests[0].method == "POST"
        assert rec.requests[0].url.path.endswith("/me/events/E1/accept")
        body = json.loads(rec.requests[0].content)
        assert body["sendResponse"] is True

    def test_decline_posts_to_decline_endpoint(self):
        backend, rec, _ = _backend(lambda r: httpx.Response(202))
        backend.update_event_rsvp(
            event_id="E1", attendee_email="me@outlook.com", response_status="declined"
        )
        assert rec.requests[0].url.path.endswith("/me/events/E1/decline")

    def test_tentative_posts_to_tentatively_accept(self):
        backend, rec, _ = _backend(lambda r: httpx.Response(202))
        backend.update_event_rsvp(
            event_id="E1", attendee_email="me@outlook.com", response_status="tentative"
        )
        assert rec.requests[0].url.path.endswith("/me/events/E1/tentativelyAccept")

    def test_unsupported_rsvp_status_raises_actionable(self):
        # needsAction has no Graph action endpoint — must raise, NOT silently
        # no-op (a silent no-op would leave the user thinking they RSVP'd).
        backend, _, _ = _backend(lambda r: httpx.Response(202))
        with pytest.raises(ConnectorsError) as exc:
            backend.update_event_rsvp(
                event_id="E1",
                attendee_email="me@outlook.com",
                response_status="needsAction",
            )
        assert "needsAction" in str(exc.value)


# ---------------------------------------------------------------------------
# create_event -> POST /me/events with translated payload
# ---------------------------------------------------------------------------


class TestCreateEvent:
    def test_create_event_posts_to_events_with_graph_payload(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "newE1"}, status=201))
        out = backend.create_event(
            summary="Lunch",
            start={"dateTime": "2026-06-10T12:00:00"},
            end={"dateTime": "2026-06-10T13:00:00"},
            attendees=["bob@example.com"],
            location="Cafe",
            description="Catch up",
        )
        assert out["id"] == "newE1"
        assert rec.requests[0].method == "POST"
        assert rec.requests[0].url.path.endswith("/me/events")
        body = json.loads(rec.requests[0].content)
        assert body["subject"] == "Lunch"
        # Google passes {"dateTime": iso}; Graph needs a paired timeZone.
        assert body["start"]["dateTime"] == "2026-06-10T12:00:00"
        assert body["start"]["timeZone"]
        assert body["location"]["displayName"] == "Cafe"
        assert body["attendees"][0]["emailAddress"]["address"] == "bob@example.com"

    def test_create_event_minimal_no_optionals(self):
        backend, rec, _ = _backend(lambda r: _ok({"id": "E"}, status=201))
        backend.create_event(
            summary="Solo",
            start={"dateTime": "2026-06-10T12:00:00"},
            end={"dateTime": "2026-06-10T13:00:00"},
        )
        body = json.loads(rec.requests[0].content)
        # No attendees/location keys when not supplied (don't send empties).
        assert "attendees" not in body or body["attendees"] == []
        assert "location" not in body


# ---------------------------------------------------------------------------
# Token freshness — every request gets a fresh token
# ---------------------------------------------------------------------------


class TestTokenFreshness:
    def test_each_request_invokes_token_fn(self):
        backend, _, token_calls = _backend(lambda r: _ok({"value": []}))
        backend.list_events()
        backend.list_events()
        assert len(token_calls) == 2

    def test_authorization_header_uses_returned_token(self):
        backend, rec, _ = _backend(
            lambda r: _ok({"value": []}),
            token_fn=lambda: "FRESH-CAL-TOKEN",
        )
        backend.list_events()
        assert rec.requests[0].headers["Authorization"] == "Bearer FRESH-CAL-TOKEN"


# ---------------------------------------------------------------------------
# Error surfacing — NO silent empty, NO token leakage (core AC)
# ---------------------------------------------------------------------------


class TestErrorSurfacing:
    def test_403_insufficient_scope_raises_actionable_not_empty(self):
        # A token lacking the Calendars scope at the Graph layer -> 403. This
        # MUST raise an actionable error, NOT return an empty event list.
        backend, _, _ = _backend(
            lambda r: httpx.Response(
                403,
                text=json.dumps(
                    {
                        "error": {
                            "code": "ErrorAccessDenied",
                            "message": "Access is denied.",
                        }
                    }
                ),
            )
        )
        with pytest.raises(ConnectorsError) as exc:
            backend.list_events(
                time_min="2026-06-01T00:00:00Z", time_max="2026-06-30T00:00:00Z"
            )
        msg = str(exc.value)
        assert "403" in msg
        # Actionable: names what to do (reconnect) and which provider.
        assert "Microsoft" in msg or "Outlook" in msg
        assert "reconnect" in msg.lower() or "scope" in msg.lower()
        # Names the calendar scope the connection is missing.
        assert "calendar" in msg.lower()

    def test_401_raises_with_reconnect_guidance(self):
        backend, _, _ = _backend(lambda r: httpx.Response(401, text="Unauthorized"))
        with pytest.raises(ConnectorsError) as exc:
            backend.list_events()
        msg = str(exc.value)
        assert "401" in msg
        assert "reconnect" in msg.lower()

    def test_500_includes_body_excerpt(self):
        backend, _, _ = _backend(
            lambda r: httpx.Response(500, text="internal graph error xyz")
        )
        with pytest.raises(ConnectorsError) as exc:
            backend.list_events()
        assert "500" in str(exc.value)
        assert "internal graph error" in str(exc.value)

    def test_error_does_not_leak_authorization_header(self):
        backend, _, _ = _backend(
            lambda r: httpx.Response(403, text="forbidden"),
            token_fn=lambda: "supersecretcaltoken",
        )
        with pytest.raises(ConnectorsError) as exc:
            backend.get_event(event_id="E1")
        full = repr(exc.value) + " " + str(exc.value) + " " + str(exc.value.__cause__)
        assert "Bearer " not in full, f"token leaked: {full!r}"
        assert "supersecretcaltoken" not in full

    def test_rsvp_403_raises_actionable(self):
        backend, _, _ = _backend(lambda r: httpx.Response(403, text="forbidden"))
        with pytest.raises(ConnectorsError) as exc:
            backend.update_event_rsvp(
                event_id="E1",
                attendee_email="me@outlook.com",
                response_status="accepted",
            )
        assert "403" in str(exc.value)


# ---------------------------------------------------------------------------
# Token resolver — grant gating raises (no silent empty), no live OAuth
# ---------------------------------------------------------------------------


class TestScopeConformance:
    def test_calendar_scopes_are_subset_of_catalog_available_scopes(self):
        # The grant ledger refuses a token request for any scope not declared in
        # the connector catalog (#1105). Pin OUTLOOK_CALENDAR_SCOPES to the
        # catalog so the two cannot drift.
        from gaia.agents.email.outlook_scopes import OUTLOOK_CALENDAR_SCOPES
        from gaia.connectors.catalog.microsoft import MICROSOFT_SPEC

        for scope in OUTLOOK_CALENDAR_SCOPES:
            assert scope in MICROSOFT_SPEC.available_scopes, scope


class TestTokenResolver:
    def test_get_outlook_calendar_token_returns_access_token(self, monkeypatch):
        captured = {}

        def fake_get_credential_sync(connector_id, *, agent_id, required_scopes):
            captured["connector_id"] = connector_id
            captured["agent_id"] = agent_id
            captured["scopes"] = list(required_scopes)
            return {"access_token": "CAL-TOK-123", "scopes": list(required_scopes)}

        monkeypatch.setattr(
            "gaia.agents.email.outlook_calendar_backend.get_credential_sync",
            fake_get_credential_sync,
        )
        token = _get_outlook_calendar_token()
        assert token == "CAL-TOK-123"
        # Uses the microsoft connector + the email agent's namespaced id.
        assert captured["connector_id"] == "microsoft"
        assert captured["agent_id"] == "builtin:email"
        # Requests the Graph Calendars scope (NOT the mail scope).
        assert any("graph.microsoft.com/Calendars" in s for s in captured["scopes"])

    def test_get_outlook_calendar_token_propagates_grant_error_not_empty(
        self, monkeypatch
    ):
        # When the user hasn't granted the calendar scope, the grant dispatcher
        # raises AuthRequiredError. The backend must let it propagate — never
        # swallow it into an empty token / empty calendar.
        def fake_get_credential_sync(connector_id, *, agent_id, required_scopes):
            raise AuthRequiredError(
                AuthRequiredError.Reason.AGENT_NOT_GRANTED,
                provider="microsoft",
                agent_id=agent_id,
                missing_scopes=required_scopes,
            )

        monkeypatch.setattr(
            "gaia.agents.email.outlook_calendar_backend.get_credential_sync",
            fake_get_credential_sync,
        )
        with pytest.raises(AuthRequiredError) as exc:
            _get_outlook_calendar_token()
        assert exc.value.reason is AuthRequiredError.Reason.AGENT_NOT_GRANTED
