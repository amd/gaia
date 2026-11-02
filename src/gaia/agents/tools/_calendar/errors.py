# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Calendar errors shared by every provider backend."""

from __future__ import annotations


class CalendarError(RuntimeError):
    """A calendar request failed in a way the caller should surface verbatim."""


class CalendarAuthError(CalendarError):
    """The calendar rejected our credentials; triggers one backend re-resolve."""


class CalendarEventGone(CalendarError):
    """The event no longer exists (404/410, or Google's ``cancelled`` status)."""


__all__ = ["CalendarAuthError", "CalendarError", "CalendarEventGone"]
