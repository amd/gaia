# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Raise the "waiting for your approval" phone alert when a card sits unanswered.

The UI server is where permission cards live, so it tells the daemon's phone
service when one has waited longer than the user's chosen time, and clears the
alert once it is answered. See docs/plans/phone-calls.mdx.
"""

from __future__ import annotations

import threading
from typing import Callable, Dict, Optional

from gaia.logger import get_logger

log = get_logger(__name__)


class ApprovalWatch:
    """One watch per pending card, keyed by a string that names it."""

    def __init__(
        self,
        *,
        wait_minutes: Optional[Callable[[], Optional[int]]] = None,
        raise_alert: Optional[Callable[[str], None]] = None,
        clear: Optional[Callable[[str], None]] = None,
    ):
        self._wait_minutes = wait_minutes or _rule_wait_minutes
        self._raise = raise_alert or _raise
        self._clear = clear or _clear
        self._lock = threading.Lock()
        self._pending: Dict[str, threading.Event] = {}
        self._raised: set = set()

    def shown(self, key: str) -> None:
        with self._lock:
            if key in self._pending:
                return
            answered = self._pending[key] = threading.Event()
        threading.Thread(
            target=self._watch, args=(key, answered), name="approval-alert", daemon=True
        ).start()

    def answered(self, key: str) -> None:
        with self._lock:
            event = self._pending.pop(key, None)
            raised = key in self._raised
            self._raised.discard(key)
        if event is not None:
            event.set()
        if raised:
            self._clear(key)

    def _watch(self, key: str, answered: threading.Event) -> None:
        minutes = self._wait_minutes()
        if minutes is None:
            return
        if answered.wait(minutes * 60):
            return
        with self._lock:
            if self._pending.get(key) is not answered:
                return
            self._raised.add(key)
        self._raise(key)


def _rule_wait_minutes() -> Optional[int]:
    """The approval rule's wait, or None when phone alerts can't use it."""
    from gaia.phone.client import get_settings

    settings = get_settings()
    if not settings or not settings.get("enabled"):
        return None
    rule = settings.get("rules", {}).get("approval_waiting", {})
    return int(rule.get("after_minutes", 10)) if rule.get("on") else None


def _raise(key: str) -> None:
    from gaia.phone.client import raise_alert

    raise_alert("approval", reason="A task is waiting for your approval.", rule_key=key)


def _clear(key: str) -> None:
    from gaia.phone.client import clear_rule

    clear_rule(key)


WATCH = ApprovalWatch()
