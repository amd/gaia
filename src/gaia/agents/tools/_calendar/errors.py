# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Calendar errors shared by every provider backend."""

from __future__ import annotations

from typing import Iterable, Tuple


class CalendarError(RuntimeError):
    """A calendar request failed in a way the caller should surface verbatim.

    ``connect`` names the connectors that signing in fixes, and ``reconnect``
    those of them that are connected already.
    """

    def __init__(
        self, *args, connect: Iterable[str] = (), reconnect: Iterable[str] = ()
    ):
        super().__init__(*args)
        self.connect: Tuple[str, ...] = tuple(connect)
        self.reconnect: Tuple[str, ...] = tuple(reconnect)


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
