# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Daemon-side agent install / uninstall / catalog.

The daemon owns install so the TUI, the CLI (``gaia hub *``) and the Agent UI
share ONE implementation, one integrity check and one install lock instead of
three forks. All of the heavy lifting already exists and is daemon-safe:

* :mod:`gaia.hub.catalog` — hub ``index.json`` fetch + the offline cache.
* :mod:`gaia.hub.installer` — download, **SHA-256 verify against the hub
  manifest**, atomic write, the ``.installed`` sentinel, and the
  one-install-per-id ``_install_slot`` guard.

What this module adds is the two things only the daemon can do:

1. **Stop the sidecar before mutating its directory.** An agent's install dir
   *is* the sidecar's binary cache; replacing or deleting a file a live process
   holds open corrupts it (or fails outright on Windows). Every mutating entry
   point stops the sidecar through the registry first, and the registry's
   post-kill liveness check turns a surviving pid into a loud
   :class:`StopFailedError` that **aborts the mutation**.
2. **Refuse agents the daemon could not run.** ``builtin_specs()`` is a static
   table (see the module docstring of :mod:`gaia.daemon.sidecars.spec`), so the
   hub can advertise agents the daemon has no spec for. Rather than install
   something that can never start, the catalog filters those ids out (visibly —
   the response reports what was filtered) and install refuses them.

There is no "install it anyway" path and no silent degradation: a checksum
mismatch, an unreachable hub, or a surviving sidecar pid each fail loudly with
a remedy.
"""

from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from gaia.daemon.sidecars.errors import (
    AgentNotInstalledError,
    HubUnavailableError,
    InstallBusyError,
    InstallFailedError,
    UnknownAgentError,
    UnsupervisedAgentError,
)
from gaia.logger import get_logger

logger = get_logger(__name__)

# Ids queued for install but whose worker has not yet entered
# ``installer.install`` (and therefore not yet claimed ``_install_slot``).
# Closes the window where two POSTs both pass the busy check and the second
# worker's InstallInProgressError would clobber the first one's progress.
_QUEUE_LOCK = threading.Lock()
_QUEUED: set = set()


def _spawn_thread(work: Callable[[], None]) -> None:
    """Default install runner: a detached worker thread."""
    threading.Thread(target=work, name="gaia-daemon-install", daemon=True).start()


def _installer():
    # Deferred: gaia.hub pulls in the agent registry; the daemon must not pay
    # that at import time (it only matters once someone installs something).
    from gaia.hub import installer as installer_mod

    return installer_mod


def _catalog():
    from gaia.hub import catalog as catalog_mod

    return catalog_mod


def supervised_ids(registry) -> "set[str]":
    """Agent ids the daemon has a sidecar spec for (i.e. can actually start)."""
    return {entry["agent_id"] for entry in registry.list_agents()}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def build_catalog(
    *,
    registry,
    refresh: bool = False,
    include_unsupervised: bool = False,
    installed_only: bool = False,
    base_url: Optional[str] = None,
    fetcher=None,
    cache_path: Optional[Path] = None,
    install_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """The hub catalog merged with local install state.

    One call answers "what can I install, and what do I already have" — the
    installed flags come from the ``~/.gaia/agents/*/.installed`` sentinels, so
    a client never has to make a second call. ``installed_only`` answers the
    purely local question ("what do I have") without touching the network at
    all, so `gaia hub list --installed` works offline.

    Not :func:`gaia.hub.catalog.build_catalog`: that one merges against a live
    :class:`AgentRegistry` of in-process Python agents, which the daemon has no
    business constructing. Deprecated-but-not-installed agents are hidden here
    too, matching what the Agent UI shows.

    Raises:
        HubUnavailableError: hub unreachable AND no usable offline cache.
    """
    catalog_mod = _catalog()
    installer_mod = _installer()
    if installed_only:
        return _installed_catalog(registry, cache_path, install_root)
    try:
        result = catalog_mod.load_index(
            base_url=base_url,
            fetcher=fetcher,
            cache_path=cache_path,
            force=refresh,
        )
    except catalog_mod.CatalogError as exc:
        raise HubUnavailableError(str(exc)) from exc

    known = supervised_ids(registry)
    installed = installer_mod.installed_versions(install_root)

    agents: List[Dict[str, Any]] = []
    filtered: List[str] = []
    for entry in result.agents:
        agent_id = entry.get("id")
        installed_version = installed.get(agent_id)
        # A deprecated agent nobody has installed is not offered (same rule the
        # Agent UI applies); a deprecated one you DO have stays visible so you
        # can see it and remove it.
        if entry.get("deprecated") and installed_version is None:
            continue
        can_supervise = agent_id in known
        if not can_supervise and not include_unsupervised:
            filtered.append(agent_id)
            continue
        agents.append(
            _merge_entry(catalog_mod, entry, installed_version, can_supervise)
        )

    return {
        "agents": agents,
        "offline": result.offline,
        "source": result.source,
        "generated_at": result.generated_at,
        "hub_url": (base_url or catalog_mod.get_hub_base_url()),
        # Never silently hidden: the ids dropped for lack of a sidecar spec are
        # reported so a client can say WHY an agent it expected is missing.
        "unsupervised_filtered": sorted(filtered),
    }


def _merge_entry(
    catalog_mod,
    entry: Dict[str, Any],
    installed_version: Optional[str],
    can_supervise: bool,
) -> Dict[str, Any]:
    latest = entry.get("latest_version") or ""
    item = dict(entry)
    item["installed"] = installed_version is not None
    item["installed_version"] = installed_version
    item["update_available"] = bool(
        installed_version
        and latest
        and catalog_mod.compare_versions(latest, installed_version) > 0
    )
    item["supervised"] = can_supervise
    return item


def _installed_catalog(
    registry, cache_path: Optional[Path], install_root: Optional[Path]
) -> Dict[str, Any]:
    """Installed agents only, from the sentinels — no network at all.

    Names/sizes are enriched from the last cached hub index when there is one;
    an agent missing from it still shows up with its id and version rather than
    disappearing.
    """
    catalog_mod = _catalog()
    installer_mod = _installer()
    cached = {
        e["id"]: e for e in catalog_mod.cached_index_agents(cache_path) if e.get("id")
    }
    known = supervised_ids(registry)
    agents = [
        _merge_entry(
            catalog_mod,
            cached.get(agent_id, {"id": agent_id, "name": agent_id}),
            record.version,
            agent_id in known,
        )
        for agent_id, record in sorted(
            installer_mod.list_installed(install_root).items()
        )
    ]
    return {
        "agents": agents,
        "offline": False,
        "source": "local",
        "generated_at": None,
        "hub_url": catalog_mod.get_hub_base_url(),
        "unsupervised_filtered": [],
    }


# ---------------------------------------------------------------------------
# Guards shared by install + uninstall
# ---------------------------------------------------------------------------


# Hub agent-id slug, identical to gaia.hub.manifest._ID_RE. Every mutating
# entry point re-validates it: the id becomes a path segment under
# ~/.gaia/agents/, so a URL-encoded "../" must never reach shutil.rmtree.
_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,50}[a-z0-9])?$")


def _validate_agent_id(agent_id: str) -> None:
    if not isinstance(agent_id, str) or not _ID_RE.match(agent_id):
        raise UnknownAgentError(
            f"'{agent_id}' is not a valid agent id. Ids are 1-52 lowercase "
            "alphanumeric characters with internal hyphens (e.g. 'email'). "
            "Run `gaia hub list` for the installable ids."
        )


def _reject_reserved(agent_id: str) -> None:
    if _installer().is_builtin(agent_id):
        raise UnsupervisedAgentError(
            f"'{agent_id}' is a reserved built-in GAIA agent: it ships with the "
            "wheel and can never be installed or uninstalled from the Agent Hub. "
            "Use `gaia hub list` to see the installable agents."
        )


def _require_supervised(agent_id: str, known: Iterable[str]) -> None:
    known = sorted(known)
    if agent_id not in known:
        raise UnknownAgentError(
            f"the daemon has no sidecar spec for '{agent_id}', so it could not "
            f"start it after installing. Installable agents: "
            f"{', '.join(known) or '(none)'}. If this agent was just published, "
            "it needs an entry in gaia.daemon.sidecars.spec.builtin_specs()."
        )


def _claim_slot(agent_id: str, action: str = "install") -> None:
    """Reserve the one-mutation-per-id slot, or raise :class:`InstallBusyError`.

    Install and uninstall share the slot — they mutate the same directory, so
    letting them interleave would let a DELETE report success while a download
    is still writing into the dir it just removed.
    """
    installer_mod = _installer()
    with _QUEUE_LOCK:
        if agent_id in _QUEUED or installer_mod.is_installing(agent_id):
            raise InstallBusyError(
                f"cannot {action} '{agent_id}': another install/uninstall for it "
                f"is already in progress. Wait for it to finish (poll GET "
                f"/daemon/v1/agents/{agent_id}/install-status), then retry."
            )
        _QUEUED.add(agent_id)


def _release_slot(agent_id: str) -> None:
    with _QUEUE_LOCK:
        _QUEUED.discard(agent_id)


@contextmanager
def sidecar_stopped(registry, agent_id: str):
    """Stop *agent_id*'s sidecar and keep it stopped for the whole body.

    Stopping once is not enough: the download + replace take tens of seconds,
    and an ``ensure`` arriving meanwhile would respawn the process from the
    directory being rewritten. The registry's ``hold_for_mutation`` holds that
    agent's lock so ``ensure``/``stop`` wait instead. A pid that SURVIVES the
    tree-kill raises :class:`StopFailedError` and the body never runs — the
    daemon never mutates a live process's directory.

    An agent the daemon has no spec for has nothing to stop and nothing that
    could respawn it, so the body runs unguarded (uninstalling such an agent
    is still legitimate).
    """
    if agent_id not in supervised_ids(registry):
        logger.info(
            "install: '%s' is not a daemon-supervised agent; nothing to stop",
            agent_id,
        )
        yield
        return
    with registry.hold_for_mutation(agent_id):
        logger.info("install: '%s' sidecar stopped and held for mutation", agent_id)
        yield


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def start_install(
    agent_id: str,
    *,
    registry,
    version: Optional[str] = None,
    base_url: Optional[str] = None,
    fetcher=None,
    install_root: Optional[Path] = None,
    runner: Optional[Callable[[Callable[[], None]], None]] = None,
) -> Dict[str, Any]:
    """Queue an install of *agent_id* and return immediately (202 semantics).

    The manifest is resolved SYNCHRONOUSLY so an unreachable hub or a malformed
    manifest is an actionable error on the request itself instead of a silent
    background failure. Progress (and any terminal error) is then polled via
    :func:`install_status`.

    Raises:
        UnsupervisedAgentError: reserved built-in id.
        UnknownAgentError: no sidecar spec — the daemon could not run it.
        InstallBusyError: an install for this id is already running.
        HubUnavailableError: the hub manifest could not be fetched.
        StopFailedError: a running sidecar survived the pre-install stop.
    """
    catalog_mod = _catalog()
    installer_mod = _installer()

    _validate_agent_id(agent_id)
    _reject_reserved(agent_id)
    _require_supervised(agent_id, supervised_ids(registry))

    url = catalog_mod.manifest_url(agent_id, base_url)
    try:
        manifest = catalog_mod.fetch_manifest(
            agent_id, base_url=base_url, fetcher=fetcher
        )
    except catalog_mod.CatalogError as exc:
        raise HubUnavailableError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - boundary translation, re-raised
        raise HubUnavailableError(
            f"could not read the Agent Hub manifest for '{agent_id}' from {url}: "
            f"{exc}. If that is a connection error, check your network or "
            "GAIA_HUB_URL; if it persists, the published manifest may be "
            "malformed — report it against the hub."
        ) from exc

    _claim_slot(agent_id)
    try:
        # Stop the sidecar up front so a survivor is a SYNCHRONOUS error the
        # caller sees on this request; the worker re-takes the hold for the
        # duration of the actual mutation.
        with sidecar_stopped(registry, agent_id):
            pass
        installer_mod.clear_progress(agent_id)
        installer_mod._set_progress(  # noqa: SLF001 - seed state for the poller
            agent_id, status="queued", phase="queued", percent=0, version=version
        )
        (runner or _spawn_thread)(
            lambda: _run_install(
                agent_id,
                registry=registry,
                version=version,
                manifest=manifest,
                base_url=base_url,
                fetcher=fetcher,
                install_root=install_root,
            )
        )
    except BaseException:
        # Anything that stops the worker from ever running (including a failure
        # to spawn the thread) must release the slot, or install/uninstall of
        # this id would be wedged for the life of the daemon.
        _release_slot(agent_id)
        raise
    return {"agent_id": agent_id, "status": "queued", "version": version}


def _run_install(
    agent_id: str,
    *,
    registry,
    version: Optional[str],
    manifest: Dict[str, Any],
    base_url: Optional[str],
    fetcher,
    install_root: Optional[Path],
) -> None:
    """Install worker. The failure is recorded in progress AND logged loudly."""
    installer_mod = _installer()
    try:
        # The hold spans the whole download+replace so no ensure can respawn
        # the sidecar from the directory being rewritten.
        with sidecar_stopped(registry, agent_id):
            result = installer_mod.install(
                agent_id,
                version=version,
                manifest=manifest,
                base_url=base_url,
                fetcher=fetcher,
                install_root=install_root,
            )
        logger.info(
            "install: %s %s installed at %s", agent_id, result.version, result.path
        )
    except Exception as exc:  # noqa: BLE001 - terminal state is the return channel
        # installer.install() records status="failed" for its own errors, but
        # not for InstallInProgressError (another process holds the slot).
        # Force a terminal state either way so a poller can never hang on
        # "queued" forever.
        state = installer_mod.get_install_status(agent_id)
        if state is None or state.get("status") not in ("failed", "completed"):
            installer_mod._set_progress(  # noqa: SLF001 - terminal state for the poller
                agent_id,
                status="failed",
                phase="failed",
                percent=0,
                version=version,
                error=str(exc),
            )
        logger.error("install: %s failed: %s", agent_id, exc)
    finally:
        _release_slot(agent_id)


def install_status(agent_id: str) -> Optional[Dict[str, Any]]:
    """Latest install progress for *agent_id*, or ``None`` if none was recorded.

    Shape: ``{agent_id, status, phase, percent, version, error}`` where
    ``status`` is ``queued`` | ``running`` | ``completed`` | ``failed``.
    """
    state = _installer().get_install_status(agent_id)
    if state is None:
        return None
    out = dict(state)
    out["agent_id"] = out.pop("id", agent_id)
    return out


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


def uninstall(
    agent_id: str,
    *,
    registry,
    install_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Stop the sidecar, verify its pid is gone, then remove the install dir.

    Raises:
        UnsupervisedAgentError: reserved built-in id (never uninstallable).
        InstallBusyError: an install for this id is in flight.
        StopFailedError: the sidecar pid survived the tree-kill (nothing is
            removed — mutating a live process's dir is never attempted).
        AgentNotInstalledError: no ``.installed`` sentinel.
        InstallFailedError: the directory could not be removed.
    """
    installer_mod = _installer()
    _validate_agent_id(agent_id)
    _reject_reserved(agent_id)
    # Uninstall takes the SAME slot as install: holding it is what stops a
    # concurrent install from downloading into the directory being removed (and
    # stops this removal from deleting a just-completed install).
    _claim_slot(agent_id, action="uninstall")
    try:
        with sidecar_stopped(registry, agent_id):
            installer_mod.uninstall(agent_id, install_root=install_root)
    except installer_mod.NotInstalledError as exc:
        raise AgentNotInstalledError(str(exc)) from exc
    except installer_mod.InstallError as exc:
        raise InstallFailedError(str(exc)) from exc
    finally:
        _release_slot(agent_id)
    installer_mod.clear_progress(agent_id)
    return {"agent_id": agent_id, "status": "uninstalled"}
