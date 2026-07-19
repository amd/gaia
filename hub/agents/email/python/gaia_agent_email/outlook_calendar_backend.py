# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Microsoft Graph calendar backend — ``LiveOutlookCalendarBackend`` (#1276).

Connects the Email Triage Agent to a personal Outlook.com / Hotmail / Live
calendar via the Microsoft OAuth provider (#1105), alongside the shipped Google
calendar connector.

Architecture seam (mirrors ``outlook_backend.py`` for the calendar surface):
this backend satisfies the SAME ``CalendarBackend`` Protocol as
``LiveCalendarBackend`` by **translating Microsoft Graph ``event`` JSON into the
Google Calendar v3 envelope** on read, and Google's calendar verbs
(``update_event_rsvp``, ``create_event``) into MS Graph mutations on write. The
email agent's calendar tools call methods on a ``CalendarBackend`` and consume
Google-shaped dicts — so they operate on Outlook and Google calendars
interchangeably, without a single tool change.

Translation summary (Graph -> Google shape):

- ``event`` -> ``{id, summary(=subject), start, end, location(=location.
  displayName), organizer:{email}, htmlLink(=webLink)}``.
- Graph ``start``/``end`` are ``dateTimeTimeZone`` objects
  (``{dateTime, timeZone}``). Google uses ``{dateTime}`` for timed events and
  ``{date}`` for all-day. ``isAllDay==true`` -> ``{date: <YYYY-MM-DD>}``; else
  ``{dateTime: <graph dateTime>}`` (the tools read the value verbatim for
  display, so the wall-clock string passes through unchanged).
- A list response is wrapped as ``{"items": [...]}`` so the calendar tool's
  ``data.get("items", [])`` reads it exactly as a Google response.

Calendar verbs (Google -> Graph):

- ``list_events(time_min, time_max)`` -> ``GET /me/calendarView`` when BOTH
  bounds are present (Graph expands recurring series into instances within the
  window, the analogue of Google's ``singleEvents=true``); otherwise ``GET
  /me/events`` ordered by start.
- ``get_event`` -> ``GET /me/events/{id}``.
- ``create_event`` -> ``POST /me/events`` (subject/start/end/location/body/
  attendees). Google passes ``start={"dateTime": iso}``; Graph requires a paired
  ``timeZone`` so ``UTC`` is attached when the caller did not supply one.
- ``update_event_rsvp`` -> Graph has no PATCH-attendees RSVP for the invitee;
  it uses action endpoints ``POST /me/events/{id}/accept`` / ``decline`` /
  ``tentativelyAccept``. An unsupported status (e.g. ``needsAction``, which has
  no Graph analogue) raises an actionable ``ConnectorsError`` — NEVER a silent
  no-op that would leave the user thinking they RSVP'd.

Token lifecycle, error hygiene, and the no-silent-fallback contract mirror
``outlook_backend.py`` exactly:

- ``access_token_fn`` is invoked on EVERY request (the connectors token cache
  makes this cheap) so a mid-call revoke surfaces as a 401, not a stale token.
- Every non-2xx raises ``ConnectorsError`` built from ``status_code`` +
  truncated body ONLY — never from a wrapper exception that could leak the
  ``Authorization: Bearer ...`` header. An empty/no-access result is NEVER
  swallowed into an empty list.
"""

from __future__ import annotations

from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
)

import httpx

from gaia_agent_email.outlook_scopes import OUTLOOK_CALENDAR_SCOPES
from gaia_agent_email.scopes import AGENT_NAMESPACED_ID

from gaia.connectors.errors import ConnectorsError
from gaia.connectors.handler import get_credential_sync
from gaia.logger import get_logger

log = get_logger(__name__)


GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Graph default time zone for created-event start/end when the caller (the
# Google-shaped tool) supplies a bare ``dateTime`` without a paired timeZone.
_DEFAULT_TIME_ZONE = "UTC"

# Google RSVP status -> Graph event action endpoint. ``needsAction`` has no
# Graph analogue and is intentionally absent so it raises (see update_event_rsvp).
_RSVP_ACTION = {
    "accepted": "accept",
    "declined": "decline",
    "tentative": "tentativelyAccept",
}


# ---------------------------------------------------------------------------
# Graph event -> Google Calendar v3 shape translation
# ---------------------------------------------------------------------------


def _translate_endpoint(
    graph_dttz: Optional[Dict[str, Any]], *, all_day: bool
) -> Dict[str, Any]:
    """Translate a Graph ``dateTimeTimeZone`` into a Google start/end object.

    All-day events use Google's ``date`` (``YYYY-MM-DD``); timed events use
    ``dateTime`` (the Graph wall-clock string, passed through verbatim — the
    tools render it for display, not for arithmetic).
    """
    dt = (graph_dttz or {}).get("dateTime") or ""
    if all_day:
        # Graph all-day start/end are midnight-anchored ISO strings; the Google
        # ``date`` field wants just the calendar date.
        return {"date": dt[:10]} if dt else {}
    return {"dateTime": dt} if dt else {}


def graph_event_to_google(event: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a Microsoft Graph ``event`` resource into a Google Calendar v3
    ``events.get`` / ``events.list`` item shape.

    Only the fields the email agent's calendar tools read are reconstructed
    (``id``/``summary``/``start``/``end``/``location``/``organizer.email``);
    ``htmlLink`` is carried for informational parity with Google.
    """
    all_day = bool(event.get("isAllDay"))
    organizer_addr = ((event.get("organizer") or {}).get("emailAddress") or {}).get(
        "address"
    ) or ""
    organizer = {"email": organizer_addr} if organizer_addr else {}
    location = (event.get("location") or {}).get("displayName") or None
    return {
        "id": event.get("id"),
        "summary": event.get("subject") or "",
        "start": _translate_endpoint(event.get("start"), all_day=all_day),
        "end": _translate_endpoint(event.get("end"), all_day=all_day),
        "location": location,
        "organizer": organizer,
        "htmlLink": event.get("webLink") or "",
    }


def _recipients(addresses: Iterable[str]) -> List[Dict[str, Any]]:
    """Build a Graph attendee array from an iterable of email addresses."""
    out: List[Dict[str, Any]] = []
    for addr in addresses:
        addr = (addr or "").strip()
        if addr:
            out.append({"emailAddress": {"address": addr}, "type": "required"})
    return out


def _graph_endpoint(google_endpoint: Dict[str, str]) -> Dict[str, str]:
    """Translate a Google start/end (``{"dateTime": iso}``) into a Graph
    ``dateTimeTimeZone``. A bare ``dateTime`` gets ``UTC`` so Graph accepts it
    (Graph rejects a ``dateTime`` without a paired ``timeZone``)."""
    dt = google_endpoint.get("dateTime") or google_endpoint.get("date") or ""
    tz = google_endpoint.get("timeZone") or _DEFAULT_TIME_ZONE
    return {"dateTime": dt, "timeZone": tz}


# ---------------------------------------------------------------------------
# LiveOutlookCalendarBackend
# ---------------------------------------------------------------------------


class LiveOutlookCalendarBackend:
    """Concrete ``CalendarBackend`` that hits Microsoft Graph for a personal
    Outlook.com calendar.

    Satisfies the ``CalendarBackend`` structural Protocol so the email agent's
    calendar tools use it interchangeably with ``LiveCalendarBackend``.
    """

    def __init__(
        self,
        access_token_fn: Callable[[], str],
        *,
        http_client: Optional[httpx.Client] = None,
        timeout_seconds: float = 15.0,
    ):
        self._access_token_fn = access_token_fn
        # Allow tests to inject an ``httpx.MockTransport``-backed client without
        # touching the network.
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    # -- HTTP helpers -------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        # Re-fetch on every request — cheap via the connectors token cache, but
        # mandatory so a mid-call revoke surfaces as 401 (AUTH_REQUIRED), not a
        # stale-token success.
        token = self._access_token_fn()
        return {"Authorization": f"Bearer {token}"}

    def _raise_http(self, response: httpx.Response, where: str) -> None:
        # Build the error from status + truncated body ONLY. NEVER from a
        # wrapper exception, which would expose the Authorization header.
        if response.status_code == 401:
            raise ConnectorsError(
                "Microsoft Graph returned 401. The Outlook access token may "
                "have expired or been revoked. Reconnect Microsoft in "
                f"Settings → Connectors. (where: {where})"
            )
        if response.status_code == 403:
            raise ConnectorsError(
                "Microsoft Graph returned 403 (insufficient permissions). The "
                "connected Microsoft account did not grant the calendar scope "
                "this agent needs (Calendars.ReadWrite). Reconnect Microsoft in "
                "Settings → Connectors and approve calendar access. "
                f"(where: {where}; detail: {response.text[:300]})"
            )
        raise ConnectorsError(
            f"Microsoft Graph {where} returned {response.status_code}: "
            f"{response.text[:300]}"
        )

    def _get(self, path: str, *, params: Optional[dict] = None) -> Any:
        resp = self._client.get(
            f"{GRAPH_API_BASE}{path}", headers=self._headers(), params=params
        )
        if resp.status_code != 200:
            self._raise_http(resp, f"GET {path}")
        return resp.json()

    def _post(self, path: str, *, json_body: Optional[dict] = None) -> Any:
        resp = self._client.post(
            f"{GRAPH_API_BASE}{path}", headers=self._headers(), json=json_body
        )
        # Graph returns 200/201 (create) or 202/204 (RSVP actions, no content).
        if resp.status_code not in (200, 201, 202, 204):
            self._raise_http(resp, f"POST {path}")
        return resp.json() if resp.text else {}

    # -- Read APIs ----------------------------------------------------------

    def list_calendars(self) -> List[Dict[str, Any]]:
        data = self._get("/me/calendars")
        return data.get("value", [])

    def list_events(  # pylint: disable=unused-argument
        self,
        *,
        calendar_id: str = "primary",
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 25,
    ) -> Dict[str, Any]:
        # ``calendar_id`` is implicit in Graph's ``/me`` endpoints — kept in the
        # signature for Protocol parity with the Google backend.
        # pylint: disable=unused-argument
        # When BOTH bounds are present, use calendarView — it expands recurring
        # series into instances within the window (the analogue of Google's
        # ``singleEvents=true``). Otherwise list raw events ordered by start.
        if time_min and time_max:
            params: Dict[str, Any] = {
                "startDateTime": time_min,
                "endDateTime": time_max,
                "$top": max_results,
                "$orderby": "start/dateTime",
            }
            data = self._get("/me/calendarView", params=params)
        else:
            params = {"$top": max_results, "$orderby": "start/dateTime"}
            data = self._get("/me/events", params=params)
        items = [graph_event_to_google(e) for e in data.get("value", [])]
        # Wrap in the Google ``items`` envelope so the calendar tool reads it
        # exactly as a Google list response.
        return {"items": items}

    def get_event(  # pylint: disable=unused-argument
        self, *, calendar_id: str = "primary", event_id: str
    ) -> Dict[str, Any]:
        # ``calendar_id`` is implicit in Graph's ``/me`` endpoints — kept in the
        # signature for Protocol parity with the Google backend.
        # pylint: disable=unused-argument
        data = self._get(f"/me/events/{event_id}")
        return graph_event_to_google(data)

    # -- Mutate APIs --------------------------------------------------------

    def update_event_rsvp(  # pylint: disable=unused-argument
        self,
        *,
        calendar_id: str = "primary",
        event_id: str,
        attendee_email: str,
        response_status: str,
    ) -> Dict[str, Any]:
        # Graph RSVP is an action on the invitee's own copy of the event
        # (the authenticated ``/me``), so ``attendee_email`` and ``calendar_id``
        # are unused here — kept in the signature for Protocol parity with the
        # Google backend.
        # pylint: disable=unused-argument
        action = _RSVP_ACTION.get((response_status or "").strip().lower())
        if action is None:
            raise ConnectorsError(
                f"Outlook calendar RSVP status {response_status!r} is not "
                "supported by Microsoft Graph. Use 'accepted', 'declined', or "
                "'tentative'."
            )
        self._post(f"/me/events/{event_id}/{action}", json_body={"sendResponse": True})
        return {"id": event_id, "responseStatus": response_status}

    def create_event(  # pylint: disable=unused-argument
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
        # ``calendar_id`` is implicit in Graph's ``/me`` endpoints — kept in the
        # signature for Protocol parity with the Google backend.
        # pylint: disable=unused-argument
        body: Dict[str, Any] = {
            "subject": summary,
            "start": _graph_endpoint(start),
            "end": _graph_endpoint(end),
        }
        if attendees:
            recipients = _recipients(attendees)
            if recipients:
                body["attendees"] = recipients
        if location:
            body["location"] = {"displayName": location}
        if description:
            body["body"] = {"contentType": "text", "content": description}
        return self._post("/me/events", json_body=body)


# ---------------------------------------------------------------------------
# Module-level token resolver
# ---------------------------------------------------------------------------


def _get_outlook_calendar_token() -> str:
    """Return an MS Graph access token for the calendar scope via the
    grant-checked connector path.

    Uses the ``microsoft`` connector + ``oauth_pkce`` handler seam from #1105:
    ``get_credential(spec, required_scopes=[...])`` -> ``{"access_token": ...}``.
    The grant dispatcher raises ``AuthRequiredError`` (no grant / missing
    scopes) BEFORE any network round-trip; we let it propagate so the agent can
    prompt the user — never swallowed into an empty token / empty calendar.

    Module-level (not a method) so it mirrors ``_get_outlook_token`` /
    ``_get_calendar_token`` and can be unit-tested without instantiating the
    agent. In the daemon deployment (#2154) it returns the daemon-forwarded
    'microsoft' token instead of reading the keyring.
    """
    from gaia_agent_email import forwarded_credentials

    def _live() -> str:
        cred = get_credential_sync(
            "microsoft",
            agent_id=AGENT_NAMESPACED_ID,
            required_scopes=list(OUTLOOK_CALENDAR_SCOPES),
        )
        return cred["access_token"]

    return forwarded_credentials.resolve_access_token(
        "microsoft", list(OUTLOOK_CALENDAR_SCOPES), live_fetch=_live
    )


__all__ = [
    "GRAPH_API_BASE",
    "LiveOutlookCalendarBackend",
    "_get_outlook_calendar_token",
    "graph_event_to_google",
]
