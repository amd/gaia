# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``AgentSidecarSpec`` — the data that parametrizes :class:`AgentSidecarManager`
for one kind of sidecar agent (issue #2142).

Email is the first registered agent; :func:`builtin_specs` is where new agents
get added as this generalizes beyond email.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class AgentSidecarSpec:
    """Immutable description of one sidecar agent kind.

    ``token_env_var`` is a cross-repo literal contract: for the email spec it
    MUST equal ``gaia_agent_email.caller_auth.TOKEN_ENV_VAR``. Kept as a plain
    string (not imported from the hub wheel) so the daemon never depends on it.

    ``token_file_env_var`` is the file-delivery leg of the same contract
    (#2149): the manager writes the launch secret to a 0600 file and hands the
    sidecar its PATH via this variable, so the secret itself never sits in the
    child's environment. ``secret_file_min_version`` is the first agent version
    whose binary reads that file; older installed binaries keep the (deprecated,
    loudly logged) bare-env leg. Both unset → the spec has no file contract and
    delivery stays env-based.

    OAuth forward-out (#2154) fields — all optional, so an agent that needs no
    forwarded connectors is unaffected:

    - ``grant_agent_id`` — the namespaced agent id the connectors grant ledger
      keys by (e.g. ``installed:email``). The daemon (custody home) resolves
      grants and mints tokens under THIS id; it differs from the daemon's own
      ``agent_id`` ("email"). A literal, not imported from the hub wheel.
    - ``forward_providers`` — the connector providers whose short-lived access
      tokens the daemon may forward OUT to this sidecar (granted ones only).
    - ``forwarded_mode_env_var`` — private env channel the manager sets to ``1``
      on spawn so the sidecar boots reading forwarded credentials instead of the
      machine keyring/grants store (the whole point of forward-out: the sidecar
      never holds a long-lived refresh token). MUST equal the hub package's
      ``gaia_agent_email.forwarded_credentials.FORWARDED_MODE_ENV_VAR``.
    """

    agent_id: str
    service_id: str
    display_name: str
    expected_api_major: str
    token_env_var: str
    mode_env_var: str
    cache_dir_name: str
    token_file_env_var: Optional[str] = None
    secret_file_min_version: Optional[str] = None
    dev_src_dir: Optional[Path] = None
    dev_app_dir: str = "packaging"
    dev_module: str = "server:app"
    health_timeout: float = 30.0
    grant_agent_id: Optional[str] = None
    forward_providers: Tuple[str, ...] = field(default_factory=tuple)
    forwarded_mode_env_var: Optional[str] = None


# The email agent's caller-auth token channel (#1706). MUST equal
# gaia_agent_email.caller_auth.TOKEN_ENV_VAR — kept a literal so core never
# imports the hub wheel.
_EMAIL_TOKEN_ENV_VAR = "GAIA_EMAIL_SIDECAR_TOKEN"

# File-delivery leg (#2149). MUST equal
# gaia_agent_email.caller_auth.TOKEN_FILE_ENV_VAR — literal for the same reason.
_EMAIL_TOKEN_FILE_ENV_VAR = "GAIA_EMAIL_SIDECAR_TOKEN_FILE"

# First gaia-agent-email version whose binary reads the token file. Keep in
# lock-step with the release cut that first ships caller_auth's file leg.
_EMAIL_SECRET_FILE_MIN_VERSION = "0.6.0"

# The email agent's grant-ledger identity (mirrors gaia-agent.yaml ``id: email``
# → ``installed:email``, and ``connector_routes.EMAIL_AGENT_ID``). Kept a literal
# so core never imports the hub wheel.
_EMAIL_GRANT_AGENT_ID = "installed:email"

# The email sidecar's forwarded-credentials mode switch (#2154). MUST equal
# gaia_agent_email.forwarded_credentials.FORWARDED_MODE_ENV_VAR — a literal.
_EMAIL_FORWARDED_MODE_ENV_VAR = "GAIA_EMAIL_FORWARDED_CREDENTIALS"


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
            token_file_env_var=_EMAIL_TOKEN_FILE_ENV_VAR,
            secret_file_min_version=_EMAIL_SECRET_FILE_MIN_VERSION,
            mode_env_var="GAIA_EMAIL_AGENT_MODE",
            cache_dir_name="email",
            dev_src_dir=_default_email_src_dir(),
            grant_agent_id=_EMAIL_GRANT_AGENT_ID,
            forward_providers=("google", "microsoft"),
            forwarded_mode_env_var=_EMAIL_FORWARDED_MODE_ENV_VAR,
        ),
    }
