# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
McpServerHandler — ConnectorHandler implementation for ``type="mcp_server"``.

Manages MCP server connectors: stores secret env-var values in the OS keyring
under ``$keyring`` references, writes ``~/.gaia/mcp_servers.json`` atomically,
and signals ``MCPClientManager.reload()`` so new tools materialize without
restarting GAIA (plan amendment A5).

Keyring storage layout:
  - Service: ``gaia.connections`` (same service as OAuth tokens, per A3)
  - Username: ``<connector_id>:<env_key>``  (e.g. ``"github:GITHUB_TOKEN"``)

``mcp_servers.json`` env block uses ``$keyring`` references (plan amendment A4):
  ``{"env": {"GITHUB_TOKEN": {"$keyring": "gaia.connections:github:GITHUB_TOKEN"}}}``
``MCPClient.from_config()`` resolves references at spawn time and fails closed
if a referenced keyring entry is missing (plan amendment A5b).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import keyring

from gaia.connectors.errors import ConnectorsError
from gaia.connectors.handler import register_handler
from gaia.connectors.spec import ConnectorSpec
from gaia.connectors.store import SERVICE_NAME

logger = logging.getLogger(__name__)

# Path to the MCP server config file read by MCPClient.
_MCP_SERVERS_FILE = Path.home() / ".gaia" / "mcp_servers.json"


def _mcp_servers_path() -> Path:
    """Resolve on each call so tests can monkeypatch ``Path.home``."""
    return Path.home() / ".gaia" / "mcp_servers.json"


def _keyring_ref(connector_id: str, env_key: str) -> str:
    """Return the ``$keyring`` reference string for a given env key."""
    return f"{SERVICE_NAME}:{connector_id}:{env_key}"


def _write_mcp_servers_json(servers: Dict[str, Any]) -> None:
    """Atomically overwrite ``mcp_servers.json`` with *servers* dict.

    File is written mode 0600 (parent 0700). ``os.replace`` would otherwise
    inherit the destination's prior mode (or umask for new files), which on a
    multi-user machine leaves any plaintext env value (custom MCP entries
    that escape the keyring path, ChatAgent dynamic registrations, etc.)
    world-readable.
    """
    path = _mcp_servers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:  # Windows or read-only mount
        pass
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mcp_servers_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": servers}, f, indent=2)
            f.write("\n")
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # Windows
            pass
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_mcp_servers_json() -> Dict[str, Any]:
    """Return the servers dict from ``mcp_servers.json``, or {} if missing."""
    path = _mcp_servers_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("mcpServers", data.get("servers", {}))
    except (json.JSONDecodeError, OSError) as e:
        raise ConnectorsError(
            f"mcp_servers.json at {path} is unreadable: {e}. "
            "Delete to reset or fix the JSON."
        ) from e


def is_mcp_server_configured(connector_id: str) -> bool:
    """
    True if ``connector_id`` has an entry in ``mcp_servers.json``.

    Source-of-truth lookup for the catalog UI / `gaia connectors list` —
    no separate state cache is maintained for MCP servers; the file
    written by ``configure`` is itself the configured-state ledger. A
    corrupt mcp_servers.json bubbles up as ``ConnectorsError`` so the
    UI can show an actionable error rather than a silent "not configured".
    """
    return connector_id in _read_mcp_servers_json()


class McpServerHandler:
    """
    Handles ``type="mcp_server"`` connectors.

    ``get_credential`` resolves keyring refs and returns an env dict.
    ``configure`` stores secret env values in keyring and writes
    ``mcp_servers.json`` with ``$keyring`` placeholders.
    ``disconnect`` removes the entry from ``mcp_servers.json`` and deletes
    keyring slots.

    The handler accepts an optional *reload_callback* that is called after
    ``configure`` and ``disconnect`` so the live ``MCPClientManager``
    instance can reload without restarting GAIA (plan amendment A5).
    """

    def __init__(self, reload_callback: Optional[Callable[[], None]] = None) -> None:
        self._reload = reload_callback

    def set_reload_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """Attach (or replace) the reload callback after construction.

        The handler singleton is registered at module-import time, well before
        the FastAPI app's ``MCPClientManager`` exists. The lifespan startup
        hook calls this setter to wire ``manager.reload`` so a subsequent
        ``configure`` / ``disconnect`` / ``set_enabled`` makes new tools
        materialize without restarting GAIA (#1004).
        """
        self._reload = callback

    # ------------------------------------------------------------------
    # ConnectorHandler Protocol implementation
    # ------------------------------------------------------------------

    async def get_credential(  # pylint: disable=unused-argument
        self,
        spec: ConnectorSpec,
        *,
        required_scopes: Optional[List[str]] = None,
        account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Return resolved env-var values for the MCP server.

        Resolves every key in ``spec.mcp_env_keys`` from the keyring.
        Raises ``ConnectorsError`` if any key is missing (fail-closed).
        """
        env: Dict[str, str] = {}
        missing: List[str] = []
        for env_key in spec.mcp_env_keys:
            username = f"{spec.id}:{env_key}"
            value = keyring.get_password(SERVICE_NAME, username)
            if value is None:
                missing.append(f"{SERVICE_NAME}:{username}")
            else:
                env[env_key] = value

        if missing:
            raise ConnectorsError(
                f"MCP server connector '{spec.id}' has missing keyring entries: "
                f"{missing!r}. Reconfigure via Settings → Connectors or "
                f"`gaia connectors configure {spec.id}`."
            )

        return {
            "env": env,
            "command": spec.mcp_command,
            "args": list(spec.mcp_args),
        }

    async def configure(
        self,
        spec: ConnectorSpec,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Store env-var values in keyring and write ``mcp_servers.json``.

        ``config`` must contain a value for every key in ``spec.mcp_env_keys``.
        Plain (non-secret) env values not in ``mcp_env_keys`` are written
        directly to ``mcp_servers.json`` (not to the keyring).

        After writing, calls the reload callback (if registered) so running
        agents pick up new tools without restart.
        """
        # Validate all required env keys are supplied.
        missing_keys = [k for k in spec.mcp_env_keys if k not in config]
        if missing_keys:
            raise ConnectorsError(
                f"configure({spec.id!r}): missing required env keys {missing_keys!r}. "
                "Supply them in the config dict."
            )

        # Store secret env values in keyring + build $keyring reference env block.
        env_block: Dict[str, Any] = {}
        for env_key in spec.mcp_env_keys:
            value = config[env_key]
            username = f"{spec.id}:{env_key}"
            keyring.set_password(SERVICE_NAME, username, str(value))
            env_block[env_key] = {"$keyring": _keyring_ref(spec.id, env_key)}

        # Read, update, and atomically write mcp_servers.json.
        servers = _read_mcp_servers_json()
        servers[spec.id] = {
            "command": spec.mcp_command,
            "args": list(spec.mcp_args),
            "env": env_block,
            "disabled": config.get("disabled", False),
        }
        _write_mcp_servers_json(servers)

        logger.info(
            "mcp_server: configured connector_id=%s command=%s",
            spec.id,
            spec.mcp_command,
        )

        if self._reload is not None:
            self._reload()

        return {
            "configured": True,
            "connector_id": spec.id,
            "command": spec.mcp_command,
            "args": list(spec.mcp_args),
        }

    async def disconnect(  # pylint: disable=unused-argument
        self,
        spec: ConnectorSpec,
        *,
        account_id: Optional[str] = None,
    ) -> None:
        """Remove the MCP server entry, keyring slots, and per-agent grants."""
        # Remove from mcp_servers.json.
        servers = _read_mcp_servers_json()
        if spec.id in servers:
            del servers[spec.id]
            _write_mcp_servers_json(servers)

        # Delete keyring entries for every env key.
        for env_key in spec.mcp_env_keys:
            username = f"{spec.id}:{env_key}"
            try:
                keyring.delete_password(SERVICE_NAME, username)
            except keyring.errors.PasswordDeleteError:
                pass  # already absent — idempotent

        # Wipe per-agent grants AND activations. If the same connector_id is
        # re-added later the new connector must NOT inherit the previous user's
        # consent (grant) or tool visibility (activation) without explicit
        # re-grant + re-activate.
        from gaia.connectors.activations import revoke_all_activations_for
        from gaia.connectors.grants import revoke_all_grants_for

        revoke_all_grants_for(spec.id)
        revoke_all_activations_for(spec.id)

        logger.info("mcp_server: disconnected connector_id=%s", spec.id)

        if self._reload is not None:
            self._reload()

    async def set_enabled(self, connector_id: str, enabled: bool) -> None:
        """
        Flip the ``disabled`` flag on an existing entry in ``mcp_servers.json``.

        Unlike ``configure``/``disconnect`` this method touches **only** the
        ``disabled`` field — keyring entries, env block ``$keyring`` references,
        command, args, and per-agent grants are all preserved. The reload
        callback fires so live agents pick up the change without a restart.

        Raises ``ConnectorsError`` if ``connector_id`` is not present in
        ``mcp_servers.json`` (the toggle is meaningful only for already-
        configured connectors).
        """
        servers = _read_mcp_servers_json()
        if connector_id not in servers:
            raise ConnectorsError(
                f"set_enabled({connector_id!r}, enabled={enabled}): "
                f"connector is not configured. Configure it first via "
                f"Settings → Connectors or `gaia connectors configure {connector_id}`."
            )

        servers[connector_id]["disabled"] = not enabled
        _write_mcp_servers_json(servers)

        logger.info(
            "mcp_server: %s connector_id=%s",
            "enabled" if enabled else "disabled",
            connector_id,
        )

        if self._reload is not None:
            self._reload()
        else:
            logger.warning(
                "McpServerHandler.set_enabled: reload_callback not set; "
                "the toggle has been persisted but the running agent's tool list "
                "will not reflect the change until GAIA is restarted."
            )

    async def test(self, spec: ConnectorSpec) -> Dict[str, Any]:
        """
        Verify the connector by checking all required keyring entries exist.

        Does NOT actually spawn the MCP server process — that would require
        the real ``npx`` / command binary which may not be available in CI.
        The presence of all keyring slots is treated as "configured and ready
        to spawn".
        """
        if not spec.mcp_env_keys:
            return {"ok": True, "detail": "no_secrets_required"}

        missing: List[str] = []
        for env_key in spec.mcp_env_keys:
            username = f"{spec.id}:{env_key}"
            if keyring.get_password(SERVICE_NAME, username) is None:
                missing.append(env_key)

        if missing:
            return {
                "ok": False,
                "detail": f"missing keyring entries: {missing!r}",
            }

        return {"ok": True, "detail": "keyring_entries_present"}


# Register the handler singleton at import time.
register_handler("mcp_server", McpServerHandler())
