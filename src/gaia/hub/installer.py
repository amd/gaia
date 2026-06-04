# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Agent Hub install lifecycle: download, verify, install, rollback, uninstall.

This module owns what happens after a user clicks *Install* in the Agent Hub.
It fetches the artifact named in the per-agent manifest (``manifest.json`` →
``versions[v].artifact``), verifies its SHA-256 against the manifest, installs
it under ``~/.gaia/agents/<id>/`` (``uv pip install --target`` for Python
wheels, archive extraction for C++ binaries), records an ``.installed``
sentinel, and hot-registers the agent into the live :class:`AgentRegistry` so it
appears without a server restart.

Safety properties (CLAUDE.md — fail loudly, no silent fallbacks):

* **Checksum verified** before anything is written to the install dir — a
  mismatch raises :class:`ChecksumError` and nothing is installed.
* **Disk + platform checked** up front via
  :func:`gaia.hub.compatibility.check_compatibility`; a hard blocker raises
  before any download.
* **One install per id** — a re-entrant install for the same agent raises
  :class:`InstallInProgressError` (HTTP 409) instead of racing on the same dir.
* **Backup before update** — updating an already-installed agent snapshots the
  current install to ``.backup/<id>/`` first, so :func:`rollback` can restore it.
* **Builtins are immutable** — :func:`uninstall` refuses reserved builtin ids.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gaia.agents.registry import _RESERVED_BUILTIN_IDS
from gaia.hub import catalog as catalog_mod
from gaia.hub.compatibility import check_compatibility
from gaia.logger import get_logger

logger = get_logger(__name__)

# Sentinel file written into each hub-installed agent dir. Its presence marks a
# directory as hub-managed (vs. a hand-authored custom agent with agent.py).
SENTINEL_NAME = ".installed"

# Sub-directory under the install root holding pre-update snapshots for rollback.
BACKUP_DIRNAME = ".backup"

# Where Python wheels get installed (``uv pip install --target``).
SITE_PACKAGES_DIRNAME = "site-packages"

# The only security tier whose native agents install without an explicit trust
# opt-in. ``community`` / ``experimental`` C++ agents require ``trust_native``.
VERIFIED_TIER = "verified"

# Least-privileged default when a manifest omits its tier (mirrors the manifest
# parser: an unknown package is treated as untrusted).
DEFAULT_SECURITY_TIER = "experimental"

# Injectable pip runner: takes the full ``uv pip install`` argument list (after
# ``uv pip install``) and runs it, raising InstallError on failure.
PipRunner = Callable[[List[str]], None]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InstallError(RuntimeError):
    """Base class for install-lifecycle failures (actionable message)."""


class ChecksumError(InstallError):
    """Downloaded artifact's SHA-256 did not match the manifest."""


class DiskSpaceError(InstallError):
    """Not enough free disk space to install the agent."""


class CompatibilityError(InstallError):
    """The current machine does not satisfy the agent's requirements."""


class InstallInProgressError(InstallError):
    """An install for this agent id is already running (maps to HTTP 409)."""


class TrustRequiredError(InstallError):
    """A native (C++) non-verified agent needs explicit trust to install.

    Native agents run as unsandboxed binaries on the user's machine, so a
    ``community``/``experimental`` C++ package is only installed when the caller
    explicitly opts in (``trust_native=True`` / the UI's *Trust & Install*
    confirmation). Maps to HTTP 403 at the router boundary.
    """


class NotInstalledError(InstallError):
    """The agent is not installed (uninstall/rollback target missing)."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def default_install_root() -> Path:
    return Path.home() / ".gaia" / "agents"


def agent_install_dir(agent_id: str, install_root: Optional[Path] = None) -> Path:
    return (install_root or default_install_root()) / agent_id


def _backup_dir(agent_id: str, install_root: Optional[Path] = None) -> Path:
    return (install_root or default_install_root()) / BACKUP_DIRNAME / agent_id


def _sentinel_path(agent_id: str, install_root: Optional[Path] = None) -> Path:
    return agent_install_dir(agent_id, install_root) / SENTINEL_NAME


# ---------------------------------------------------------------------------
# Installed-agent state
# ---------------------------------------------------------------------------


@dataclass
class InstalledAgent:
    """A hub-installed agent recorded by its ``.installed`` sentinel."""

    id: str
    version: str
    language: str
    installed_at: str
    artifact_sha256: str = ""
    path: Optional[Path] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "language": self.language,
            "installed_at": self.installed_at,
            "artifact_sha256": self.artifact_sha256,
            "path": str(self.path) if self.path else None,
        }


def read_sentinel(
    agent_id: str, install_root: Optional[Path] = None
) -> Optional[InstalledAgent]:
    """Return the :class:`InstalledAgent` for *agent_id*, or ``None``."""
    path = _sentinel_path(agent_id, install_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("installer: unreadable sentinel %s: %s", path, exc)
        return None
    return InstalledAgent(
        id=data.get("id", agent_id),
        version=data.get("version", ""),
        language=data.get("language", "python"),
        installed_at=data.get("installed_at", ""),
        artifact_sha256=data.get("artifact_sha256", ""),
        path=agent_install_dir(agent_id, install_root),
    )


def list_installed(install_root: Optional[Path] = None) -> Dict[str, InstalledAgent]:
    """Map ``agent_id -> InstalledAgent`` for every hub-installed agent."""
    root = install_root or default_install_root()
    result: Dict[str, InstalledAgent] = {}
    if not root.exists():
        return result
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name == BACKUP_DIRNAME:
            continue
        if not (child / SENTINEL_NAME).exists():
            continue
        installed = read_sentinel(child.name, install_root)
        if installed is not None:
            result[child.name] = installed
    return result


def installed_versions(install_root: Optional[Path] = None) -> Dict[str, str]:
    """Map ``agent_id -> version`` for hub-installed agents (catalog merge)."""
    return {aid: ia.version for aid, ia in list_installed(install_root).items()}


def is_builtin(agent_id: str) -> bool:
    """Whether *agent_id* is a reserved builtin (immutable; never uninstalled)."""
    return agent_id in _RESERVED_BUILTIN_IDS


# ---------------------------------------------------------------------------
# Concurrency guard (one install per id)
# ---------------------------------------------------------------------------

_GUARD_LOCK = threading.Lock()
_IN_PROGRESS: set = set()


@contextmanager
def _install_slot(agent_id: str):
    with _GUARD_LOCK:
        if agent_id in _IN_PROGRESS:
            raise InstallInProgressError(
                f"An install for '{agent_id}' is already in progress. Wait for it "
                f"to finish (poll GET /api/agents/{agent_id}/install-status)."
            )
        _IN_PROGRESS.add(agent_id)
    try:
        yield
    finally:
        with _GUARD_LOCK:
            _IN_PROGRESS.discard(agent_id)


def is_installing(agent_id: str) -> bool:
    with _GUARD_LOCK:
        return agent_id in _IN_PROGRESS


# ---------------------------------------------------------------------------
# Progress tracking (polling endpoint)
# ---------------------------------------------------------------------------

_PROGRESS_LOCK = threading.Lock()
_PROGRESS: Dict[str, Dict[str, Any]] = {}


def _set_progress(
    agent_id: str,
    *,
    status: str,
    phase: str,
    percent: int,
    version: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    with _PROGRESS_LOCK:
        _PROGRESS[agent_id] = {
            "id": agent_id,
            "status": status,
            "phase": phase,
            "percent": percent,
            "version": version,
            "error": error,
        }


def get_install_status(agent_id: str) -> Optional[Dict[str, Any]]:
    """Return the latest install progress for *agent_id*, or ``None``."""
    with _PROGRESS_LOCK:
        state = _PROGRESS.get(agent_id)
        return dict(state) if state is not None else None


def clear_progress(agent_id: Optional[str] = None) -> None:
    """Clear progress state (test/maintenance hook)."""
    with _PROGRESS_LOCK:
        if agent_id is None:
            _PROGRESS.clear()
        else:
            _PROGRESS.pop(agent_id, None)


# ---------------------------------------------------------------------------
# Download + verify
# ---------------------------------------------------------------------------


def _download_and_verify(
    url: str, expected_sha256: str, fetcher: catalog_mod.Fetcher
) -> bytes:
    data = fetcher(url)
    actual = hashlib.sha256(data).hexdigest()
    if actual != (expected_sha256 or "").lower():
        raise ChecksumError(
            f"Artifact checksum mismatch for {url}: expected {expected_sha256}, "
            f"got {actual}. The download is corrupt or tampered; nothing was "
            f"installed. Try again, or report it if it persists."
        )
    return data


# ---------------------------------------------------------------------------
# Pip runner
# ---------------------------------------------------------------------------


def _default_run_pip(args: List[str]) -> None:
    """Run ``uv pip install <args>``; raise InstallError on failure."""
    cmd = ["uv", "pip", "install", *args]
    try:
        proc = subprocess.run(  # noqa: S603 - args are constructed, not shell
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise InstallError(
            "Could not run 'uv' to install the agent's Python package. Install "
            "uv (https://docs.astral.sh/uv/) or ensure it is on PATH."
        ) from exc
    if proc.returncode != 0:
        raise InstallError(
            f"'uv pip install' failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )


# ---------------------------------------------------------------------------
# Artifact installation
# ---------------------------------------------------------------------------


def _install_python_artifact(
    artifact_bytes: bytes,
    filename: str,
    install_dir: Path,
    run_pip: PipRunner,
) -> Path:
    """Install a Python wheel/sdist into ``install_dir/site-packages``."""
    site_packages = install_dir / SITE_PACKAGES_DIRNAME
    site_packages.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gaia-hub-") as tmp:
        wheel_path = Path(tmp) / filename
        wheel_path.write_bytes(artifact_bytes)
        run_pip(["--target", str(site_packages), str(wheel_path)])
    return site_packages


def _install_cpp_artifact(
    artifact_bytes: bytes, filename: str, install_dir: Path
) -> Path:
    """Extract a C++ agent archive into ``install_dir``."""
    install_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gaia-hub-") as tmp:
        archive_path = Path(tmp) / filename
        archive_path.write_bytes(artifact_bytes)
        lower = filename.lower()
        if lower.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(install_dir)
        elif lower.endswith((".tar.gz", ".tgz", ".tar")):
            with tarfile.open(archive_path) as tf:
                tf.extractall(install_dir)  # noqa: S202 - hub artifacts are trusted
        else:
            raise InstallError(
                f"Unsupported C++ artifact format '{filename}'. Expected a .zip "
                f"or .tar.gz archive."
            )
    return install_dir


# ---------------------------------------------------------------------------
# Version resolution
# ---------------------------------------------------------------------------


def _resolve_version(manifest: Dict[str, Any], version: Optional[str]):
    versions = manifest.get("versions") or {}
    if not versions:
        raise InstallError(
            f"Hub manifest for '{manifest.get('id')}' has no published versions."
        )
    if version is None:
        version = manifest.get("latest_version")
        if not version or version not in versions:
            version = max(versions, key=catalog_mod._parse_version)
    if version not in versions:
        raise InstallError(
            f"Version '{version}' of '{manifest.get('id')}' is not published. "
            f"Available: {', '.join(sorted(versions))}."
        )
    entry = versions[version]
    artifact = entry.get("artifact") or {}
    if not artifact.get("path") or not artifact.get("sha256"):
        raise InstallError(
            f"Hub manifest version '{version}' of '{manifest.get('id')}' is "
            f"missing its artifact path or checksum."
        )
    return version, artifact


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------


def _snapshot_backup(agent_id: str, install_root: Path) -> None:
    """Move the current install dir to ``.backup/<id>/`` before an update."""
    install_dir = agent_install_dir(agent_id, install_root)
    if not install_dir.exists():
        return
    backup = _backup_dir(agent_id, install_root)
    if backup.exists():
        shutil.rmtree(backup)
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(install_dir), str(backup))
    logger.info("installer: snapshotted %s -> %s", install_dir, backup)


# ---------------------------------------------------------------------------
# Hot-register
# ---------------------------------------------------------------------------


def _hot_register(agent_id: str, install_dir: Path, language: str, registry) -> bool:
    """Register a freshly-installed Python agent into the live registry.

    Adds the install's ``site-packages`` to ``sys.path`` so entry-point
    discovery can find the new distribution, then asks the registry to rescan.
    Returns whether the agent is now registered. Best-effort: a failure here
    does not undo the on-disk install (the agent loads after a restart).
    """
    if language != "python":
        logger.info(
            "installer: %s is a native agent; it registers via the Electron "
            "process manager, not in-process",
            agent_id,
        )
        return False
    if registry is None:
        return False
    site_packages = install_dir / SITE_PACKAGES_DIRNAME
    try:
        sp = str(site_packages)
        if sp not in sys.path:
            sys.path.insert(0, sp)
        import importlib

        importlib.invalidate_caches()
        registry.discover_installed_agents()
    except Exception as exc:  # noqa: BLE001 - secondary effect; log, don't fail
        logger.warning("installer: hot-register failed for %s: %s", agent_id, exc)
        return False
    return registry.get(agent_id) is not None


# ---------------------------------------------------------------------------
# Security tier / native-agent trust
# ---------------------------------------------------------------------------


def requires_native_trust(manifest: Dict[str, Any]) -> bool:
    """Whether installing *manifest* needs an explicit native-trust opt-in.

    True for native (``language: cpp``) agents that are not in the ``verified``
    tier — they ship an unsandboxed binary from a non-AMD-audited publisher.
    """
    language = manifest.get("language", "python")
    tier = manifest.get("security_tier", DEFAULT_SECURITY_TIER)
    return language == "cpp" and tier != VERIFIED_TIER


def ensure_native_trust(
    agent_id: str, manifest: Dict[str, Any], *, trust_native: bool
) -> None:
    """Raise :class:`TrustRequiredError` if native trust is needed but absent.

    No-op for Python agents and ``verified`` native agents. Called both by the
    router (synchronous 403) and :func:`install` (defense in depth).
    """
    if trust_native or not requires_native_trust(manifest):
        return
    tier = manifest.get("security_tier", DEFAULT_SECURITY_TIER)
    raise TrustRequiredError(
        f"'{agent_id}' is a native (C++) agent in the '{tier}' security tier. "
        f"Native agents run as unsandboxed binaries on your machine, so this one "
        f"is not installed automatically. Re-install with explicit trust "
        f"(trust_native=true, or the UI's 'Trust & Install' confirmation) only if "
        f"you trust its publisher. See "
        f"https://amd-gaia.ai/docs/spec/agent-hub-restructure."
    )


# ---------------------------------------------------------------------------
# Public: install
# ---------------------------------------------------------------------------


@dataclass
class InstallResult:
    """Outcome of a successful :func:`install`."""

    id: str
    version: str
    language: str
    path: Path
    updated: bool
    hot_registered: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "language": self.language,
            "path": str(self.path),
            "updated": self.updated,
            "hot_registered": self.hot_registered,
        }


def install(
    agent_id: str,
    *,
    version: Optional[str] = None,
    manifest: Optional[Dict[str, Any]] = None,
    base_url: Optional[str] = None,
    fetcher: Optional[catalog_mod.Fetcher] = None,
    run_pip: Optional[PipRunner] = None,
    install_root: Optional[Path] = None,
    registry: Any = None,
    skip_compatibility_check: bool = False,
    trust_native: bool = False,
) -> InstallResult:
    """Download, verify, and install an agent from the hub.

    Args:
        agent_id: Hub agent id to install.
        version: Specific version to install; defaults to the manifest's latest.
        manifest: Pre-fetched per-agent manifest; fetched from the hub if absent.
        base_url / fetcher: Hub origin + HTTP fetcher (injectable for tests).
        run_pip: ``uv pip install`` runner (injectable for tests).
        install_root: Install root; defaults to ``~/.gaia/agents``.
        registry: Live :class:`AgentRegistry` to hot-register into.
        skip_compatibility_check: Skip the platform/disk gate (tests/forced).
        trust_native: Explicit opt-in to install a non-verified native (C++)
            agent. Required for ``community``/``experimental`` C++ packages.

    Raises:
        InstallInProgressError, ChecksumError, DiskSpaceError,
        CompatibilityError, TrustRequiredError, InstallError.
    """
    fetcher = fetcher or catalog_mod.fetch_bytes
    run_pip = run_pip or _default_run_pip
    root = install_root or default_install_root()
    base_url = (base_url or catalog_mod.get_hub_base_url()).rstrip("/")

    with _install_slot(agent_id):
        _set_progress(
            agent_id, status="running", phase="resolving", percent=5, version=version
        )
        try:
            if manifest is None:
                manifest = catalog_mod.fetch_manifest(
                    agent_id, base_url=base_url, fetcher=fetcher
                )
            language = manifest.get("language", "python")

            # Native-agent trust gate — refuse non-verified C++ packages unless
            # the caller explicitly opted in (defense in depth; the router also
            # enforces this synchronously for a clean 403).
            ensure_native_trust(agent_id, manifest, trust_native=trust_native)

            # Deprecation is non-fatal but must be loud: a deprecated agent may
            # be unmaintained or superseded.
            if manifest.get("deprecated"):
                logger.warning(
                    "installer: '%s' is deprecated and may be unmaintained or "
                    "superseded; installing anyway",
                    agent_id,
                )

            resolved_version, artifact = _resolve_version(manifest, version)

            # --- compatibility / disk gate ---
            _set_progress(
                agent_id,
                status="running",
                phase="checking",
                percent=10,
                version=resolved_version,
            )
            if not skip_compatibility_check:
                report = check_compatibility(
                    manifest.get("requirements"),
                    download_size_bytes=artifact.get("size_bytes", 0),
                    install_dir=root,
                )
                if not report.compatible:
                    blockers = "; ".join(report.blockers)
                    if not report.disk_ok:
                        raise DiskSpaceError(blockers)
                    raise CompatibilityError(blockers)

            # --- download + verify ---
            _set_progress(
                agent_id,
                status="running",
                phase="downloading",
                percent=30,
                version=resolved_version,
            )
            artifact_url = f"{base_url}/{artifact['path']}"
            artifact_bytes = _download_and_verify(
                artifact_url, artifact["sha256"], fetcher
            )

            _set_progress(
                agent_id,
                status="running",
                phase="installing",
                percent=60,
                version=resolved_version,
            )

            # --- backup before update ---
            updated = read_sentinel(agent_id, root) is not None
            if updated:
                _snapshot_backup(agent_id, root)

            install_dir = agent_install_dir(agent_id, root)
            try:
                install_dir.mkdir(parents=True, exist_ok=True)
                if language == "python":
                    _install_python_artifact(
                        artifact_bytes, artifact["filename"], install_dir, run_pip
                    )
                else:
                    _install_cpp_artifact(
                        artifact_bytes, artifact["filename"], install_dir
                    )
                _write_agent_yaml(
                    agent_id, resolved_version, install_dir, base_url, fetcher
                )
                _write_sentinel(
                    agent_id,
                    resolved_version,
                    language,
                    artifact["sha256"],
                    install_dir,
                )
            except Exception:
                # Install failed mid-write — restore the backup if we made one so
                # the user is left with a working previous version, not a stub.
                _restore_backup_if_present(agent_id, root)
                raise

            # --- hot-register ---
            _set_progress(
                agent_id,
                status="running",
                phase="registering",
                percent=90,
                version=resolved_version,
            )
            hot = _hot_register(agent_id, install_dir, language, registry)

            # NOTE: the backup snapshot is intentionally retained after a
            # successful update so rollback() can restore the prior version. It
            # is overwritten by the next update's snapshot and cleared on
            # uninstall.

            _set_progress(
                agent_id,
                status="completed",
                phase="completed",
                percent=100,
                version=resolved_version,
            )
            return InstallResult(
                id=agent_id,
                version=resolved_version,
                language=language,
                path=install_dir,
                updated=updated,
                hot_registered=hot,
            )
        except InstallInProgressError:
            raise
        except Exception as exc:
            _set_progress(
                agent_id,
                status="failed",
                phase="failed",
                percent=0,
                version=version,
                error=str(exc),
            )
            raise


def _write_sentinel(
    agent_id: str,
    version: str,
    language: str,
    sha256: str,
    install_dir: Path,
) -> None:
    sentinel = InstalledAgent(
        id=agent_id,
        version=version,
        language=language,
        installed_at=datetime.now(timezone.utc).isoformat(),
        artifact_sha256=sha256,
        path=install_dir,
    )
    (install_dir / SENTINEL_NAME).write_text(
        json.dumps(sentinel.to_dict(), indent=2), encoding="utf-8"
    )


def _write_agent_yaml(
    agent_id: str,
    version: str,
    install_dir: Path,
    base_url: str,
    fetcher: catalog_mod.Fetcher,
) -> None:
    """Fetch the version's ``gaia-agent.yaml`` into the install dir.

    Best-effort: the manifest already carries identity, so a missing yaml does
    not fail the install — but we log it so a broken hub object is visible.
    """
    url = f"{base_url}/agents/{agent_id}/{version}/gaia-agent.yaml"
    try:
        data = fetcher(url)
    except Exception as exc:  # noqa: BLE001 - optional asset
        logger.warning("installer: could not fetch %s: %s", url, exc)
        return
    (install_dir / "gaia-agent.yaml").write_bytes(data)


# ---------------------------------------------------------------------------
# Public: uninstall
# ---------------------------------------------------------------------------


def uninstall(
    agent_id: str,
    *,
    install_root: Optional[Path] = None,
    registry: Any = None,
) -> None:
    """Remove a hub-installed agent. Refuses builtins.

    Raises:
        InstallError: If *agent_id* is a reserved builtin.
        NotInstalledError: If the agent is not installed.
    """
    if is_builtin(agent_id):
        raise InstallError(
            f"'{agent_id}' is a built-in GAIA agent and cannot be uninstalled."
        )
    root = install_root or default_install_root()
    install_dir = agent_install_dir(agent_id, root)
    if read_sentinel(agent_id, root) is None:
        raise NotInstalledError(
            f"'{agent_id}' is not installed (no {SENTINEL_NAME} at {install_dir})."
        )
    shutil.rmtree(install_dir)
    _discard_backup(agent_id, root)
    if registry is not None:
        _deregister(agent_id, registry)
    logger.info("installer: uninstalled %s", agent_id)


# ---------------------------------------------------------------------------
# Public: rollback
# ---------------------------------------------------------------------------


def rollback(
    agent_id: str,
    *,
    install_root: Optional[Path] = None,
    registry: Any = None,
) -> InstalledAgent:
    """Restore the pre-update snapshot from ``.backup/<id>/``.

    Raises:
        InstallError: If there is no backup to restore.
    """
    root = install_root or default_install_root()
    backup = _backup_dir(agent_id, root)
    if not backup.exists():
        raise InstallError(
            f"No backup to roll back to for '{agent_id}'. A backup is only "
            f"created when updating an already-installed agent."
        )
    install_dir = agent_install_dir(agent_id, root)
    if install_dir.exists():
        shutil.rmtree(install_dir)
    shutil.move(str(backup), str(install_dir))
    logger.info("installer: rolled back %s from backup", agent_id)

    restored = read_sentinel(agent_id, root)
    if restored is None:
        raise InstallError(
            f"Rollback for '{agent_id}' restored a directory with no "
            f"{SENTINEL_NAME} sentinel; the backup is corrupt."
        )
    if registry is not None:
        _hot_register(agent_id, install_dir, restored.language, registry)
    return restored


# ---------------------------------------------------------------------------
# Backup helpers
# ---------------------------------------------------------------------------


def _discard_backup(agent_id: str, install_root: Path) -> None:
    backup = _backup_dir(agent_id, install_root)
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def _restore_backup_if_present(agent_id: str, install_root: Path) -> None:
    backup = _backup_dir(agent_id, install_root)
    if not backup.exists():
        return
    install_dir = agent_install_dir(agent_id, install_root)
    if install_dir.exists():
        shutil.rmtree(install_dir, ignore_errors=True)
    shutil.move(str(backup), str(install_dir))
    logger.info("installer: restored %s from backup after failed install", agent_id)


def _deregister(agent_id: str, registry: Any) -> None:
    """Remove an agent from the live registry (best-effort)."""
    try:
        # pylint: disable=protected-access
        with registry._lock:  # noqa: SLF001
            registry._agents.pop(agent_id, None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("installer: could not deregister %s: %s", agent_id, exc)


# ---------------------------------------------------------------------------
# Setup executor: progressive, resumable, parallel multi-agent install (#468)
# ---------------------------------------------------------------------------
#
# ``run_setup`` installs several agents in one pass with three properties the
# single-agent ``install`` does not provide on its own:
#
# * **Progressive** — steps run smallest-download-first so a minimal agent is
#   usable while larger ones keep downloading.
# * **Resumable** — every step transition is persisted to a JSON state file
#   (``~/.gaia/setup_state.json``). A crashed/interrupted run re-reads it and
#   skips already-``completed`` steps instead of re-downloading them.
# * **Parallel** — downloads run with *bounded* concurrency (independent agents
#   are safe to fetch at once); a failed step does not block the others.

# Step lifecycle states for the resumable setup state file.
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_COMPLETED = "completed"
STEP_FAILED = "failed"

# Default parallel-download bound. Conservative: enough to overlap a small fast
# download with a large slow one without saturating a typical home connection.
DEFAULT_MAX_PARALLEL = 2


def default_setup_state_path(install_root: Optional[Path] = None) -> Path:
    """Path of the resumable setup state file.

    Lives next to the install root's parent (``~/.gaia/setup_state.json``) so it
    survives across the per-agent install dirs it coordinates.
    """
    root = install_root or default_install_root()
    return root.parent / "setup_state.json"


@dataclass
class SetupStep:
    """One agent install within a :func:`run_setup` plan."""

    agent_id: str
    version: Optional[str] = None
    size_bytes: int = 0
    status: str = STEP_PENDING
    error: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "version": self.version,
            "size_bytes": self.size_bytes,
            "status": self.status,
            "error": self.error,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SetupStep":
        return cls(
            agent_id=data["agent_id"],
            version=data.get("version"),
            size_bytes=data.get("size_bytes", 0),
            status=data.get("status", STEP_PENDING),
            error=data.get("error"),
            completed_at=data.get("completed_at"),
        )


@dataclass
class SetupResult:
    """Outcome of :func:`run_setup`."""

    steps: List[SetupStep] = field(default_factory=list)

    @property
    def completed(self) -> List[str]:
        return [s.agent_id for s in self.steps if s.status == STEP_COMPLETED]

    @property
    def failed(self) -> List[str]:
        return [s.agent_id for s in self.steps if s.status == STEP_FAILED]

    @property
    def all_ok(self) -> bool:
        return all(s.status == STEP_COMPLETED for s in self.steps)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "completed": self.completed,
            "failed": self.failed,
            "all_ok": self.all_ok,
        }


def _artifact_size(manifest: Dict[str, Any], version: Optional[str]) -> int:
    """Best-effort download size for ordering (0 if it can't be resolved)."""
    try:
        _, artifact = _resolve_version(manifest, version)
        return int(artifact.get("size_bytes", 0) or 0)
    except InstallError:
        return 0


def read_setup_state(path: Path) -> Optional[Dict[str, Any]]:
    """Read the resumable setup state file, or ``None`` if absent/corrupt."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("installer: unreadable setup state %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _write_setup_state(path: Path, steps: List[SetupStep]) -> None:
    payload = {
        "status": (
            STEP_COMPLETED
            if all(s.status == STEP_COMPLETED for s in steps)
            else STEP_RUNNING
        ),
        "steps": [s.to_dict() for s in steps],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        # A state-write failure must not abort an otherwise-fine install, but it
        # does break resume — so it is logged loudly, not swallowed silently.
        logger.warning("installer: could not write setup state %s: %s", path, exc)


def run_setup(
    manifests: Dict[str, Dict[str, Any]],
    *,
    versions: Optional[Dict[str, Optional[str]]] = None,
    max_parallel: int = DEFAULT_MAX_PARALLEL,
    resume: bool = True,
    state_path: Optional[Path] = None,
    install_root: Optional[Path] = None,
    fetcher: Optional[catalog_mod.Fetcher] = None,
    run_pip: Optional[PipRunner] = None,
    registry: Any = None,
    skip_compatibility_check: bool = False,
    installer_fn: Optional[Callable[..., InstallResult]] = None,
) -> SetupResult:
    """Install several agents progressively, resumably, and in parallel.

    Args:
        manifests: ``{agent_id: manifest}`` for every agent to install. Passing
            manifests in keeps this function offline-testable (no catalog fetch).
        versions: Optional ``{agent_id: version}`` overrides (default: latest).
        max_parallel: Bound on concurrent installs (must be >= 1).
        resume: When True, completed steps recorded in *state_path* are skipped.
        state_path: Resumable state file (default: ``~/.gaia/setup_state.json``).
        install_root: Install root passed through to each :func:`install`.
        fetcher / run_pip / registry / skip_compatibility_check: forwarded to
            :func:`install`.
        installer_fn: Injectable install callable (defaults to :func:`install`);
            tests use it to assert the concurrency bound without real downloads.

    Returns:
        A :class:`SetupResult` listing each step's final status. A failed step
        does not abort the others (independent agents stay independent).
    """
    if max_parallel < 1:
        raise InstallError("max_parallel must be >= 1.")
    versions = versions or {}
    install_fn = installer_fn or install
    state_path = state_path or default_setup_state_path(install_root)

    # Build the plan: smallest download first so a minimal agent is usable while
    # larger ones keep going (progressive capability unlock, #468).
    steps = [
        SetupStep(
            agent_id=aid,
            version=versions.get(aid),
            size_bytes=_artifact_size(manifest, versions.get(aid)),
        )
        for aid, manifest in manifests.items()
    ]
    steps.sort(key=lambda s: (s.size_bytes, s.agent_id))

    # Resume: mark steps already completed in a prior run so we skip them.
    if resume:
        prior = read_setup_state(state_path)
        if prior:
            done = {
                s["agent_id"]
                for s in prior.get("steps", [])
                if s.get("status") == STEP_COMPLETED
            }
            for step in steps:
                if step.agent_id in done:
                    step.status = STEP_COMPLETED

    _write_setup_state(state_path, steps)

    state_lock = threading.Lock()

    def _persist() -> None:
        with state_lock:
            _write_setup_state(state_path, steps)

    def _run_step(step: SetupStep) -> None:
        if step.status == STEP_COMPLETED:
            logger.info("installer: setup skipping completed step %s", step.agent_id)
            return
        with state_lock:
            step.status = STEP_RUNNING
        _persist()
        try:
            install_fn(
                step.agent_id,
                version=step.version,
                manifest=manifests[step.agent_id],
                fetcher=fetcher,
                run_pip=run_pip,
                install_root=install_root,
                registry=registry,
                skip_compatibility_check=skip_compatibility_check,
            )
        except Exception as exc:  # noqa: BLE001 - recorded per-step; others go on
            with state_lock:
                step.status = STEP_FAILED
                step.error = str(exc)
            _persist()
            logger.warning("installer: setup step %s failed: %s", step.agent_id, exc)
            return
        with state_lock:
            step.status = STEP_COMPLETED
            step.completed_at = datetime.now(timezone.utc).isoformat()
        _persist()

    pending = [s for s in steps if s.status != STEP_COMPLETED]
    if pending:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            list(pool.map(_run_step, pending))

    _persist()
    return SetupResult(steps=steps)


def get_setup_status(install_root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Return the latest persisted setup state (for the polling endpoint)."""
    return read_setup_state(default_setup_state_path(install_root))
