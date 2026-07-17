# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``AgentSidecarSpec`` — the data that parametrizes :class:`AgentSidecarManager`
for one kind of sidecar agent (issue #2142).

Email is the first registered agent; :func:`builtin_specs` is where new agents
get added as this generalizes beyond email.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AgentSidecarSpec:
    """Immutable description of one sidecar agent kind.

    ``token_env_var`` is a cross-repo literal contract: for the email spec it
    MUST equal ``gaia_agent_email.caller_auth.TOKEN_ENV_VAR``. Kept as a plain
    string (not imported from the hub wheel) so the daemon never depends on it.
    """

    agent_id: str
    service_id: str
    display_name: str
    expected_api_major: str
    token_env_var: str
    mode_env_var: str
    cache_dir_name: str
    dev_src_dir: Optional[Path] = None
    dev_app_dir: str = "packaging"
    dev_module: str = "server:app"
    health_timeout: float = 30.0


# The email agent's caller-auth token channel (#1706). MUST equal
# gaia_agent_email.caller_auth.TOKEN_ENV_VAR — kept a literal so core never
# imports the hub wheel.
_EMAIL_TOKEN_ENV_VAR = "GAIA_EMAIL_SIDECAR_TOKEN"


def _default_email_src_dir() -> Path:
    # src/gaia/daemon/sidecars/spec.py -> repo root is parents[4].
    return Path(__file__).resolve().parents[4] / "hub" / "agents" / "email" / "python"


def builtin_specs() -> "dict[str, AgentSidecarSpec]":
    """Return the specs for every agent the daemon knows how to supervise."""
    return {
        "email": AgentSidecarSpec(
            agent_id="email",
            service_id="gaia-agent-email",
            display_name="Email",
            expected_api_major="2",
            token_env_var=_EMAIL_TOKEN_ENV_VAR,
            mode_env_var="GAIA_EMAIL_AGENT_MODE",
            cache_dir_name="email",
            dev_src_dir=_default_email_src_dir(),
        ),
    }
