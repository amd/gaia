# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""WaitToolsMixin — lets an agent pause, e.g. until a rate limit resets.

Pure Python, so it behaves the same on Windows, which has no ``sleep`` command,
and it needs no shell access or approval.
"""

import math
import time
from datetime import datetime
from typing import Any, Dict

from gaia.logger import get_logger
from gaia.tool_cancellation import tool_cancelled

logger = get_logger(__name__)

#: Longest single wait. A longer one takes several calls, each ending in a
#: result the user sees.
MAX_SLEEP_SECONDS = 300.0

# Upper bound on how late a wait notices Stop.
_STOP_POLL_SECONDS = 0.5


class WaitToolsMixin:
    """
    Mixin providing a wait tool.

    Tools provided:
    - sleep: Wait up to MAX_SLEEP_SECONDS, ending early when the turn is stopped

    Requires an ``Agent`` host: the wait listens for stop on ``_cancel_event``
    and ``_console_cancelled()``, which ``Agent`` owns. ``KNOWN_TOOLS``
    advertises this mixin to any host, so a host missing either channel is
    refused by name before a single second is waited.
    """

    #: Host attributes the wait needs to notice a Stop.
    _WAIT_HOST_ATTRS = ("_cancel_event", "_console_cancelled")

    def register_wait_tools(self) -> None:
        """Register the wait tool into _TOOL_REGISTRY."""
        from gaia.agents.base.tools import tool

        # Above the cap: the default 180 s tool timeout must not cut a wait short.
        @tool(timeout=MAX_SLEEP_SECONDS + 30, display_label="Waiting")
        def sleep(seconds: float, reason: str = "") -> Dict[str, Any]:
            """Wait before continuing: until a rate limit resets, or because the user asked you to wait.

            Args:
                seconds: How long to wait, more than 0 and at most 300. To wait longer, call sleep again.
                reason: What you are waiting for.
            """
            return self._sleep(seconds, reason)

    def _sleep(self, seconds: float, reason: str) -> Dict[str, Any]:
        """Wait in short naps so a Stop is honoured within one of them.

        Raises:
            TypeError: The host lacks a stop channel, so the wait could not be
                interrupted. Raised before waiting, never part-way through.
        """
        self._require_stop_channels()
        if not math.isfinite(seconds) or seconds <= 0:
            return {
                "status": "error",
                "error": (
                    f"sleep needs seconds greater than 0 and at most "
                    f"{MAX_SLEEP_SECONDS:g}; got {seconds!r}, so nothing was "
                    "waited. If what you were waiting for is already due, "
                    "continue without sleeping."
                ),
            }
        if seconds > MAX_SLEEP_SECONDS:
            return {
                "status": "error",
                "error": (
                    f"sleep waits at most {MAX_SLEEP_SECONDS:g} seconds per call; "
                    f"got {seconds:g}, so nothing was waited. Call "
                    f"sleep({MAX_SLEEP_SECONDS:g}), then call it again for the "
                    f"remaining {seconds - MAX_SLEEP_SECONDS:g} seconds."
                ),
            }

        logger.info("Waiting %gs: %s", seconds, reason or "(no reason given)")
        start = time.monotonic()
        deadline = start + seconds
        stopped = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self._wait_interrupted():
                stopped = True
                break
            time.sleep(min(_STOP_POLL_SECONDS, remaining))

        slept = round(time.monotonic() - start, 2)
        finished_at = datetime.now().astimezone().isoformat(timespec="seconds")
        if stopped:
            logger.info("Wait stopped after %gs of %gs", slept, seconds)
            return {
                "status": "cancelled",
                "slept_seconds": slept,
                "requested_seconds": seconds,
                "reason": reason,
                "finished_at": finished_at,
                "message": (
                    f"Stopped after {slept:g} of {seconds:g} seconds because a "
                    "stop was requested."
                ),
            }
        return {
            "status": "success",
            "slept_seconds": slept,
            "reason": reason,
            "finished_at": finished_at,
        }

    def _require_stop_channels(self) -> None:
        """Refuse to start a wait nothing could interrupt.

        Registration stays permissive because the registry is also built by
        skeletons that only count tools and never call one.
        """
        missing = [a for a in self._WAIT_HOST_ATTRS if not hasattr(self, a)]
        if missing:
            raise TypeError(
                f"{type(self).__name__} cannot run the sleep tool: it is "
                f"missing {', '.join(missing)}, so a Stop could not end the "
                "wait and nothing was waited. Inherit from "
                "gaia.agents.base.agent.Agent (which owns both) and list Agent "
                "before WaitToolsMixin in the bases."
            )

    def _wait_interrupted(self) -> bool:
        """True once the turn this wait belongs to has been asked to stop.

        One signal per way a turn is stopped: the Agent UI's Stop and the TUI's
        cancel set ``console.cancelled``; the flagship's ``/cancel`` and the
        UI's stream teardown set ``_cancel_event``; ``tool_cancelled()`` fires
        once the agent has given up on this tool call.

        Both host attributes are guaranteed present — ``_sleep`` refuses a host
        that lacks either before it waits.
        """
        cancel_event = self._cancel_event
        return (
            self._console_cancelled()
            or (cancel_event is not None and cancel_event.is_set())
            or tool_cancelled()
        )
