# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
McpServerHandler — ConnectorHandler implementation for ``type="mcp_server"``.

Manages MCP server connectors: stores secret env-var values in the OS keyring
under ``$keyring`` references, writes ``<gaia_home>/mcp_servers.json`` atomically,
and signals ``MCPClientManager.reload()`` so new tools materialize without
restarting GAIA (plan amendment A5).

Keyring storage layout:
  - Service: ``gaia.connections`` (same service as OAuth tokens, per A3)
  - Username: ``<connector_id>:<env_key>``  (e.g. ``"mcp-github:GITHUB_PERSONAL_ACCESS_TOKEN"``)

``mcp_servers.json`` env block uses ``$keyring`` references (plan amendment A4):
  ``{"env": {"GITHUB_PERSONAL_ACCESS_TOKEN": {"$keyring": "gaia.connections:mcp-github:GITHUB_PERSONAL_ACCESS_TOKEN"}}}``
``MCPClient.from_config()`` resolves references at spawn time and fails closed
if a referenced keyring entry is missing (plan amendment A5b).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from gaia.config import mcp_servers_path
from gaia.connectors._keyring import keyring  # actionable error if missing (#1621)
from gaia.connectors.errors import ConnectorsError
from gaia.connectors.handler import register_handler
from gaia.connectors.spec import ConnectorSpec
from gaia.connectors.store import SERVICE_NAME

logger = logging.getLogger(__name__)


def _mcp_servers_path() -> Path:
    """Resolve on each call so ``GAIA_HOME`` set after import is honored."""
    return mcp_servers_path()


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


def _saved_keyring_usernames(connector_id: str, entry: Any) -> List[str]:
    """Return keyring usernames a saved entry references in ``connector_id``'s own namespace."""
    if not isinstance(entry, dict):
        return []
    values: List[Any] = []
    for block in ("env", "headers"):
        if isinstance(entry.get(block), dict):
            values.extend(entry[block].values())
    prefix = f"{SERVICE_NAME}:{connector_id}:"
    usernames = []
    for value in values:
        ref = value.get("$keyring") if isinstance(value, dict) else None
        if isinstance(ref, str) and ref.startswith(prefix):
            usernames.append(ref[len(SERVICE_NAME) + 1 :])
    return usernames


def _rederived_env(spec: ConnectorSpec, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Build *spec*'s ``$keyring`` env block; a renamed key reads its old slot."""
    saved = set(_saved_keyring_usernames(spec.id, entry))
    stale = sorted(saved - {f"{spec.id}:{env_key}" for env_key in spec.mcp_env_keys})
    unsaved = [k for k in spec.mcp_env_keys if f"{spec.id}:{k}" not in saved]
    env: Dict[str, Any] = {
        env_key: {"$keyring": _keyring_ref(spec.id, env_key)}
        for env_key in spec.mcp_env_keys
    }
    # One key gone and one new: a rename, so the secret is still under the old name.
    if len(stale) == 1 and len(unsaved) == 1:
        env[unsaved[0]] = {"$keyring": f"{SERVICE_NAME}:{stale[0]}"}
    return env


def validate_base_url(url: Any) -> str:
    """Return *url* as a bare ``scheme://host[:port]``, or raise.

    Only a base URL is accepted: the catalog appends the endpoint path, so a
    pasted path, query, fragment or ``user:pass@`` is refused rather than
    trusted. Plain http must point at this PC or the local network; the
    transport checks that again on every connection.
    """
    text = url.strip() if isinstance(url, str) else ""
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConnectorsError(
            f"{text or 'The address'!r} is not a web address. Enter it like "
            f"http://homeassistant.local:8123."
        )
    if parsed.username is not None or parsed.password is not None:
        raise ConnectorsError(
            "Leave the user name and password out of the address; the access "
            "token is entered separately."
        )
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ConnectorsError(
            f"Enter only the server's address, without a path: "
            f"{parsed.scheme}://{parsed.netloc}"
        )
    try:
        port = parsed.port
    except ValueError as e:
        raise ConnectorsError(f"{text!r} has an invalid port: {e}") from e
    base = f"{parsed.scheme}://{parsed.netloc}".lower()
    if parsed.scheme == "http":
        # Deferred: the MCP client imports the connectors package.
        from gaia.mcp.client.transports.http import is_private_address

        try:
            infos = socket.getaddrinfo(parsed.hostname, port or 80)
        except OSError as e:
            raise ConnectorsError(
                f"Can't find {parsed.hostname} on the network ({e}). Check the "
                f"address, and that this PC is on the same network."
            ) from e
        public = sorted(
            {info[4][0] for info in infos if not is_private_address(info[4][0])}
        )
        if public:
            raise ConnectorsError(
                f"{parsed.hostname} is a public address ({public[0]}). GAIA only "
                f"sends an access token over plain http on your local network. "
                f"Use the server's https address."
            )
    return base


def _http_entry(spec: ConnectorSpec, base_url: str, disabled: bool) -> Dict[str, Any]:
    """The ``mcp_servers.json`` entry for a remote catalog server."""
    return {
        "type": "http",
        "url": base_url + spec.mcp_url_path,
        "base_url": base_url,
        "headers": {
            header: {"$keyring": _keyring_ref(spec.id, key), "format": template}
            for header, key, template in spec.mcp_headers
        },
        "private_http_only": True,
        "writes_need_person": spec.mcp_writes_need_person,
        "tool_note": spec.mcp_tool_note,
        "disabled": disabled,
    }


def _http_hint(spec: ConnectorSpec, kind: str, base_url: str, fallback: str) -> str:
    return dict(spec.mcp_http_hints).get(kind, fallback).replace("{url}", base_url)


def probe_http_server(
    spec: ConnectorSpec, base_url: str, secrets: Dict[str, str]
) -> Dict[str, Any]:
    """Connect to a remote catalog server for real and count its tools.

    Returns ``{"ok": True, "detail": ..., "tool_count": N}`` or
    ``{"ok": False, "detail": <what to do>}``. The token goes only to the
    catalog-pinned endpoint under *base_url*.
    """
    # Deferred: the MCP client imports the connectors package.
    from gaia.mcp.client.mcp_client import MCPClient
    from gaia.mcp.client.transports.http import HTTPTransport, MCPHTTPError

    headers = {
        header: template.replace("{}", secrets[key])
        for header, key, template in spec.mcp_headers
    }
    transport = HTTPTransport(
        base_url + spec.mcp_url_path,
        timeout=15,
        headers=headers,
        private_http_only=True,
    )
    client = MCPClient(spec.id, transport)
    if not client.connect():
        cause = client.last_exception
        while cause is not None and not isinstance(cause, MCPHTTPError):
            cause = cause.__cause__
        status = cause.status if isinstance(cause, MCPHTTPError) else None
        if isinstance(cause, MCPHTTPError) and cause.refused:
            detail = str(cause)
        elif status in (401, 403):
            detail = _http_hint(
                spec, "unauthorized", base_url, "The server rejected the access token."
            )
        elif status == 404:
            detail = _http_hint(
                spec, "not_found", base_url, f"No MCP server answers at {base_url}."
            )
        elif status is None and isinstance(cause, MCPHTTPError):
            detail = _http_hint(
                spec, "unreachable", base_url, f"Nothing is answering at {base_url}."
            )
        else:
            detail = _http_hint(
                spec,
                "protocol",
                base_url,
                f"The server at {base_url} did not complete the MCP handshake.",
            )
        logger.info("mcp_server: probe of %s failed: %s", spec.id, client.last_error)
        return {"ok": False, "detail": detail}
    try:
        tool_count = len(client.list_tools())
    finally:
        client.disconnect()
    return {
        "ok": True,
        "detail": f"Connected: {tool_count} {spec.display_name} tools.",
        "tool_count": tool_count,
    }


def rederive_catalog_entries(servers: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return *servers* with each catalog connector's entry rebuilt from the catalog.

    A saved entry holds what ``configure`` wrote at the time, so catalog fixes
    (a version pin, a new command, a renamed env var) never reached people who
    had already connected. The catalog-owned fields (``command``, ``args`` and
    the ``env`` keys it defines) are rebuilt; the user's own (``disabled``,
    ``timeout``, plain env values they added) are kept, and secrets stay in the
    keyring.

    Ids the catalog doesn't know (hand-added servers) are returned unchanged.
    Nothing is written back: ``configure`` / ``disconnect`` stay the only writers.
    """
    # Imported here: the catalog imports this module to register the handler.
    import gaia.connectors.catalog  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.registry import REGISTRY

    rebuilt = dict(servers)
    for connector_id, entry in servers.items():
        if connector_id not in REGISTRY or not isinstance(entry, dict):
            continue
        spec = REGISTRY.get(connector_id)
        if spec.type != "mcp_server":
            continue
        if spec.mcp_transport == "http":
            base_url = entry.get("base_url")
            if not isinstance(base_url, str):
                continue
            kept = {
                k: v
                for k, v in entry.items()
                if k not in ("command", "args", "env", "url", "headers")
            }
            rebuilt[connector_id] = {
                **kept,
                **_http_entry(spec, base_url, bool(entry.get("disabled", False))),
            }
            continue
        saved_env = entry.get("env")
        env = {
            key: value
            for key, value in (saved_env if isinstance(saved_env, dict) else {}).items()
            if key not in spec.mcp_env_keys and isinstance(value, str)
        }
        env.update(_rederived_env(spec, entry))
        rebuilt[connector_id] = {
            **entry,
            "command": spec.mcp_command,
            "args": list(spec.mcp_args),
            "env": env,
        }
    return rebuilt


def _saved_secret(spec: ConnectorSpec, env_key: str) -> Optional[str]:
    """Return *env_key*'s secret; a renamed key reads the slot it was saved under."""
    value = keyring.get_password(SERVICE_NAME, f"{spec.id}:{env_key}")
    if value is not None:
        return value
    entry = _read_mcp_servers_json().get(spec.id)
    if not isinstance(entry, dict):
        return None
    _, _, username = _rederived_env(spec, entry)[env_key]["$keyring"].partition(":")
    if username == f"{spec.id}:{env_key}":
        return None
    return keyring.get_password(SERVICE_NAME, username)


def _delete_keyring_slots(usernames: Iterable[str]) -> None:
    """Delete each ``gaia.connections`` keyring slot; absent slots are already gone."""
    for username in sorted(usernames):
        try:
            keyring.delete_password(SERVICE_NAME, username)
        except keyring.errors.PasswordDeleteError:
            pass


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
            value = _saved_secret(spec, env_key)
            if value is None:
                missing.append(_keyring_ref(spec.id, env_key))
            else:
                env[env_key] = value

        if missing:
            raise ConnectorsError(
                f"MCP server connector '{spec.id}' has missing keyring entries: "
                f"{missing!r}. Reconfigure via Settings → Connectors or "
                f"`gaia connectors configure {spec.id}`."
            )

        if spec.mcp_transport == "http":
            entry = _read_mcp_servers_json().get(spec.id) or {}
            return {"env": env, "url": entry.get("url")}
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
        if spec.mcp_transport == "http":
            return await self._configure_http(spec, config)

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
        stale = set(_saved_keyring_usernames(spec.id, servers.get(spec.id))) - {
            f"{spec.id}:{env_key}" for env_key in spec.mcp_env_keys
        }
        servers[spec.id] = {
            "command": spec.mcp_command,
            "args": list(spec.mcp_args),
            "env": env_block,
            "disabled": config.get("disabled", False),
        }
        _write_mcp_servers_json(servers)
        _delete_keyring_slots(stale)

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

    async def _configure_http(
        self, spec: ConnectorSpec, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Check a remote server answers with these credentials, then save them.

        Nothing is saved when the live check fails, so a typo never leaves a
        half-configured connector behind.
        """
        if spec.mcp_url_field not in config:
            raise ConnectorsError(
                f"configure({spec.id!r}): missing {spec.mcp_url_field!r}, the "
                f"server's address."
            )
        # Off the event loop: a .local lookup can take seconds.
        base_url = await asyncio.to_thread(
            validate_base_url, config[spec.mcp_url_field]
        )
        secrets = {key: str(config.get(key) or "").strip() for key in spec.mcp_env_keys}
        empty = [key for key, value in secrets.items() if not value]
        if empty:
            raise ConnectorsError(
                f"configure({spec.id!r}): {empty[0]!r} is empty. Paste the access "
                f"token."
            )
        result = await asyncio.to_thread(probe_http_server, spec, base_url, secrets)
        if not result["ok"]:
            raise ConnectorsError(result["detail"])

        servers = _read_mcp_servers_json()
        stale = set(_saved_keyring_usernames(spec.id, servers.get(spec.id))) - {
            f"{spec.id}:{key}" for key in spec.mcp_env_keys
        }
        for key, value in secrets.items():
            keyring.set_password(SERVICE_NAME, f"{spec.id}:{key}", value)
            # A null or read-only backend accepts the write and keeps nothing,
            # and the agent would then fail closed with no tools at all.
            if keyring.get_password(SERVICE_NAME, f"{spec.id}:{key}") != value:
                raise ConnectorsError(
                    f"This PC's keyring didn't keep the {spec.display_name} token, "
                    f"so GAIA can't save the connection. Check that the system "
                    f"keyring (Windows Credential Manager, macOS Keychain, or a "
                    f"Secret Service on Linux) is available, then connect again."
                )
        servers[spec.id] = _http_entry(
            spec, base_url, bool(config.get("disabled", False))
        )
        _write_mcp_servers_json(servers)
        _delete_keyring_slots(stale)
        logger.info("mcp_server: configured connector_id=%s url=%s", spec.id, base_url)

        if self._reload is not None:
            self._reload()

        return {
            "configured": True,
            "connector_id": spec.id,
            "url": base_url,
            "detail": result["detail"],
            "tool_count": result["tool_count"],
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
        usernames = {f"{spec.id}:{env_key}" for env_key in spec.mcp_env_keys}
        if spec.id in servers:
            # The saved entry may reference slots under env names the catalog has since renamed.
            usernames.update(_saved_keyring_usernames(spec.id, servers[spec.id]))
            del servers[spec.id]
            _write_mcp_servers_json(servers)

        _delete_keyring_slots(usernames)

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
        if spec.mcp_transport == "http":
            return await self._test_http(spec)

        if not spec.mcp_env_keys:
            return {"ok": True, "detail": "no_secrets_required"}

        missing: List[str] = []
        for env_key in spec.mcp_env_keys:
            if _saved_secret(spec, env_key) is None:
                missing.append(env_key)

        if missing:
            return {
                "ok": False,
                "detail": f"missing keyring entries: {missing!r}",
            }

        return {"ok": True, "detail": "keyring_entries_present"}

    async def _test_http(self, spec: ConnectorSpec) -> Dict[str, Any]:
        """Probe a connected remote server with its saved credentials."""
        entry = _read_mcp_servers_json().get(spec.id)
        base_url = entry.get("base_url") if isinstance(entry, dict) else None
        if not isinstance(base_url, str):
            return {"ok": False, "detail": f"{spec.display_name} is not connected."}
        secrets = {key: _saved_secret(spec, key) for key in spec.mcp_env_keys}
        if any(value is None for value in secrets.values()):
            return {
                "ok": False,
                "detail": f"The saved access token is missing. Connect "
                f"{spec.display_name} again.",
            }
        try:
            base_url = await asyncio.to_thread(validate_base_url, base_url)
        except ConnectorsError as e:
            return {"ok": False, "detail": str(e)}
        return await asyncio.to_thread(probe_http_server, spec, base_url, secrets)


# Register the handler singleton at import time.
register_handler("mcp_server", McpServerHandler())
