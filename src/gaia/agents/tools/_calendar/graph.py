# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Microsoft Graph backend for the flagship agent's calendar tools."""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import tzlocal
from tzlocal.windows_tz import win_tz

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
    parse_instant,
)
from gaia.agents.tools._calendar.scopes import MICROSOFT_CONNECTOR_ID

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Without this Graph returns each event's own wall clock, unlabelled in practice.
_PREFER_UTC = 'outlook.timezone="UTC"'
_SELECT = (
    "id,subject,start,end,isAllDay,location,attendees,isOrganizer,organizer,webLink,"
    "type,showAs,responseStatus,isCancelled,originalStartTimeZone,originalEndTimeZone"
)
_MAX_PAGE = 100
_MAX_PAGES = 50


def _error_code(response: httpx.Response) -> str:
    """Graph's ``error.code``. Never its message, which is upstream prose."""
    try:
        parsed = response.json()
    except ValueError:
        return ""
    error = parsed.get("error") if isinstance(parsed, dict) else None
    code = error.get("code") if isinstance(error, dict) else ""
    return code if isinstance(code, str) else ""


def _utc_value(endpoint: Optional[Dict[str, Any]], event_id: Any) -> str:
    """A Graph ``dateTimeTimeZone`` as RFC 3339, refusing an unexpected zone."""
    endpoint = endpoint or {}
    value = endpoint.get("dateTime")
    zone = endpoint.get("timeZone")
    if value and zone == "UTC":
        return parse_instant(f"{value}Z").isoformat()
    # Graph ignores the UTC preference on write replies and uses the event's own zone.
    named = _named_zone(zone)
    if not value or named is None:
        raise CalendarError(
            f"Microsoft Graph returned event {event_id} with time {value!r} in zone "
            f"{zone!r} instead of UTC, so its real time can't be known."
        )
    wall_clock = parse_instant(f"{value}Z").replace(tzinfo=named)
    return wall_clock.astimezone(timezone.utc).isoformat()


def _named_zone(name: Optional[str]) -> Optional[tzinfo]:
    """*name* as a zone, IANA or Windows spelling; Graph may send either."""
    for candidate in (name, win_tz.get(name or "")):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        # OSError: a name like "Pacific" is a folder in the zone database.
        except (ZoneInfoNotFoundError, ValueError, OSError):
            continue
    return None


def _all_day_value(
    endpoint: Optional[Dict[str, Any]], original_zone: Optional[str], event_id: Any
) -> str:
    """The calendar date of a Graph all-day bound, or a refusal rather than a guess.

    An all-day bound is midnight in the zone it was written in. Asked for UTC, Graph
    can return that same moment off midnight, and its date is then a day out.
    """
    endpoint = endpoint or {}
    value = endpoint.get("dateTime")
    stated_zone = endpoint.get("timeZone")
    if value and stated_zone:
        # The clock reading is in the stated zone, so midnight there names the date.
        moment = parse_instant(f"{value}Z")
        if moment.time() == datetime.min.time():
            return moment.date().isoformat()
        zone = _named_zone(original_zone) if stated_zone == "UTC" else None
        if zone is not None:
            moment = moment.astimezone(zone)
            if moment.time() == datetime.min.time():
                return moment.date().isoformat()
    raise CalendarError(
        f"Microsoft Graph returned all-day event {event_id} with time {value!r} in "
        f"zone {endpoint.get('timeZone')!r}, originally {original_zone!r}. That isn't "
        "midnight in either, so its date can't be known. Check it in Outlook."
    )


def _parse_event(raw: Dict[str, Any]) -> CalendarEvent:
    if not raw.get("id"):
        raise CalendarError("Microsoft Graph returned an event with no id.")
    all_day = bool(raw.get("isAllDay"))
    if all_day:
        start_zone = raw.get("originalStartTimeZone")
        end_zone = raw.get("originalEndTimeZone") or start_zone
        start = _all_day_value(raw.get("start"), start_zone, raw["id"])
        end = _all_day_value(raw.get("end"), end_zone, raw["id"])
    else:
        start = _utc_value(raw.get("start"), raw["id"])
        end = _utc_value(raw.get("end"), raw["id"])
    response = (raw.get("responseStatus") or {}).get("response")
    organizer = (
        ((raw.get("organizer") or {}).get("emailAddress") or {}).get("address") or ""
    ).casefold()
    is_organizer = raw.get("isOrganizer") is True
    addresses = (
        (a.get("emailAddress") or {}).get("address") for a in raw.get("attendees") or []
    )
    cancelled = raw.get("isCancelled") is True
    return CalendarEvent(
        id=raw["id"],
        title=raw.get("subject") or "",
        start=start,
        end=end,
        all_day=all_day,
        time_zone=raw.get("originalStartTimeZone"),
        location=(raw.get("location") or {}).get("displayName") or None,
        attendees=tuple(
            a
            for a in addresses
            if a and not (is_organizer and a.casefold() == organizer)
        ),
        organizer_is_self=is_organizer,
        html_link=raw.get("webLink") or None,
        recurring_instance=raw.get("type") in ("occurrence", "exception"),
        series_master=raw.get("type") == "seriesMaster",
        blocks_time=(
            raw.get("showAs") != "free" and response != "declined" and not cancelled
        ),
        cancelled=cancelled,
    )


def _written_event(response: httpx.Response) -> CalendarEvent:
    """The event a 2xx write returned; the write stands whatever its reply holds."""
    try:
        return _parse_event(response.json())
    except (ValueError, CalendarError, AttributeError, KeyError, TypeError) as exc:
        raise CalendarWriteUnreadable(
            f"Microsoft Graph accepted the change but its reply couldn't be read ({exc})."
        ) from exc


def _this_computers_zone() -> str:
    """Outlook pins an all-day event to a zone; UTC would show a day off elsewhere."""
    try:
        name = tzlocal.get_localzone_name()
        ZoneInfo(name)
    # TypeError: tzlocal returns None when it can't find a zone at all.
    except (ZoneInfoNotFoundError, ValueError, OSError, TypeError) as exc:
        raise CalendarError(
            "This computer's time zone can't be named, so an all-day Outlook event "
            "might land on the wrong day. Nothing was changed. Set the system time "
            "zone, or set TZ to an IANA name such as America/New_York, then retry."
        ) from exc
    return name


def _endpoint(value: str, *, all_day: bool) -> Dict[str, str]:
    if all_day:
        return {"dateTime": f"{value}T00:00:00", "timeZone": _this_computers_zone()}
    moment = parse_instant(value).astimezone(timezone.utc)
    return {
        "dateTime": moment.strftime("%Y-%m-%dT%H:%M:%S"),
        "timeZone": "UTC",
    }


def _slot_fields(slot: EventSlot) -> Dict[str, Any]:
    return {
        "start": _endpoint(slot.start, all_day=slot.all_day),
        "end": _endpoint(slot.end, all_day=slot.all_day),
        "isAllDay": slot.all_day,
    }


class GraphCalendarBackend:
    """One connected account's default Outlook calendar."""

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
        detail = _error_code(response)
        if code == 401:
            raise CalendarAuthError(
                "Microsoft Graph rejected the access token (401), so the "
                "Microsoft connection needs reconnecting. "
                f"(request: {where})",
                connect=(MICROSOFT_CONNECTOR_ID,),
                reconnect=(MICROSOFT_CONNECTOR_ID,),
            )
        if code == 403:
            raise CalendarAuthError(
                "Microsoft Graph refused the calendar request (403). The connection "
                "may lack calendar access, so it needs reconnecting with calendar "
                f"access ticked. (request: {where}; "
                f"code: {detail or 'forbidden'})",
                connect=(MICROSOFT_CONNECTOR_ID,),
                reconnect=(MICROSOFT_CONNECTOR_ID,),
            )
        if code == 404:
            raise CalendarEventNotFound(f"Outlook found no event ({where}).")
        if code == 410:
            raise CalendarEventGone(f"Outlook deleted this event ({where}).")
        if code == 429:
            raise CalendarError(
                "Microsoft Graph is rate-limiting this account. Retry after "
                f"{response.headers.get('Retry-After', 'a short wait')}s. "
                f"(request: {where})"
            )
        raise CalendarError(
            f"Microsoft Graph request failed: {where} returned {code}"
            f"{f' ({detail})' if detail else ''}. Nothing was confirmed as changed."
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        where = f"{method} {url.split('?', 1)[0].removeprefix(GRAPH_API_BASE)}"
        try:
            response = self._client.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {self._access_token_fn()}",
                    "Prefer": _PREFER_UTC,
                },
                params=params,
                json=body,
            )
        except httpx.HTTPError as exc:
            raise CalendarError(
                f"Couldn't reach Microsoft Graph ({type(exc).__name__}) on {where}. "
                "If this was a change, check the calendar before retrying: it may "
                "have gone through."
            ) from exc
        if response.status_code >= 300:
            self._raise(response, where)
        return response

    @staticmethod
    def _event_url(event_id: str) -> str:
        return f"{GRAPH_API_BASE}/me/events/{event_path_segment(event_id)}"

    def list_events(self, window: CalendarWindow, *, limit: int) -> CalendarPage:
        events: List[CalendarEvent] = []
        url = f"{GRAPH_API_BASE}/me/calendarView"
        params: Optional[Dict[str, Any]] = {
            "startDateTime": window.start_utc,
            "endDateTime": window.end_utc,
            "$orderby": "start/dateTime",
            "$select": _SELECT,
            "$top": min(limit, _MAX_PAGE),
        }
        for _ in range(_MAX_PAGES):
            data = self._request("GET", url, params=params).json()
            events.extend(_parse_event(item) for item in data.get("value") or [])
            next_link = data.get("@odata.nextLink")
            if len(events) >= limit:
                return CalendarPage(
                    tuple(events[:limit]),
                    truncated=bool(next_link) or len(events) > limit,
                )
            if not next_link:
                return CalendarPage(tuple(events), truncated=False)
            # The bearer token follows this link, so it must stay on Graph.
            if not next_link.startswith(f"{GRAPH_API_BASE}/"):
                raise CalendarError(
                    "Microsoft Graph returned a next-page link outside Graph, so "
                    "the rest of this calendar window was not read."
                )
            url, params = next_link, None
        return CalendarPage(tuple(events), truncated=True)

    def get_event(self, event_id: str) -> CalendarEvent:
        response = self._request(
            "GET", self._event_url(event_id), params={"$select": _SELECT}
        )
        return _parse_event(response.json())

    def create_event(self, event: NewEvent) -> CalendarEvent:
        body: Dict[str, Any] = {"subject": event.title, **_slot_fields(event.slot)}
        if event.attendees:
            body["attendees"] = [
                {"emailAddress": {"address": a}, "type": "required"}
                for a in event.attendees
            ]
        if event.location:
            body["location"] = {"displayName": event.location}
        if event.description:
            body["body"] = {"contentType": "text", "content": event.description}
        response = self._request("POST", f"{GRAPH_API_BASE}/me/events", body=body)
        return _written_event(response)

    def update_event(self, event: CalendarEvent, patch: EventPatch) -> CalendarEvent:
        body: Dict[str, Any] = {}
        if patch.title is not None:
            body["subject"] = patch.title
        if patch.slot is not None:
            body.update(_slot_fields(patch.slot))
        if patch.location is not None:
            body["location"] = {"displayName": patch.location}
        if patch.description is not None:
            body["body"] = {"contentType": "text", "content": patch.description}
        response = self._request("PATCH", self._event_url(event.id), body=body)
        return _written_event(response)

    def cancel_event(self, event: CalendarEvent) -> CancelOutcome:
        url = self._event_url(event.id)
        if not event.organizer_is_self:
            self._request("DELETE", url)
            return CancelOutcome.REMOVED_FROM_YOUR_CALENDAR
        if event.attendees:
            self._request("POST", f"{url}/cancel", body={})
        else:
            # No one to tell, and /cancel is documented only for meetings.
            self._request("DELETE", url)
        return CancelOutcome.CANCELLED_FOR_EVERYONE


__all__ = ["GRAPH_API_BASE", "GraphCalendarBackend"]
