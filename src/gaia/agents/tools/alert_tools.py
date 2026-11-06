# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""AlertToolsMixin — lets an agent ask GAIA to reach the user by phone.

The tool only raises an alert in the daemon; the user's own rules decide
whether that becomes a text or a call, and what is said is a fixed sentence,
never the agent's words. Registered only when the agent runs under the daemon
and the user has turned on "Agents may alert me".
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from gaia.logger import get_logger

logger = get_logger(__name__)

_URL_ENV = "GAIA_HOST_CUSTODY_URL"
_SECRET_ENV = "GAIA_HOST_CUSTODY_SECRET"
_TIMEOUT = 10.0
#: Asked while the agent is built, so a hung daemon must not hold that up.
_CHECK_TIMEOUT = 3.0


def _custody() -> Optional[tuple]:
    url, secret = os.environ.get(_URL_ENV), os.environ.get(_SECRET_ENV)
    return (url.rstrip("/"), secret) if url and secret else None


def agent_alerts_allowed() -> bool:
    """Whether this agent may raise alerts now: under the daemon, setting on."""
    custody = _custody()
    if custody is None:
        return False
    import httpx

    url, secret = custody
    try:
        response = httpx.get(
            f"{url}/host/v1/alerts",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=_CHECK_TIMEOUT,
        )
        body = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError) as e:
        logger.info("phone alerts unavailable: %s", e)
        return False
    return isinstance(body, dict) and body.get("allowed") is True


class AlertToolsMixin:
    """Provides ``alert_owner``."""

    def register_alert_tools(self) -> None:
        if not agent_alerts_allowed():
            return
        from gaia.agents.base.tools import tool

        @tool(display_label="Alerting you")
        def alert_owner(reason: str) -> Dict[str, Any]:
            """Ask GAIA to reach the user by text or phone call because something needs them now.

            Use only for something urgent the user must act on soon, such as a task that cannot continue without them. The user hears a fixed sentence, not your words; your reason is shown in GAIA's apps.

            Args:
                reason: One sentence on what needs the user's attention.
            """
            return self._alert_owner(reason)

    def _alert_owner(self, reason: str) -> Dict[str, Any]:
        import httpx

        custody = _custody()
        if custody is None:
            return {
                "status": "error",
                "error": "GAIA's daemon isn't reachable, so the user wasn't alerted.",
            }
        url, secret = custody
        try:
            response = httpx.post(
                f"{url}/host/v1/alerts",
                json={"reason": str(reason or "")[:500]},
                headers={"Authorization": f"Bearer {secret}"},
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as e:
            return {"status": "error", "error": f"Couldn't reach GAIA's daemon: {e}"}
        if response.status_code >= 400:
            return {
                "status": "error",
                "error": f"GAIA's daemon refused the alert ({response.status_code}).",
            }
        body = response.json()
        if not body.get("raised"):
            return {"status": "not_sent", "message": body.get("message", "")}
        return {
            "status": "success",
            "message": "The user will be alerted following their phone settings.",
        }
