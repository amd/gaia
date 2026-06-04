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
from dataclasses import dataclass
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

    Raises:
        InstallInProgressError, ChecksumError, DiskSpaceError,
        CompatibilityError, InstallError.
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
