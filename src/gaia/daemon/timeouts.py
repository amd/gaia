# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Timeouts shared by the daemon relay and its thin clients."""

from __future__ import annotations

import os

# A single agent-loop step can be quiet while the sidecar is classifying a
# mailbox message. Keep the shipping idle-read budget unless the operator has
# explicitly raised the existing agent timeout setting.
DEFAULT_AGENT_READ_TIMEOUT = 300.0
_TIMEOUT_ENV_VAR = "GAIA_AGENT_TOOL_TIMEOUT"


def agent_read_timeout() -> float:
    """Return the SSE idle-read timeout for a relayed agent query.

    The relay and thin client both read this at request time so a long-running
    CLI process observes an environment override without requiring a restart.
    The value can extend, but never shorten, the historical 300-second
    default: the setting is intended to accommodate long agent work, not to
    make the transport less tolerant than before.
    """
    raw = os.environ.get(_TIMEOUT_ENV_VAR)
    if raw is None or raw == "":
        return DEFAULT_AGENT_READ_TIMEOUT
    try:
        configured = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{_TIMEOUT_ENV_VAR} must be a positive number of seconds, "
            f"got {raw!r}. Unset it to use the default "
            f"({DEFAULT_AGENT_READ_TIMEOUT})."
        ) from exc
    if configured <= 0:
        raise ValueError(
            f"{_TIMEOUT_ENV_VAR} must be a positive number of seconds, "
            f"got {configured}. Unset it to use the default "
            f"({DEFAULT_AGENT_READ_TIMEOUT})."
        )
    return max(DEFAULT_AGENT_READ_TIMEOUT, configured)
