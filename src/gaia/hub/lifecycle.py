# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agent Hub lifecycle: per-agent configure, health check, and status.

This module is the *post-install* half of the agent lifecycle manager (issue
#465). The install/uninstall/rollback half ships in :mod:`gaia.hub.installer`
(#1096); this module adds the three operations a user performs on an agent that
is already installed:

* :func:`configure` — persist per-agent settings (model preference, arbitrary
  overrides) under ``~/.gaia/agents/<id>/config.json``. The config survives
  restarts and is read by :func:`status` and the Agent UI.
* :func:`health_check` — verify that an installed agent actually *loads*: its
  registration resolves and its entry point / module imports. Distinguishes
  ``healthy`` / ``degraded`` / ``error`` / ``not_installed`` so the UI can show
  a working/degraded/broken badge instead of a silent failure at first use.
* :func:`status` / :func:`status_all` — aggregate installed version + health +
  config summary into one record per agent for the catalog/status panel.

Fail-loudly (CLAUDE.md): :func:`configure` rejects a non-dict config and a
reserved/empty id rather than silently writing junk. :func:`health_check` never
swallows a load failure — it surfaces it as ``error`` with the underlying
message instead of pretending the agent is fine.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gaia.hub import installer as installer_mod
from gaia.logger import get_logger

logger = get_logger(__name__)

# Per-agent config file written alongside the install sentinel.
CONFIG_NAME = "config.json"

# Health states (issue #465). ``degraded`` = loads but missing something
# optional; ``error`` = a required piece is broken; ``not_installed`` = nothing
# to check.
HEALTH_HEALTHY = "healthy"
HEALTH_DEGRADED = "degraded"
HEALTH_ERROR = "error"
HEALTH_NOT_INSTALLED = "not_installed"

# Top-level config keys we recognise explicitly (everything else is preserved
# verbatim under the persisted dict — agents may declare their own settings).
CONFIG_MODEL_KEY = "model"


class LifecycleError(RuntimeError):
    """Raised when a configure/health/status operation cannot proceed.

    The message names *what* failed, *what* to do, and *where* to look, per the
    project's fail-loudly rule.
    """


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def config_path(agent_id: str, install_root: Optional[Path] = None) -> Path:
    """Path of the per-agent ``config.json`` (``~/.gaia/agents/<id>/config.json``).

    Raises:
        LifecycleError: If *agent_id* is not a safe single path component
            (path-traversal shaped ids are rejected by the installer).
    """
    try:
        return installer_mod.agent_install_dir(agent_id, install_root) / CONFIG_NAME
    except installer_mod.InstallError as exc:
        raise LifecycleError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Configure
# ---------------------------------------------------------------------------


def read_config(agent_id: str, install_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the persisted config for *agent_id* (``{}`` if none).

    Raises:
        LifecycleError: If the config file exists but is not valid JSON / not a
            JSON object — a corrupt config is a loud failure, not an empty dict.
    """
    path = config_path(agent_id, install_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleError(
            f"config for '{agent_id}' at {path} is unreadable or corrupt: {exc}. "
            f"Fix or delete the file, then re-run 'gaia agent configure {agent_id}'."
        ) from exc
    if not isinstance(data, dict):
        raise LifecycleError(
            f"config for '{agent_id}' at {path} must be a JSON object, got "
            f"{type(data).__name__}. Delete the file and reconfigure."
        )
    return data


def configure(
    agent_id: str,
    config: Dict[str, Any],
    *,
    install_root: Optional[Path] = None,
    merge: bool = True,
) -> Dict[str, Any]:
    """Persist per-agent configuration under ``~/.gaia/agents/<id>/config.json``.

    Args:
        agent_id: Agent to configure (hub-installed, builtin, or custom).
        config: Settings to store (e.g. ``{"model": "Qwen3.5-35B-A3B-GGUF"}``).
        install_root: Override the install root (tests pass a tmp dir).
        merge: When True (default) merge into the existing config; when False
            replace it wholesale.

    Returns:
        The full persisted config dict (post-merge).

    Raises:
        LifecycleError: If *agent_id* is empty/reserved-invalid or *config* is
            not a dict.
    """
    if not agent_id or not isinstance(agent_id, str):
        raise LifecycleError("agent_id must be a non-empty string.")
    if not isinstance(config, dict):
        raise LifecycleError(
            f"config for '{agent_id}' must be a dict of settings, got "
            f"{type(config).__name__}."
        )

    existing = read_config(agent_id, install_root) if merge else {}
    merged = {**existing, **config}

    path = config_path(agent_id, install_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("lifecycle: wrote config for %s -> %s", agent_id, path)
    return merged


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@dataclass
class HealthStatus:
    """Outcome of :func:`health_check`."""

    id: str
    state: str  # healthy | degraded | error | not_installed
    detail: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.state in (HEALTH_HEALTHY, HEALTH_DEGRADED)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "detail": self.detail,
            "warnings": list(self.warnings),
        }


# A loader probe: given (agent_id, registry), import/resolve the agent so we
# know it actually loads. Returns a list of non-fatal warnings (→ degraded) or
# raises to signal a hard error. Injected in tests.
LoaderProbe = Callable[[str, Any], List[str]]


def _default_loader(agent_id: str, registry: Any) -> List[str]:
    """Resolve an installed agent's entry point / module to prove it loads.

    Built-ins are in-tree and always importable, so a registered builtin needs
    no entry-point probe. For installed wheel agents we re-resolve the
    ``gaia.agent`` / ``gaia.agents`` entry point named *agent_id* and ``.load()``
    it — that is precisely "the entry point resolves". A failure raises.
    """
    if installer_mod.is_builtin(agent_id):
        # Builtins ship in the core wheel; presence in the registry is proof.
        if registry is not None and registry.get(agent_id) is None:
            raise LifecycleError(
                f"builtin agent '{agent_id}' is not present in the registry; "
                f"the core install may be broken."
            )
        return []

    import importlib

    from gaia.agents.registry import AGENT_ENTRY_POINT_GROUPS

    last_err: Optional[Exception] = None
    for group in AGENT_ENTRY_POINT_GROUPS:
        for ep in importlib.metadata.entry_points(group=group):
            if ep.name != agent_id:
                continue
            try:
                ep.load()
                return []
            except Exception as exc:  # noqa: BLE001 - re-raised as LifecycleError
                last_err = exc
    if last_err is not None:
        raise LifecycleError(
            f"agent '{agent_id}' is installed but its entry point failed to "
            f"load: {last_err}. Re-install it ('gaia agent' / the Hub UI) or "
            f"check its dependencies."
        )
    raise LifecycleError(
        f"agent '{agent_id}' is installed on disk but exposes no "
        f"'gaia.agent' entry point, so it cannot be loaded. The package may be "
        f"incomplete; re-install it."
    )


def _binary_health(
    agent_id: str,
    sentinel: "installer_mod.InstalledAgent",
    install_root: Optional[Path],
    warnings: List[str],
) -> HealthStatus:
    """Health for a binary-kind install: the executable exists (+x on POSIX).

    Binary agents ship no site-packages / entry point, so the wheel loader
    probe would always report them broken; presence of the runnable executable
    IS the health signal.
    """
    install_dir = installer_mod.agent_install_dir(agent_id, install_root)
    if not sentinel.executable:
        return HealthStatus(
            id=agent_id,
            state=HEALTH_ERROR,
            detail=(
                f"'{agent_id}' is a binary install but its sentinel records no "
                f"executable name; re-install it from the Hub."
            ),
            warnings=warnings,
        )
    exe = install_dir / sentinel.executable
    if not exe.is_file():
        return HealthStatus(
            id=agent_id,
            state=HEALTH_ERROR,
            detail=(
                f"'{agent_id}' executable is missing at {exe}; "
                f"re-install it from the Hub."
            ),
            warnings=warnings,
        )
    if os.name != "nt" and not os.access(exe, os.X_OK):
        return HealthStatus(
            id=agent_id,
            state=HEALTH_ERROR,
            detail=(
                f"'{agent_id}' executable at {exe} is not executable "
                f"(chmod +x it, or re-install from the Hub)."
            ),
            warnings=warnings,
        )
    if warnings:
        return HealthStatus(
            id=agent_id,
            state=HEALTH_DEGRADED,
            detail=f"'{agent_id}' loads but has {len(warnings)} warning(s).",
            warnings=warnings,
        )
    return HealthStatus(
        id=agent_id, state=HEALTH_HEALTHY, detail=f"'{agent_id}' is healthy."
    )


def health_check(
    agent_id: str,
    *,
    registry: Any = None,
    install_root: Optional[Path] = None,
    loader: Optional[LoaderProbe] = None,
) -> HealthStatus:
    """Check whether an installed agent is working.

    Returns one of ``healthy`` / ``degraded`` / ``error`` / ``not_installed``:

    * ``not_installed`` — no install sentinel, not a builtin, and not registered.
    * ``error`` — installed but the entry point / module fails to load.
    * ``degraded`` — loads, but something optional is off (e.g. a corrupt config
      or an optional warning from the loader probe).
    * ``healthy`` — loads cleanly with no warnings.

    Binary-kind installs (sentinel ``artifact_kind: binary``) are probed by
    executable presence instead of the entry-point loader — they have no
    entry point by design.
    """
    loader = loader or _default_loader

    sentinel = installer_mod.read_sentinel(agent_id, install_root)
    installed = sentinel is not None
    is_builtin = installer_mod.is_builtin(agent_id)
    registered = registry is not None and registry.get(agent_id) is not None

    if not installed and not is_builtin and not registered:
        return HealthStatus(
            id=agent_id,
            state=HEALTH_NOT_INSTALLED,
            detail=f"'{agent_id}' is not installed.",
        )

    warnings: List[str] = []

    # A corrupt config is non-fatal (the agent can run on defaults) but must be
    # surfaced — it degrades, it doesn't silently pass.
    try:
        read_config(agent_id, install_root)
    except LifecycleError as exc:
        warnings.append(str(exc))

    if (
        sentinel is not None
        and sentinel.artifact_kind == installer_mod.ARTIFACT_KIND_BINARY
    ):
        return _binary_health(agent_id, sentinel, install_root, warnings)

    try:
        warnings.extend(loader(agent_id, registry) or [])
    except Exception as exc:  # noqa: BLE001 - any load failure → error state
        return HealthStatus(
            id=agent_id,
            state=HEALTH_ERROR,
            detail=str(exc),
            warnings=warnings,
        )

    if warnings:
        return HealthStatus(
            id=agent_id,
            state=HEALTH_DEGRADED,
            detail=f"'{agent_id}' loads but has {len(warnings)} warning(s).",
            warnings=warnings,
        )
    return HealthStatus(
        id=agent_id, state=HEALTH_HEALTHY, detail=f"'{agent_id}' is healthy."
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@dataclass
class AgentStatus:
    """Aggregated lifecycle status for one agent."""

    id: str
    installed: bool
    installed_version: Optional[str]
    health: str
    config: Dict[str, Any] = field(default_factory=dict)
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "installed": self.installed,
            "installed_version": self.installed_version,
            "health": self.health,
            "config": dict(self.config),
            "source": self.source,
        }


def status(
    agent_id: str,
    *,
    registry: Any = None,
    install_root: Optional[Path] = None,
    loader: Optional[LoaderProbe] = None,
) -> AgentStatus:
    """Aggregate installed version, health, and config summary for one agent."""
    sentinel = installer_mod.read_sentinel(agent_id, install_root)
    is_builtin = installer_mod.is_builtin(agent_id)
    reg = registry.get(agent_id) if registry is not None else None

    installed = sentinel is not None or is_builtin or reg is not None
    health = health_check(
        agent_id, registry=registry, install_root=install_root, loader=loader
    )

    # Config read is best-effort here — a corrupt config already shows up as a
    # health warning, so summarise empty rather than raise from status().
    try:
        config = read_config(agent_id, install_root)
    except LifecycleError:
        config = {}

    # Hub-installed agents carry a SemVer in their sentinel; builtins/custom
    # agents have no published version, so report None rather than guess.
    version = sentinel.version if sentinel is not None else None

    source = ""
    if reg is not None:
        source = reg.source
    elif sentinel is not None:
        source = "installed"
    elif is_builtin:
        source = "builtin"

    return AgentStatus(
        id=agent_id,
        installed=installed,
        installed_version=version,
        health=health.state,
        config=config,
        source=source,
    )


def status_all(
    *,
    registry: Any = None,
    install_root: Optional[Path] = None,
    loader: Optional[LoaderProbe] = None,
) -> Dict[str, AgentStatus]:
    """Status for every discovered agent (hub-installed + registered)."""
    ids = set(installer_mod.list_installed(install_root).keys())
    if registry is not None:
        for reg in registry.list():
            ids.add(reg.id)
    return {
        aid: status(aid, registry=registry, install_root=install_root, loader=loader)
        for aid in sorted(ids)
    }
