# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Calendar backends: the request each provider actually receives.

A real ``httpx`` client over ``MockTransport``, so these assert the outgoing
call rather than that a stub was reached. No network, no real calendar.
"""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from gaia.agents.tools._calendar import (
    CalendarAuthError,
    CalendarError,
    CalendarEventGone,
    CalendarEventNotFound,
    CalendarWindow,
    CancelOutcome,
    EventPatch,
    GoogleCalendarBackend,
    GraphCalendarBackend,
    NewEvent,
)
from gaia.agents.tools._calendar.model import (
    CalendarEvent,
    parse_instant,
    parse_slot,
)

EST = timezone(timedelta(hours=-5))
WINDOW = CalendarWindow(
    datetime(2026, 11, 3, 9, tzinfo=EST), datetime(2026, 11, 3, 17, tzinfo=EST)
)
TIMED = parse_slot("2026-11-03T09:00:00-05:00", "2026-11-03T10:00:00-05:00")
ALL_DAY = parse_slot("2026-11-03", "2026-11-03")


def _google(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GoogleCalendarBackend(lambda: "tok", http_client=client)


def _graph(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GraphCalendarBackend(lambda: "tok", http_client=client)


def _recording(payload, status=200):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=payload)

    return seen, handler


def _event(**over):
    base = dict(
        id="e/1+2",
        title="Sync",
        start="2026-11-03T09:00:00-05:00",
        end="2026-11-03T10:00:00-05:00",
        all_day=False,
        time_zone=None,
        location=None,
        attendees=(),
        organizer_is_self=True,
        html_link=None,
        recurring_instance=False,
        series_master=False,
        blocks_time=True,
        cancelled=False,
    )
    return CalendarEvent(**{**base, **over})


GOOGLE_RAW = {
    "id": "g1",
    "summary": "Sync",
    "start": {"dateTime": "2026-11-03T09:00:00-05:00", "timeZone": "America/New_York"},
    "end": {"dateTime": "2026-11-03T10:00:00-05:00"},
    "organizer": {"self": True},
    "attendees": [
        {"email": "me@x.com", "self": True, "responseStatus": "accepted"},
        {"email": "dana@x.com"},
    ],
    "htmlLink": "https://cal/g1",
}

GRAPH_RAW = {
    "id": "m1",
    "subject": "Sync",
    "start": {"dateTime": "2026-11-03T14:00:00.0000000", "timeZone": "UTC"},
    "end": {"dateTime": "2026-11-03T15:00:00.0000000", "timeZone": "UTC"},
    "isAllDay": False,
    "isOrganizer": True,
    "organizer": {"emailAddress": {"address": "me@x.com"}},
    "attendees": [
        {"emailAddress": {"address": "me@x.com"}},
        {"emailAddress": {"address": "dana@x.com"}},
    ],
    "type": "singleInstance",
    "showAs": "busy",
    "originalStartTimeZone": "Eastern Standard Time",
}


class TestGoogleReads:
    def test_list_asks_for_expanded_instances_with_utc_bounds(self):
        seen, handler = _recording({"items": [GOOGLE_RAW]})
        page = _google(handler).list_events(WINDOW, limit=25)
        params = seen[0].url.params
        assert seen[0].url.path == "/calendar/v3/calendars/primary/events"
        assert params["singleEvents"] == "true"
        assert params["orderBy"] == "startTime"
        assert params["timeMin"] == "2026-11-03T14:00:00Z"
        assert params["timeMax"] == "2026-11-03T22:00:00Z"
        event = page.events[0]
        assert event.attendees == ("dana@x.com",)
        assert event.organizer_is_self and event.time_zone == "America/New_York"
        assert not page.truncated

    def test_an_empty_calendar_is_an_empty_complete_page(self):
        _, handler = _recording({"items": []})
        page = _google(handler).list_events(WINDOW, limit=25)
        assert page.events == () and not page.truncated

    def test_follows_pages_until_the_limit_and_reports_more(self):
        calls = []

        def handler(request):
            calls.append(request.url.params.get("pageToken"))
            return httpx.Response(
                200, json={"items": [GOOGLE_RAW], "nextPageToken": "n"}
            )

        page = _google(handler).list_events(WINDOW, limit=2)
        assert calls == [None, "n"]
        assert len(page.events) == 2 and page.truncated

    def test_all_day_uses_date_with_its_stored_end(self):
        raw = {
            "id": "a",
            "summary": "Off",
            "start": {"date": "2026-11-03"},
            "end": {"date": "2026-11-04"},
        }
        _, handler = _recording({"items": [raw]})
        event = _google(handler).list_events(WINDOW, limit=5).events[0]
        assert event.all_day and (event.start, event.end) == (
            "2026-11-03",
            "2026-11-04",
        )

    @pytest.mark.parametrize(
        "over",
        [
            {"transparency": "transparent"},
            {
                "attendees": [
                    {"email": "me@x", "self": True, "responseStatus": "declined"}
                ]
            },
        ],
    )
    def test_free_and_declined_do_not_block_time(self, over):
        _, handler = _recording({"items": [{**GOOGLE_RAW, **over}]})
        assert not _google(handler).list_events(WINDOW, limit=5).events[0].blocks_time

    def test_recurring_flags(self):
        _, instance = _recording({**GOOGLE_RAW, "recurringEventId": "s"})
        assert _google(instance).get_event("g1_x").recurring_instance
        _, master = _recording({**GOOGLE_RAW, "recurrence": ["RRULE:FREQ=DAILY"]})
        assert _google(master).get_event("s").series_master

    def test_a_deleted_event_reads_as_gone(self):
        _, handler = _recording({**GOOGLE_RAW, "status": "cancelled"})
        with pytest.raises(CalendarEventGone):
            _google(handler).get_event("g1")


class TestGoogleWrites:
    def test_create_with_attendees_notifies_them(self):
        seen, handler = _recording(GOOGLE_RAW)
        _google(handler).create_event(
            NewEvent("Sync", TIMED, attendees=("dana@x.com",), location="Room")
        )
        body = json.loads(seen[0].content)
        assert seen[0].method == "POST"
        assert seen[0].url.params["sendUpdates"] == "all"
        assert body["attendees"] == [{"email": "dana@x.com"}]
        assert body["start"]["dateTime"] == "2026-11-03T09:00:00-05:00"
        assert body["location"] == "Room"

    def test_create_without_attendees_sends_no_notification_flag(self):
        seen, handler = _recording(GOOGLE_RAW)
        _google(handler).create_event(NewEvent("Sync", TIMED))
        assert "sendUpdates" not in seen[0].url.params
        assert "attendees" not in json.loads(seen[0].content)

    def test_all_day_create_uses_date_with_an_exclusive_end(self):
        seen, handler = _recording(GOOGLE_RAW)
        _google(handler).create_event(NewEvent("Off", ALL_DAY))
        body = json.loads(seen[0].content)
        assert body["start"]["date"] == "2026-11-03"
        assert body["end"]["date"] == "2026-11-04"

    def test_patch_sends_only_changed_fields_and_encodes_the_id(self):
        seen, handler = _recording(GOOGLE_RAW)
        _google(handler).update_event(
            _event(attendees=("dana@x.com",)), EventPatch(title="New")
        )
        assert seen[0].method == "PATCH"
        assert json.loads(seen[0].content) == {"summary": "New"}
        assert seen[0].url.raw_path.startswith(
            b"/calendar/v3/calendars/primary/events/e%2F1%2B2"
        )
        assert seen[0].url.params["sendUpdates"] == "all"

    def test_cancel_as_organizer_notifies_and_says_so(self):
        seen = []
        outcome = _google(
            lambda r: (seen.append(r), httpx.Response(204))[1]
        ).cancel_event(_event(attendees=("dana@x.com",)))
        assert seen[0].method == "DELETE"
        assert seen[0].url.params["sendUpdates"] == "all"
        assert outcome is CancelOutcome.CANCELLED_FOR_EVERYONE

    def test_cancel_as_guest_only_removes_it_and_notifies_no_one(self):
        seen = []
        outcome = _google(
            lambda r: (seen.append(r), httpx.Response(204))[1]
        ).cancel_event(_event(attendees=("dana@x.com",), organizer_is_self=False))
        assert "sendUpdates" not in seen[0].url.params
        assert outcome is CancelOutcome.REMOVED_FROM_YOUR_CALENDAR

    @pytest.mark.parametrize(
        "status, error", [(404, CalendarEventNotFound), (410, CalendarEventGone)]
    )
    def test_a_missing_event_says_how_it_is_missing(self, status, error):
        _, handler = _recording({}, status=status)
        with pytest.raises(error):
            _google(handler).cancel_event(_event())

    def test_auth_and_api_disabled_errors_are_distinct(self):
        _, unauthorized = _recording({}, status=401)
        with pytest.raises(CalendarAuthError):
            _google(unauthorized).get_event("g1")
        _, disabled = _recording(
            {"error": {"errors": [{"reason": "accessNotConfigured"}]}}, status=403
        )
        with pytest.raises(CalendarError, match="not enabled"):
            _google(disabled).get_event("g1")

    def test_a_server_body_never_reaches_the_error(self):
        def handler(_):
            return httpx.Response(500, text="<html>secret page</html>")

        with pytest.raises(CalendarError) as caught:
            _google(handler).get_event("g1")
        assert "secret" not in str(caught.value)


class TestGraphReads:
    def test_list_uses_calendar_view_and_prefers_utc(self):
        seen, handler = _recording({"value": [GRAPH_RAW]})
        page = _graph(handler).list_events(WINDOW, limit=25)
        assert seen[0].url.path == "/v1.0/me/calendarView"
        assert seen[0].headers["Prefer"] == 'outlook.timezone="UTC"'
        assert seen[0].url.params["startDateTime"] == "2026-11-03T14:00:00Z"
        assert seen[0].url.params["endDateTime"] == "2026-11-03T22:00:00Z"
        event = page.events[0]
        assert event.start == "2026-11-03T14:00:00+00:00"
        assert event.attendees == ("dana@x.com",)

    def test_get_also_prefers_utc(self):
        seen, handler = _recording(GRAPH_RAW)
        _graph(handler).get_event("m/1")
        assert seen[0].headers["Prefer"] == 'outlook.timezone="UTC"'
        assert seen[0].url.raw_path.startswith(b"/v1.0/me/events/m%2F1")

    def test_a_time_not_in_utc_is_refused_rather_than_assumed(self):
        raw = {
            **GRAPH_RAW,
            "start": {"dateTime": "2026-11-03T09:00:00", "timeZone": "Pacific"},
        }
        _, handler = _recording(raw)
        with pytest.raises(CalendarError, match="instead of UTC"):
            _graph(handler).get_event("m1")

    def test_follows_next_link_until_exhausted(self):
        calls = []

        def handler(request):
            calls.append(str(request.url))
            if len(calls) == 1:
                return httpx.Response(
                    200,
                    json={
                        "value": [GRAPH_RAW],
                        "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/calendarView?$skip=1",
                    },
                )
            return httpx.Response(200, json={"value": [GRAPH_RAW]})

        page = _graph(handler).list_events(WINDOW, limit=25)
        assert len(calls) == 2 and "$skip=1" in calls[1].replace("%24", "$")
        assert len(page.events) == 2 and not page.truncated

    def test_a_next_link_off_graph_is_refused_so_the_token_stays_home(self):
        _, handler = _recording(
            {"value": [GRAPH_RAW], "@odata.nextLink": "https://evil.example/next"}
        )
        with pytest.raises(CalendarError, match="outside Graph"):
            _graph(handler).list_events(WINDOW, limit=25)

    def test_a_next_link_past_the_limit_reports_truncation(self):
        _, handler = _recording(
            {
                "value": [GRAPH_RAW],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
            }
        )
        assert _graph(handler).list_events(WINDOW, limit=1).truncated

    def test_all_day_reads_as_dates(self):
        raw = {
            **GRAPH_RAW,
            "isAllDay": True,
            "start": {"dateTime": "2026-11-03T00:00:00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": "2026-11-04T00:00:00.0000000", "timeZone": "UTC"},
        }
        _, handler = _recording(raw)
        event = _graph(handler).get_event("m1")
        assert event.all_day and (event.start, event.end) == (
            "2026-11-03",
            "2026-11-04",
        )

    @pytest.mark.parametrize(
        "over",
        [
            {"showAs": "free"},
            {"responseStatus": {"response": "declined"}},
            {"isCancelled": True},
        ],
    )
    def test_free_declined_and_cancelled_do_not_block_time(self, over):
        _, handler = _recording({**GRAPH_RAW, **over})
        assert not _graph(handler).get_event("m1").blocks_time

    def test_recurring_flags(self):
        _, occurrence = _recording({**GRAPH_RAW, "type": "occurrence"})
        assert _graph(occurrence).get_event("m1").recurring_instance
        _, master = _recording({**GRAPH_RAW, "type": "seriesMaster"})
        assert _graph(master).get_event("m1").series_master


class TestGraphWrites:
    def test_timed_create_pairs_date_time_with_time_zone(self):
        seen, handler = _recording(GRAPH_RAW, status=201)
        _graph(handler).create_event(NewEvent("Sync", TIMED, attendees=("d@x.com",)))
        body = json.loads(seen[0].content)
        assert body["start"] == {"dateTime": "2026-11-03T14:00:00", "timeZone": "UTC"}
        assert body["end"] == {"dateTime": "2026-11-03T15:00:00", "timeZone": "UTC"}
        assert body["isAllDay"] is False
        assert body["attendees"][0]["emailAddress"]["address"] == "d@x.com"

    def test_all_day_create_has_midnight_bounds_and_is_all_day(self):
        seen, handler = _recording(GRAPH_RAW, status=201)
        _graph(handler).create_event(NewEvent("Off", ALL_DAY))
        body = json.loads(seen[0].content)
        assert body["isAllDay"] is True
        assert body["start"]["dateTime"] == "2026-11-03T00:00:00"
        assert body["end"]["dateTime"] == "2026-11-04T00:00:00"

    def test_patch_sends_only_changed_fields(self):
        seen, handler = _recording(GRAPH_RAW)
        _graph(handler).update_event(_event(), EventPatch(location="Room"))
        assert seen[0].method == "PATCH"
        assert json.loads(seen[0].content) == {"location": {"displayName": "Room"}}

    def test_organizer_cancel_of_a_meeting_posts_cancel(self):
        seen = []
        outcome = _graph(
            lambda r: (seen.append(r), httpx.Response(202))[1]
        ).cancel_event(_event(attendees=("d@x.com",)))
        assert seen[0].method == "POST"
        assert seen[0].url.raw_path.endswith(b"/e%2F1%2B2/cancel")
        assert outcome is CancelOutcome.CANCELLED_FOR_EVERYONE

    def test_non_organizer_cancel_deletes_and_says_only_their_calendar(self):
        seen = []
        outcome = _graph(
            lambda r: (seen.append(r), httpx.Response(204))[1]
        ).cancel_event(_event(attendees=("d@x.com",), organizer_is_self=False))
        assert seen[0].method == "DELETE"
        assert outcome is CancelOutcome.REMOVED_FROM_YOUR_CALENDAR

    def test_organizer_with_no_attendees_deletes(self):
        seen = []
        _graph(lambda r: (seen.append(r), httpx.Response(204))[1]).cancel_event(
            _event()
        )
        assert seen[0].method == "DELETE"

    def test_a_second_cancel_is_not_found(self):
        _, handler = _recording({"error": {"code": "ErrorItemNotFound"}}, status=404)
        with pytest.raises(CalendarEventNotFound):
            _graph(handler).cancel_event(_event())


class TestTimeRules:
    @pytest.mark.parametrize("value", ["2026-11-03T09:00:00", "2026-11-03", ""])
    def test_no_offset_is_refused(self, value):
        with pytest.raises(CalendarError):
            parse_instant(value, "start")

    def test_the_refusal_says_how_to_fix_it(self):
        with pytest.raises(CalendarError, match="no UTC offset.*This computer's time"):
            parse_instant("2026-11-03T09:00:00", "start")

    def test_seven_fractional_digits_parse(self):
        assert parse_instant("2026-11-03T14:00:00.1234567Z").microsecond == 123456

    def test_a_mixed_slot_is_refused(self):
        with pytest.raises(CalendarError, match="must match"):
            parse_slot("2026-11-03", "2026-11-03T10:00:00-05:00")

    def test_an_inverted_window_is_refused(self):
        with pytest.raises(CalendarError, match="must be after"):
            CalendarWindow(WINDOW.end, WINDOW.start)
