# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Thin-client driver for plain JSON agent-control routes (#2516).

Companion to ``agent_query.py`` (which drives the streaming ``/query``
route): this covers the non-streaming, request/response routes relayed
through the daemon — today, the email agent's session-scoped autonomy
control surface (``/v1/email/agent/session`` and
``/v1/email/agent/autonomy*``).

Same authenticated path as every other ``gaia email`` command: ensure the
daemon + sidecar, then present ONLY the daemon client token to the relay
(``ANY /v1/<agent>/*``); the daemon swaps it for the sidecar's own bearer
server-side, so the CLI never holds — or invents a way to hold — sidecar
credentials (design #2150/#2152, mirrored from ``agent_query.run_query``).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from gaia.daemon import client, paths
from gaia.daemon.constants import AUTH_SCHEME
from gaia.daemon.errors import DaemonError
from gaia.daemon.timeouts import agent_read_timeout
from gaia.logger import get_logger

logger = get_logger(__name__)

_CONNECT_TIMEOUT = 10.0


# The CLI waits a little past the daemon's own budget so the daemon's verdict
# on a slow sidecar arrives first.
_READ_GRACE = 30.0


class AgentResponseTimeoutError(DaemonError):
    """The agent accepted the request but did not answer within the read
    budget. The work may still be running on the agent."""


class AgentHTTPError(DaemonError):
    """The relay answered with an HTTP error status."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def may_still_be_running(exc: Exception) -> bool:
    """True when *exc* leaves open that the agent is still doing the work:
    the read timed out, or the relay itself failed (5xx) after accepting it."""
    if isinstance(exc, AgentResponseTimeoutError):
        return True
    return isinstance(exc, AgentHTTPError) and exc.status_code >= 500


def relay_json(
    agent_id: str,
    method: str,
    path: str,
    *,
    json_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Ensure the daemon + *agent_id* sidecar, then relay one JSON request.

    Presents ONLY the daemon client token — the relay swaps in the sidecar
    bearer server-side (never invented or held here). Raises
    :class:`~gaia.daemon.errors.DaemonError` on any non-2xx response or
    transport failure — a caller must never mistake a refusal for an empty
    success.
    """
    import requests

    inst = client.ensure_agent(agent_id)
    url = f"{inst.base_url}/v1/{agent_id}/{path.lstrip('/')}"
    headers = {"Authorization": f"{AUTH_SCHEME} {inst.token}"}
    # The daemon relays to the sidecar on this same budget;
    # GAIA_AGENT_TOOL_TIMEOUT raises both.
    try:
        read_timeout = agent_read_timeout() + _READ_GRACE
    except ValueError as e:
        raise DaemonError(str(e)) from e
    try:
        r = requests.request(
            method,
            url,
            headers=headers,
            json=json_body,
            timeout=(_CONNECT_TIMEOUT, read_timeout),
        )
    except requests.exceptions.ReadTimeout as e:
        raise AgentResponseTimeoutError(
            f"the '{agent_id}' agent did not answer {method} {path} within "
            f"{read_timeout:g}s; it may still be working. Set "
            "GAIA_AGENT_TOOL_TIMEOUT (seconds) for both the daemon and this "
            "shell to wait longer."
        ) from e
    except requests.exceptions.RequestException as e:
        raise DaemonError(
            f"could not reach the '{agent_id}' agent through the daemon at "
            f"{inst.base_url}: {e}. Check `gaia daemon status` and the daemon "
            f"log at {paths.log_path()}."
        ) from e
    if r.status_code >= 400:
        raise AgentHTTPError(
            f"the '{agent_id}' agent refused {method} {path} "
            f"(HTTP {r.status_code}): {client._error_detail(r)}",
            r.status_code,
        )
    try:
        return r.json()
    except ValueError as e:
        raise DaemonError(
            f"the '{agent_id}' agent returned a non-JSON response for "
            f"{method} {path}: {e}"
        ) from e


__all__ = [
    "AgentHTTPError",
    "AgentResponseTimeoutError",
    "may_still_be_running",
    "relay_json",
]
