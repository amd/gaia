# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Calendar tools — list events, RSVP to invites, create events from emails.

``accept_invite``, ``decline_invite``, ``create_event_from_email`` are
registered in ``TOOLS_REQUIRING_CONFIRMATION`` at the agent level —
calendar mutations are externally visible to other attendees.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from gaia.agents.base.tools import tool
from gaia.agents.email.verbose import log_tool_call
from gaia.connectors.errors import ConnectorsError
from gaia.logger import get_logger

log = get_logger(__name__)


def _envelope_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, default=str)


def _envelope_err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def list_calendar_events_impl(
    cal, *, time_min: Optional[str], time_max: Optional[str], debug: bool = False
) -> Dict[str, Any]:
    with log_tool_call(
        "list_calendar_events",
        {"time_min": time_min, "time_max": time_max},
        debug=debug,
    ) as st:
        data = cal.list_events(time_min=time_min, time_max=time_max)
        events = []
        for e in data.get("items", []):
            organizer = (e.get("organizer") or {}).get("email")
            events.append(
                {
                    "id": e.get("id"),
                    "summary": e.get("summary", ""),
                    "start": (e.get("start") or {}).get("dateTime")
                    or (e.get("start") or {}).get("date"),
                    "end": (e.get("end") or {}).get("dateTime")
                    or (e.get("end") or {}).get("date"),
                    "location": e.get("location"),
                    "organizer": organizer,
                    "missing_organizer": organizer is None,
                }
            )
        st["result_summary"] = {"count": len(events)}
        return {"events": events}


def update_rsvp_impl(
    cal,
    *,
    event_id: str,
    user_email: str,
    status: str,
    debug: bool = False,
) -> Dict[str, Any]:
    """Generic RSVP update used by accept/decline."""
    with log_tool_call(
        "update_rsvp",
        {"event_id": event_id, "status": status},
        debug=debug,
    ) as st:
        cal.update_event_rsvp(
            event_id=event_id,
            attendee_email=user_email,
            response_status=status,
        )
        st["result_summary"] = {"event_id": event_id, "status": status}
        return {"event_id": event_id, "status": status}


def create_event_from_email_impl(
    cal,
    *,
    summary: str,
    start: Dict[str, str],
    end: Dict[str, str],
    attendees: Optional[list] = None,
    location: Optional[str] = None,
    description: Optional[str] = None,
    debug: bool = False,
) -> Dict[str, Any]:
    with log_tool_call(
        "create_event_from_email",
        {"summary": summary, "start": start, "end": end},
        debug=debug,
    ) as st:
        ev = cal.create_event(
            summary=summary,
            start=start,
            end=end,
            attendees=attendees,
            location=location,
            description=description,
        )
        st["result_summary"] = {"event_id": ev.get("id")}
        return {"event_id": ev.get("id"), "summary": summary}


class CalendarToolsMixin:
    def _register_calendar_tools(self) -> None:
        cal = self._calendar
        # The user's email is needed for RSVP — fetched from the Gmail
        # backend (cheap; cached by Lemonade behind the scenes).
        gmail = self._gmail
        debug_flag = bool(getattr(self.config, "debug", False))

        @tool
        def list_calendar_events(
            time_min: Optional[str] = None, time_max: Optional[str] = None
        ) -> str:
            """List calendar events between two RFC 3339 timestamps."""
            try:
                return _envelope_ok(
                    list_calendar_events_impl(
                        cal, time_min=time_min, time_max=time_max, debug=debug_flag
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def accept_invite(event_id: str) -> str:
            """RSVP yes to a calendar event. Requires user confirmation."""
            try:
                user = gmail.get_user_email()
                return _envelope_ok(
                    update_rsvp_impl(
                        cal,
                        event_id=event_id,
                        user_email=user,
                        status="accepted",
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def decline_invite(event_id: str) -> str:
            """RSVP no to a calendar event. Requires user confirmation."""
            try:
                user = gmail.get_user_email()
                return _envelope_ok(
                    update_rsvp_impl(
                        cal,
                        event_id=event_id,
                        user_email=user,
                        status="declined",
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")

        @tool
        def create_event_from_email(
            summary: str,
            start_iso: str,
            end_iso: str,
            attendees: str = "",
            location: str = "",
            description: str = "",
        ) -> str:
            """Create a calendar event derived from an email's content.

            Requires user confirmation. ``attendees`` is a comma-separated
            list of email addresses.
            """
            try:
                attendee_list = (
                    [a.strip() for a in attendees.split(",") if a.strip()]
                    if attendees
                    else None
                )
                return _envelope_ok(
                    create_event_from_email_impl(
                        cal,
                        summary=summary,
                        start={"dateTime": start_iso},
                        end={"dateTime": end_iso},
                        attendees=attendee_list,
                        location=location or None,
                        description=description or None,
                        debug=debug_flag,
                    )
                )
            except ConnectorsError as exc:
                return _envelope_err(str(exc))
            except Exception as exc:
                log.exception("email tool error: %s", type(exc).__name__)
                return _envelope_err(f"{type(exc).__name__}: {exc}")
