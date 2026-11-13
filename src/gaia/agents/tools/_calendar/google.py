# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Google Calendar v3 backend for the flagship agent's calendar tools."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import httpx

from gaia.agents.tools._calendar.errors import (
    CalendarAuthError,
    CalendarError,
    CalendarEventGone,
    CalendarEventNotFound,
    CalendarWriteUnreadable,
)
from gaia.agents.tools._calendar.model import (
    CalendarEvent,
    CalendarPage,
    CalendarWindow,
    CancelOutcome,
    EventPatch,
    EventSlot,
    NewEvent,
    event_path_segment,
)
from gaia.agents.tools._calendar.scopes import GOOGLE_CONNECTOR_ID

GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
_EVENTS_PATH = "/calendars/primary/events"
_ENABLE_URL = (
    "https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"
)
_MAX_PAGE = 250
_MAX_PAGES = 50
_SCOPE_REASONS = {"insufficientPermissions", "insufficientAuthenticationScopes"}


def _error_reason(response: httpx.Response) -> str:
    """Google's structured reason. Never its message, which is upstream prose."""
    try:
        parsed = response.json()
    except ValueError:
        return ""
    error = parsed.get("error") if isinstance(parsed, dict) else None
    errors = error.get("errors") if isinstance(error, dict) else None
    first = errors[0] if isinstance(errors, list) and errors else None
    reason = first.get("reason") if isinstance(first, dict) else ""
    return reason if isinstance(reason, str) else ""


def _parse_event(raw: Dict[str, Any]) -> CalendarEvent:
    start = raw.get("start") or {}
    end = raw.get("end") or {}
    all_day = "date" in start
    start_value = start.get("date") or start.get("dateTime")
    end_value = end.get("date") or end.get("dateTime")
    if not raw.get("id") or not start_value or not end_value:
        raise CalendarError(
            "Google Calendar returned an event with no id or times, so it can't be "
            f"read safely (event id: {raw.get('id') or 'none'})."
        )
    attendees = raw.get("attendees") or []
    me = next((a for a in attendees if a.get("self")), {})
    cancelled = raw.get("status") == "cancelled"
    return CalendarEvent(
        id=raw["id"],
        title=raw.get("summary") or "",
        start=start_value,
        end=end_value,
        all_day=all_day,
        time_zone=start.get("timeZone"),
        location=raw.get("location") or None,
        attendees=tuple(
            a["email"] for a in attendees if a.get("email") and not a.get("self")
        ),
        organizer_is_self=(raw.get("organizer") or {}).get("self") is True,
        html_link=raw.get("htmlLink") or None,
        recurring_instance=bool(raw.get("recurringEventId")),
        series_master=bool(raw.get("recurrence")),
        blocks_time=(
            raw.get("transparency") != "transparent"
            and me.get("responseStatus") != "declined"
            and not cancelled
        ),
        cancelled=cancelled,
    )


def _written_event(response: httpx.Response) -> CalendarEvent:
    """The event a 2xx write returned; the write stands whatever its reply holds."""
    try:
        return _parse_event(response.json())
    except (ValueError, CalendarError, AttributeError, KeyError, TypeError) as exc:
        raise CalendarWriteUnreadable(
            f"Google Calendar accepted the change but its reply couldn't be read ({exc})."
        ) from exc


def _slot_fields(slot: EventSlot) -> Dict[str, Any]:
    # Explicit nulls: PATCH merges these objects, so the other kind must be cleared.
    if slot.all_day:
        return {
            "start": {"date": slot.start, "dateTime": None},
            "end": {"date": slot.end, "dateTime": None},
        }
    return {
        "start": {"dateTime": slot.start, "date": None},
        "end": {"dateTime": slot.end, "date": None},
    }


def _notify_params(attendees: Any) -> Dict[str, str]:
    return {"sendUpdates": "all"} if attendees else {}


class GoogleCalendarBackend:
    """One connected account's primary Google calendar."""

    def __init__(
        self,
        access_token_fn: Callable[[], str],
        *,
        http_client: Optional[httpx.Client] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._access_token_fn = access_token_fn
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    def _raise(self, response: httpx.Response, where: str) -> None:
        code = response.status_code
        reason = _error_reason(response)
        if code == 401:
            raise CalendarAuthError(
                "Google Calendar rejected the access token (401), so the Google "
                "connection needs reconnecting. "
                f"(request: {where})",
                connect=(GOOGLE_CONNECTOR_ID,),
                reconnect=(GOOGLE_CONNECTOR_ID,),
            )
        if code == 404:
            raise CalendarEventNotFound(f"Google Calendar found no event ({where}).")
        if code == 410:
            raise CalendarEventGone(f"Google Calendar deleted this event ({where}).")
        if code == 403 and reason == "accessNotConfigured":
            raise CalendarError(
                "The Google Calendar API is not enabled for the Google Cloud project "
                f"behind this connection. Enable it, wait a minute, then retry: "
                f"{_ENABLE_URL}"
            )
        if code == 403 and reason in _SCOPE_REASONS:
            raise CalendarAuthError(
                "Google refused the calendar request because the connection lacks "
                "calendar access, so it needs reconnecting with calendar access "
                f"ticked. (request: {where})",
                connect=(GOOGLE_CONNECTOR_ID,),
                reconnect=(GOOGLE_CONNECTOR_ID,),
            )
        if code == 429 or (
            code == 403 and reason.lower().endswith("ratelimitexceeded")
        ):
            raise CalendarError(
                "Google Calendar is rate-limiting this account. Retry after "
                f"{response.headers.get('Retry-After', 'a short wait')}. "
                f"(request: {where})"
            )
        raise CalendarError(
            f"Google Calendar request failed: {where} returned {code}"
            f"{f' ({reason})' if reason else ''}. Nothing was confirmed as changed."
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        where = f"{method} {path}"
        try:
            response = self._client.request(
                method,
                f"{GOOGLE_CALENDAR_API_BASE}{path}",
                headers={"Authorization": f"Bearer {self._access_token_fn()}"},
                params=params,
                json=body,
            )
        except httpx.HTTPError as exc:
            raise CalendarError(
                f"Couldn't reach Google Calendar ({type(exc).__name__}) on {where}. "
                "If this was a change, check the calendar before retrying: it may "
                "have gone through."
            ) from exc
        if response.status_code >= 300:
            self._raise(response, where)
        return response

    @staticmethod
    def _event_path(event_id: str) -> str:
        return f"{_EVENTS_PATH}/{event_path_segment(event_id)}"

    def list_events(self, window: CalendarWindow, *, limit: int) -> CalendarPage:
        events: List[CalendarEvent] = []
        token: Optional[str] = None
        for _ in range(_MAX_PAGES):
            params: Dict[str, Any] = {
                "timeMin": window.start_utc,
                "timeMax": window.end_utc,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": min(limit - len(events), _MAX_PAGE),
            }
            if token:
                params["pageToken"] = token
            data = self._request("GET", _EVENTS_PATH, params=params).json()
            events.extend(_parse_event(item) for item in data.get("items") or [])
            token = data.get("nextPageToken")
            if not token:
                return CalendarPage(tuple(events), truncated=False)
            if len(events) >= limit:
                break
        return CalendarPage(tuple(events), truncated=True)

    def get_event(self, event_id: str) -> CalendarEvent:
        event = _parse_event(self._request("GET", self._event_path(event_id)).json())
        # Google keeps a deleted event readable with this status.
        if event.cancelled:
            raise CalendarEventGone(f"Google Calendar event {event_id} was cancelled.")
        return event

    def create_event(self, event: NewEvent) -> CalendarEvent:
        body: Dict[str, Any] = {"summary": event.title, **_slot_fields(event.slot)}
        if event.attendees:
            body["attendees"] = [{"email": a} for a in event.attendees]
        if event.location:
            body["location"] = event.location
        if event.description:
            body["description"] = event.description
        response = self._request(
            "POST", _EVENTS_PATH, params=_notify_params(event.attendees), body=body
        )
        return _written_event(response)

    def update_event(self, event: CalendarEvent, patch: EventPatch) -> CalendarEvent:
        body: Dict[str, Any] = {}
        if patch.title is not None:
            body["summary"] = patch.title
        if patch.slot is not None:
            body.update(_slot_fields(patch.slot))
        if patch.location is not None:
            body["location"] = patch.location
        if patch.description is not None:
            body["description"] = patch.description
        response = self._request(
            "PATCH",
            self._event_path(event.id),
            params=_notify_params(event.attendees),
            body=body,
        )
        return _written_event(response)

    def cancel_event(self, event: CalendarEvent) -> CancelOutcome:
        notifies = event.attendees if event.organizer_is_self else ()
        self._request(
            "DELETE", self._event_path(event.id), params=_notify_params(notifies)
        )
        return (
            CancelOutcome.CANCELLED_FOR_EVERYONE
            if event.organizer_is_self
            else CancelOutcome.REMOVED_FROM_YOUR_CALENDAR
        )


__all__ = ["GOOGLE_CALENDAR_API_BASE", "GoogleCalendarBackend"]
