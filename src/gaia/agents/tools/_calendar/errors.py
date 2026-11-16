# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Calendar errors shared by every provider backend."""

from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional, Tuple


class CalendarError(RuntimeError):
    """A calendar request failed in a way the caller should surface verbatim.

    ``connect`` names the connectors that signing in fixes, and ``reconnect``
    those of them that are connected already. ``account`` maps a connector to
    the one account that failed (its stable id), so a sign-in offers that one.
    ``setup`` marks a call stopped by how accounts are set up right now, before
    it reached the calendar.
    """

    def __init__(
        self,
        *args,
        connect: Iterable[str] = (),
        reconnect: Iterable[str] = (),
        setup: bool = False,
        account: Optional[Mapping[str, str]] = None,
    ):
        super().__init__(*args)
        self.connect: Tuple[str, ...] = tuple(connect)
        self.reconnect: Tuple[str, ...] = tuple(reconnect)
        self.setup = setup
        self.account: Dict[str, str] = dict(account or {})


class CalendarAuthError(CalendarError):
    """The calendar rejected our credentials; triggers one backend re-resolve."""


class CalendarEventGone(CalendarError):
    """The event was deleted (410, or Google's ``cancelled`` status)."""


class CalendarEventNotFound(CalendarError):
    """404: the event is gone or the id is wrong, and the provider can't say which."""


class CalendarWriteUnreadable(CalendarError):
    """A write returned 2xx and its reply couldn't be read. The write did happen."""


__all__ = [
    "CalendarAuthError",
    "CalendarError",
    "CalendarEventGone",
    "CalendarEventNotFound",
    "CalendarWriteUnreadable",
]
