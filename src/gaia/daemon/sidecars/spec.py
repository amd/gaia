# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``AgentSidecarSpec`` — the data that parametrizes :class:`AgentSidecarManager`
for one kind of sidecar agent (issue #2142).

Email is the first registered agent; :func:`builtin_specs` is where new agents
get added as this generalizes beyond email.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from gaia.connectors.providers.base import ConnectorRequirement
from gaia.daemon.sidecars.errors import DevSrcDirResolutionError


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

    ``mode_env_var`` names the env var a CALLER's own process reads to imply
    "user" vs "dev" mode when it does not pass an explicit override (issue
    #2588). It is resolved by :func:`resolve_caller_mode` against the
    resolving process's OWN environment — never the daemon's. A shell export
    of this variable therefore has no effect on an already-running daemon; it
    only matters to whichever caller (CLI, Agent UI) forwards its own value.

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

    Connector-grant registration (#2408) — host/UI-facing only, never consulted
    by :class:`~gaia.daemon.sidecars.manager.AgentSidecarManager`:

    - ``required_connections`` — the connector scopes this sidecar needs,
      surfaced into the live ``AgentRegistry`` (via
      ``gaia.hub.installer.register_installed_sidecars`` ->
      ``AgentRegistry.register_sidecar``) so the connectors grant flow and
      ``/api/agents`` can see them without an importable wheel. Transcribed as
      literals from the hub package's own scope constants (core cannot import
      the wheel at runtime, #2154) and guarded against drift by
      ``tests/unit/connectors/test_email_scope_drift.py``.
    """

    agent_id: str
    service_id: str
    display_name: str
    expected_api_major: str
    token_env_var: str
    mode_env_var: str
    cache_dir_name: str
    # Guide URL surfaced in user-mode failure messages so a stuck user has a
    # place to read next (kept generic: shared spawn/fetch code has no agent
    # docs baked in). Must keep the /docs/ prefix (#1058).
    docs_url: Optional[str] = None
    token_file_env_var: Optional[str] = None
    secret_file_min_version: Optional[str] = None
    dev_src_dir: Optional[Path] = None
    dev_app_dir: str = "packaging"
    dev_module: str = "server:app"
    health_timeout: float = 30.0
    grant_agent_id: Optional[str] = None
    forward_providers: Tuple[str, ...] = field(default_factory=tuple)
    forwarded_mode_env_var: Optional[str] = None
    required_connections: Tuple[ConnectorRequirement, ...] = field(
        default_factory=tuple
    )


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

# Connector scopes the email sidecar needs (#2408). Transcribed as literals —
# not imported — so core never depends on the hub wheel (server.py:621-629).
# MUST equal gaia_agent_email/scopes.py's ALL_SCOPES (GMAIL_SCOPES +
# CALENDAR_SCOPES) / REQUIRED_SCOPES (GMAIL_SCOPES) and outlook_scopes.py's
# OUTLOOK_ALL_SCOPES / OUTLOOK_REQUIRED_SCOPES. Guarded against drift by
# tests/unit/connectors/test_email_scope_drift.py. ``scopes`` is what the
# daemon REQUESTS at consent; ``required_scopes`` (#2730 D5) is the narrower
# subset the forward-out mint ENFORCES — calendar is requested but optional.
_EMAIL_REQUIRED_CONNECTIONS = (
    ConnectorRequirement(
        connector_id="google",
        scopes=(
            "https://www.googleapis.com/auth/gmail.modify",  # from gaia_agent_email/scopes.py
            "https://www.googleapis.com/auth/gmail.send",  # from gaia_agent_email/scopes.py
            "https://www.googleapis.com/auth/calendar.events",  # from gaia_agent_email/scopes.py
            "https://www.googleapis.com/auth/calendar.readonly",  # from gaia_agent_email/scopes.py
        ),
        required_scopes=(
            "https://www.googleapis.com/auth/gmail.modify",  # from gaia_agent_email/scopes.py
            "https://www.googleapis.com/auth/gmail.send",  # from gaia_agent_email/scopes.py
        ),
    ),
    ConnectorRequirement(
        connector_id="microsoft",
        scopes=(
            "https://graph.microsoft.com/Mail.ReadWrite",  # from gaia_agent_email/outlook_scopes.py
            "https://graph.microsoft.com/Mail.Send",  # from gaia_agent_email/outlook_scopes.py
            "https://graph.microsoft.com/Calendars.ReadWrite",  # from gaia_agent_email/outlook_scopes.py
        ),
        required_scopes=(
            "https://graph.microsoft.com/Mail.ReadWrite",  # from gaia_agent_email/outlook_scopes.py
            "https://graph.microsoft.com/Mail.Send",  # from gaia_agent_email/outlook_scopes.py
        ),
    ),
    ConnectorRequirement(
        connector_id="microsoft_work",
        scopes=(
            "https://graph.microsoft.com/Mail.ReadWrite",  # from gaia_agent_email/outlook_scopes.py
            "https://graph.microsoft.com/Mail.Send",  # from gaia_agent_email/outlook_scopes.py
            "https://graph.microsoft.com/Calendars.ReadWrite",  # from gaia_agent_email/outlook_scopes.py
        ),
        required_scopes=(
            "https://graph.microsoft.com/Mail.ReadWrite",  # from gaia_agent_email/outlook_scopes.py
            "https://graph.microsoft.com/Mail.Send",  # from gaia_agent_email/outlook_scopes.py
        ),
    ),
)


def agent_dev_src_dir(repo_root: Path, agent_id: str) -> Path:
    """The per-agent dev-mode source directory under a repo root.

    Single owner of the ``hub/agents/<id>/python`` layout convention so the
    daemon's own default (below) and a caller's own resolution (issue #2588)
    always agree on the same join — hardcoding this a second time anywhere
    else is how a caller-resolved path silently fails to match the daemon's.
    """
    return repo_root / "hub" / "agents" / agent_id / "python"


def repo_root_from_agent_dev_src_dir(dev_src_dir: Path, agent_id: str) -> Path:
    """Invert :func:`agent_dev_src_dir`: recover the repo root a per-agent
    dev-mode source dir was joined from.

    A "restart the daemon" remedy must name the REPO ROOT — that's what a
    Python environment/editable install is rooted at, and what the daemon's
    own ``parents[4]`` anchor actually depends on — never the per-agent
    source dir itself, which restarting from does nothing to change. Single
    owner of the inverse join so that remedy can't independently drift from
    :func:`agent_dev_src_dir`'s forward join.

    Raises:
        DevSrcDirResolutionError: *dev_src_dir* does not end in
            ``hub/agents/<agent_id>/python`` — guessing a repo root from an
            unexpected shape (e.g. an explicit ``--dev-src-dir`` pointed
            somewhere else entirely) would be worse than refusing.
    """
    expected_tail = ("hub", "agents", agent_id, "python")
    parts = dev_src_dir.parts
    tail = parts[-len(expected_tail) :] if len(parts) >= len(expected_tail) else ()
    # Case-insensitive by design, unlike the identity comparison elsewhere in
    # this module: this matches a path's SHAPE against fixed literals, not
    # two independent user paths for equality, so it isn't the case-folding
    # hazard that comparison must avoid.
    if tuple(p.lower() for p in tail) != tuple(t.lower() for t in expected_tail):
        raise DevSrcDirResolutionError(
            f"'{dev_src_dir}' does not end in the expected "
            f"hub/agents/{agent_id}/python layout, so no repo root can be "
            "derived from it to name in a restart remedy."
        )
    return Path(*parts[: -len(expected_tail)])


def _default_email_src_dir() -> Path:
    # src/gaia/daemon/sidecars/spec.py -> repo root is parents[4]. This
    # follows the Python environment that launched the DAEMON, never a
    # caller's — see resolve_caller_dev_src_dir for the caller-side mirror.
    return agent_dev_src_dir(Path(__file__).resolve().parents[4], "email")


def resolve_caller_mode(agent_id: str, override: Optional[str] = None) -> str:
    """The mode THIS PROCESS's own environment implies for *agent_id*.

    *override* (an explicit ``--mode`` flag, say) wins outright. Otherwise the
    resolving process's own ``os.environ[spec.mode_env_var]`` is consulted —
    an *agent_id* absent from :func:`builtin_specs` has no env var to consult
    and falls straight through to ``"user"``.

    Deliberately only ever reads THIS process's environment. The daemon is a
    long-lived, per-user singleton with its own environment, distinct from
    whichever caller (CLI, Agent UI) is asking it to do something — consulting
    the daemon's env for the caller's intent is issue #2588's root cause A.
    Every caller must resolve its own intent and send it explicitly.
    """
    spec = builtin_specs().get(agent_id)
    env_var = spec.mode_env_var if spec is not None else None
    return override or (os.environ.get(env_var) if env_var else None) or "user"


def _normalize_git_toplevel(raw: str) -> Path:
    """Normalize ``git rev-parse --show-toplevel`` output for the running OS.

    Git-Bash on Windows emits POSIX-shaped paths like ``/c/Users/...``; a
    native Windows Python parses that as rooted at the CURRENT drive, silently
    producing the wrong path rather than an error. Rewrite the drive-letter
    prefix before it ever becomes a ``Path``.
    """
    text = raw.strip()
    if os.name == "nt":
        match = re.match(r"^/([A-Za-z])/(.*)$", text)
        if match:
            drive, rest = match.groups()
            text = f"{drive}:/{rest}"
    return Path(text)


def resolve_caller_dev_src_dir(
    agent_id: str,
    *,
    explicit: Optional[str] = None,
    cwd: Optional[Path] = None,
) -> Path:
    """The CALLER's own per-agent dev-mode source dir (issue #2588 root cause B).

    Never derived from ``__file__`` — that follows the Python environment that
    launched the CALLER, which is exactly the ambiguity this exists to avoid
    when the caller and the daemon do not share one checkout. Resolution
    order: *explicit* (the caller's own escape hatch, e.g. a ``--dev-src-dir``
    flag) wins outright; otherwise the caller's *cwd* (default
    ``Path.cwd()``) via ``git rev-parse --show-toplevel``, joined through
    :func:`agent_dev_src_dir`. Every path returned is
    ``.expanduser().resolve()``d so it compares correctly, as a ``Path``
    object, against the daemon's own ``spec.dev_src_dir``.

    Raises:
        DevSrcDirResolutionError: *explicit* is not an absolute path (a
            relative path would resolve against whichever process reads it
            next, which is the bug wearing a new hat), or the caller's *cwd*
            is not inside a git work tree (no ``git`` on PATH, or the command
            fails) — there is no silent guess in either case.
    """
    if explicit is not None:
        candidate = Path(explicit)
        if not candidate.is_absolute():
            raise DevSrcDirResolutionError(
                f"--dev-src-dir must be an absolute path; got '{explicit}'. A "
                "relative path would resolve against whichever process reads "
                "it, which is exactly the ambiguity this flag exists to avoid."
            )
        return candidate.expanduser().resolve()

    resolved_cwd = cwd or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(resolved_cwd),
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise DevSrcDirResolutionError(
            f"could not determine your checkout root from {resolved_cwd} "
            f"({exc}). Run this command from inside a git work tree, or pass "
            "--dev-src-dir <path> to name the source directory explicitly."
        ) from exc

    repo_root = _normalize_git_toplevel(result.stdout)
    return agent_dev_src_dir(repo_root, agent_id).expanduser().resolve()


def builtin_specs() -> "dict[str, AgentSidecarSpec]":
    """Return the specs for every agent the daemon knows how to supervise."""
    return {
        "email": AgentSidecarSpec(
            agent_id="email",
            service_id="gaia-agent-email",
            display_name="Email",
            expected_api_major="2",
            docs_url="https://amd-gaia.ai/docs/guides/email",
            token_env_var=_EMAIL_TOKEN_ENV_VAR,
            token_file_env_var=_EMAIL_TOKEN_FILE_ENV_VAR,
            secret_file_min_version=_EMAIL_SECRET_FILE_MIN_VERSION,
            mode_env_var="GAIA_EMAIL_AGENT_MODE",
            cache_dir_name="email",
            dev_src_dir=_default_email_src_dir(),
            grant_agent_id=_EMAIL_GRANT_AGENT_ID,
            forward_providers=("google", "microsoft", "microsoft_work"),
            forwarded_mode_env_var=_EMAIL_FORWARDED_MODE_ENV_VAR,
            required_connections=_EMAIL_REQUIRED_CONNECTIONS,
        ),
    }
