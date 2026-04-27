# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Agent bundle export / import.

Produces and consumes ``.zip`` archives of custom agents living under
``~/.gaia/agents/``. The archive contains a ``bundle.json`` table of
contents plus one subdirectory per agent.

This module lives in ``gaia.installer`` (not ``gaia.agents``) because the
agents package owns runtime and registry concerns, not archive
serialization.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from gaia.logger import get_logger
from gaia.version import __version__

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUNDLE_JSON_NAME = "bundle.json"
BUNDLE_FORMAT_VERSION = 1

# Extraction limits (zip-bomb / resource-abuse guards).
MAX_ENTRIES = 1000
MAX_UNCOMPRESSED_TOTAL = 500 * 1024 * 1024  # 500 MB
MAX_UNCOMPRESSED_PER_FILE = 50 * 1024 * 1024  # 50 MB

# Same regex as AgentManifest.validate_id in gaia.agents.registry.
_AGENT_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,50}[a-z0-9])?$")

# Reserved Windows device names (case-insensitive).
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ExportResult:
    """Return value of :func:`export_custom_agents`."""

    output_path: Path
    agent_ids: List[str]


@dataclass
class ImportResult:
    """Return value of :func:`import_agent_bundle`."""

    imported: List[str] = field(default_factory=list)
    overwritten: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agents_root() -> Path:
    """Return the resolved ``~/.gaia/agents`` path."""
    return (Path.home() / ".gaia" / "agents").resolve()


def _is_custom_agent_dir(path: Path) -> bool:
    """A directory qualifies as a custom agent if it holds agent.py or agent.yaml."""
    return path.is_dir() and (
        (path / "agent.py").is_file() or (path / "agent.yaml").is_file()
    )


def _validate_agent_id(agent_id: str) -> None:
    """Raise ValueError if the agent id is unsafe or malformed."""
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError("Agent ID cannot be empty")
    if "/" in agent_id or "\\" in agent_id or ".." in agent_id:
        raise ValueError(f"Agent ID '{agent_id}' contains path separators")
    if agent_id.upper() in _RESERVED_WINDOWS_NAMES:
        raise ValueError(f"Agent ID '{agent_id}' is a reserved Windows device name")
    if not _AGENT_ID_RE.match(agent_id):
        raise ValueError(
            f"Agent ID '{agent_id}' is invalid. "
            "Use lowercase letters, digits, and hyphens (e.g. 'my-agent')."
        )


def _iso_now() -> str:
    """Return an ISO-8601 UTC timestamp with trailing Z."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_custom_agents(output_path: Path) -> ExportResult:
    """Export every custom agent under ``~/.gaia/agents/`` into a zip bundle.

    Args:
        output_path: Destination path for the ``.zip`` file.

    Returns:
        :class:`ExportResult` with the path written and the agent IDs included.

    Raises:
        ValueError: If there are no custom agents to export.
    """
    output_path = Path(output_path)
    agents_root = _agents_root()

    if not agents_root.exists():
        raise ValueError("No custom agents found to export")

    agent_dirs = sorted(
        (d for d in agents_root.iterdir() if _is_custom_agent_dir(d)),
        key=lambda p: p.name,
    )
    if not agent_dirs:
        raise ValueError("No custom agents found to export")

    agent_ids = [d.name for d in agent_dirs]

    manifest = {
        "format_version": BUNDLE_FORMAT_VERSION,
        "exported_at": _iso_now(),
        "gaia_version": __version__,
        "agent_ids": agent_ids,
    }

    # Write to a temp file in the same directory so the final os.replace is atomic
    # across the same filesystem. This prevents a partial write from corrupting an
    # existing export.zip on failure.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=".gaia-export-", suffix=".zip", dir=str(output_path.parent)
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)

    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(BUNDLE_JSON_NAME, json.dumps(manifest, indent=2))
            for agent_dir in agent_dirs:
                for file_path in sorted(agent_dir.rglob("*")):
                    if not file_path.is_file():
                        continue
                    arcname = f"{agent_dir.name}/{file_path.relative_to(agent_dir).as_posix()}"
                    zf.write(file_path, arcname=arcname)
        os.replace(tmp_path, output_path)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise

    log.info("Exported %d custom agent(s) to %s", len(agent_ids), output_path)
    return ExportResult(output_path=output_path, agent_ids=agent_ids)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


def _validate_zip_entries(infos: List[zipfile.ZipInfo], agents_root: Path) -> None:
    """Enforce size, entry-count, symlink, and path-traversal guards.

    Raises:
        ValueError: On any policy violation.
    """
    if len(infos) > MAX_ENTRIES:
        raise ValueError(f"bundle has too many entries: {len(infos)} > {MAX_ENTRIES}")

    total_uncompressed = sum(i.file_size for i in infos)
    if total_uncompressed > MAX_UNCOMPRESSED_TOTAL:
        raise ValueError(
            f"bundle uncompressed size {total_uncompressed} exceeds limit "
            f"{MAX_UNCOMPRESSED_TOTAL}"
        )

    for info in infos:
        name = info.filename

        if info.file_size > MAX_UNCOMPRESSED_PER_FILE:
            raise ValueError(
                f"entry {name} exceeds per-file limit "
                f"({info.file_size} > {MAX_UNCOMPRESSED_PER_FILE})"
            )

        # Reject symlinks (stored in upper 16 bits of external_attr on unix zips).
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError(f"symlink entries not allowed: {name}")

        # Reject absolute paths and Windows drive letters (e.g. "C:/foo").
        if name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
            raise ValueError(f"absolute paths not allowed: {name}")

        # Skip the bundle manifest: it is not written to disk.
        if name == BUNDLE_JSON_NAME:
            continue

        # Directory entries are fine as long as they resolve inside the root.
        dest = (agents_root / name).resolve()
        if dest == agents_root:
            continue
        if not dest.is_relative_to(agents_root):
            raise ValueError(f"path traversal blocked: {name}")


def _read_bundle_manifest(zf: zipfile.ZipFile) -> dict:
    """Load and validate ``bundle.json`` from the archive."""
    try:
        raw = zf.read(BUNDLE_JSON_NAME)
    except KeyError as exc:
        raise ValueError("bundle is missing bundle.json") from exc

    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"bundle.json is not valid JSON: {exc}") from exc

    if not isinstance(manifest, dict):
        raise ValueError("bundle.json must be a JSON object")

    fmt = manifest.get("format_version")
    if fmt != BUNDLE_FORMAT_VERSION:
        raise ValueError(
            f"unsupported bundle format_version: {fmt!r} "
            f"(expected {BUNDLE_FORMAT_VERSION})"
        )

    agent_ids = manifest.get("agent_ids")
    if not isinstance(agent_ids, list) or not all(
        isinstance(a, str) for a in agent_ids
    ):
        raise ValueError("bundle.json 'agent_ids' must be a list of strings")

    return manifest


def import_agent_bundle(bundle_path: Path) -> ImportResult:
    """Import an exported bundle into ``~/.gaia/agents/`` and hot-register.

    Overwrites any existing agent directory of the same ID. Partial failures
    leave previously-installed agents intact (each agent is staged in a temp
    dir and atomically moved into place).

    Args:
        bundle_path: Path to the ``.zip`` file produced by
            :func:`export_custom_agents`.

    Returns:
        :class:`ImportResult` listing imported / overwritten / errored IDs.

    Raises:
        ValueError: On malformed bundle or security-policy violation.
    """
    bundle_path = Path(bundle_path)
    agents_root = _agents_root()
    agents_root.mkdir(parents=True, exist_ok=True)

    result = ImportResult()

    with zipfile.ZipFile(bundle_path) as zf:
        infos = zf.infolist()
        _validate_zip_entries(infos, agents_root)
        manifest = _read_bundle_manifest(zf)
        agent_ids = manifest["agent_ids"]

        # Validate every declared agent id BEFORE touching the filesystem.
        for agent_id in agent_ids:
            _validate_agent_id(agent_id)

        # Stage each agent in a temp dir, then move into place atomically.
        with tempfile.TemporaryDirectory(
            prefix=".gaia-import-", dir=str(agents_root)
        ) as staging_root_str:
            staging_root = Path(staging_root_str)

            # Extract every non-bundle.json entry into staging_root.
            # Track aggregate bytes written to catch zip-bombs that spread
            # across many entries, each staying under the per-file cap.
            total_written = 0
            for info in infos:
                if info.filename == BUNDLE_JSON_NAME:
                    continue
                # Defensive: re-check destination lies under staging_root.
                dest = (staging_root / info.filename).resolve()
                if dest != staging_root and not dest.is_relative_to(staging_root):
                    raise ValueError(
                        f"path traversal blocked during extract: {info.filename}"
                    )
                if info.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                bytes_written = 0
                with zf.open(info) as src, open(dest, "wb") as out:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        bytes_written += len(chunk)
                        total_written += len(chunk)
                        if bytes_written > MAX_UNCOMPRESSED_PER_FILE:
                            raise ValueError(
                                f"entry {info.filename} exceeds per-file limit "
                                f"during extraction"
                            )
                        if total_written > MAX_UNCOMPRESSED_TOTAL:
                            raise ValueError(
                                "bundle exceeds total uncompressed size limit "
                                "during extraction"
                            )
                        out.write(chunk)

            # Move each staged agent dir to its final location.
            for agent_id in agent_ids:
                staged_dir = staging_root / agent_id
                if not staged_dir.is_dir():
                    result.errors.append(
                        f"{agent_id}: bundle declared this agent but no "
                        f"directory was present in the archive"
                    )
                    continue

                final_dir = agents_root / agent_id
                existed = final_dir.exists()
                if existed:
                    # Move existing to a sibling temp dir so we can restore on failure.
                    backup_dir = staging_root / f".backup-{agent_id}"
                    os.replace(final_dir, backup_dir)
                try:
                    os.replace(staged_dir, final_dir)
                except Exception as exc:
                    # Attempt rollback.
                    if existed and (staging_root / f".backup-{agent_id}").exists():
                        try:
                            os.replace(staging_root / f".backup-{agent_id}", final_dir)
                        except OSError:
                            pass
                    # Log full exception detail server-side; surface only a
                    # generic message to the caller so OS-level paths and
                    # implementation details do not leak into HTTP responses
                    # (CodeQL py/stack-trace-exposure).
                    log.warning(
                        "Failed to move staged agent %s into place: %s",
                        agent_id,
                        exc,
                    )
                    result.errors.append(
                        f"{agent_id}: failed to move to final location "
                        f"(see server logs)"
                    )
                    continue

                if existed:
                    result.overwritten.append(agent_id)
                result.imported.append(agent_id)

    log.info(
        "Imported %d agent(s), overwrote %d, errors=%d",
        len(result.imported),
        len(result.overwritten),
        len(result.errors),
    )
    return result
