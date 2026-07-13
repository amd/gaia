# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Calendar backend Protocol + ``LiveCalendarBackend``.

Same architectural pattern as ``gmail_backend.py``: a Protocol so the
eval harness can inject a fake; semantic methods (``accept_invite``,
``decline_invite``) so the seam is provider-agnostic for #963.
"""

from __future__ import annotations

from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Protocol,
    runtime_checkable,
)

import httpx

from gaia_agent_email.scopes import AGENT_NAMESPACED_ID, CALENDAR_SCOPES
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.handler import get_credential_sync
from gaia.logger import get_logger

log = get_logger(__name__)


CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class CalendarBackend(Protocol):
    def list_calendars(self) -> List[Dict[str, Any]]: ...

    def list_events(
        self,
        *,
        calendar_id: str = "primary",
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 25,
    ) -> Dict[str, Any]: ...

    def get_event(
        self, *, calendar_id: str = "primary", event_id: str
    ) -> Dict[str, Any]: ...

    def update_event_rsvp(
        self,
        *,
        calendar_id: str = "primary",
        event_id: str,
        attendee_email: str,
        response_status: str,
    ) -> Dict[str, Any]:
        """Update the user's RSVP on a calendar event.

        ``response_status``: ``accepted`` / ``declined`` / ``tentative`` / ``needsAction``.
        """
        ...

    def create_event(
        self,
        *,
        calendar_id: str = "primary",
        summary: str,
        start: Dict[str, str],
        end: Dict[str, str],
        attendees: Optional[Iterable[str]] = None,
        location: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]: ...


# ---------------------------------------------------------------------------
# LiveCalendarBackend
# ---------------------------------------------------------------------------


class LiveCalendarBackend:
    def __init__(
        self,
        access_token_fn: Callable[[], str],
        *,
        http_client: Optional[httpx.Client] = None,
        timeout_seconds: float = 15.0,
    ):
        self._access_token_fn = access_token_fn
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    def _headers(self) -> Dict[str, str]:
        token = self._access_token_fn()
        return {"Authorization": f"Bearer {token}"}

    def _raise_http(self, response: httpx.Response, where: str) -> None:
        if response.status_code == 401:
            raise ConnectorsError(
                "Calendar API returned 401. The access token may have expired or "
                "scopes were revoked. Reconnect Google in Settings → "
                f"Connectors. (where: {where})"
            )
        raise ConnectorsError(
            f"Calendar API {where} returned {response.status_code}: "
            f"{response.text[:300]}"
        )

    def _get(self, path: str, *, params: Optional[dict] = None) -> Any:
        resp = self._client.get(
            f"{CALENDAR_API_BASE}{path}", headers=self._headers(), params=params
        )
        if resp.status_code != 200:
            self._raise_http(resp, f"GET {path}")
        return resp.json()

    def _post(self, path: str, *, json_body: dict) -> Any:
        resp = self._client.post(
            f"{CALENDAR_API_BASE}{path}", headers=self._headers(), json=json_body
        )
        if resp.status_code not in (200, 201):
            self._raise_http(resp, f"POST {path}")
        return resp.json() if resp.text else {}

    def _patch(self, path: str, *, json_body: dict) -> Any:
        resp = self._client.patch(
            f"{CALENDAR_API_BASE}{path}", headers=self._headers(), json=json_body
        )
        if resp.status_code != 200:
            self._raise_http(resp, f"PATCH {path}")
        return resp.json()

    # -- Read APIs ----------------------------------------------------------

    def list_calendars(self) -> List[Dict[str, Any]]:
        data = self._get("/users/me/calendarList")
        return data.get("items", [])

    def list_events(
        self,
        *,
        calendar_id: str = "primary",
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 25,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "maxResults": max_results,
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        return self._get(
            f"/calendars/{calendar_id}/events",
            params=params,
        )

    def get_event(
        self, *, calendar_id: str = "primary", event_id: str
    ) -> Dict[str, Any]:
        return self._get(f"/calendars/{calendar_id}/events/{event_id}")

    # -- Mutate APIs --------------------------------------------------------

    def update_event_rsvp(
        self,
        *,
        calendar_id: str = "primary",
        event_id: str,
        attendee_email: str,
        response_status: str,
    ) -> Dict[str, Any]:
        # Calendar API expects the full attendees array on PATCH; we fetch
        # the current event, update the matching attendee, and PATCH back.
        event = self.get_event(calendar_id=calendar_id, event_id=event_id)
        attendees = list(event.get("attendees") or [])
        if not attendees:
            # No attendees array — synthesize one with just the user.
            attendees = [
                {
                    "email": attendee_email,
                    "responseStatus": response_status,
                    "self": True,
                }
            ]
        else:
            updated = False
            for a in attendees:
                if (a.get("email") or "").lower() == attendee_email.lower():
                    a["responseStatus"] = response_status
                    updated = True
                    break
            if not updated:
                attendees.append(
                    {
                        "email": attendee_email,
                        "responseStatus": response_status,
                        "self": True,
                    }
                )
        return self._patch(
            f"/calendars/{calendar_id}/events/{event_id}",
            json_body={"attendees": attendees},
        )

    def create_event(
        self,
        *,
        calendar_id: str = "primary",
        summary: str,
        start: Dict[str, str],
        end: Dict[str, str],
        attendees: Optional[Iterable[str]] = None,
        location: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"summary": summary, "start": start, "end": end}
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
        if location:
            body["location"] = location
        if description:
            body["description"] = description
        return self._post(
            f"/calendars/{calendar_id}/events",
            json_body=body,
        )


# ---------------------------------------------------------------------------
# Module-level token resolver
# ---------------------------------------------------------------------------


def _get_calendar_token() -> str:
    """Return a Calendar access token via the standard grant-checked path."""
    cred = get_credential_sync(
        "google",
        agent_id=AGENT_NAMESPACED_ID,
        required_scopes=list(CALENDAR_SCOPES),
    )
    return cred["access_token"]


__all__ = [
    "CALENDAR_API_BASE",
    "CalendarBackend",
    "LiveCalendarBackend",
    "_get_calendar_token",
]
