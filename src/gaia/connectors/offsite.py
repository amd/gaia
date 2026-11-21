# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Refuse connectors that send data off this PC while Local only is on."""

from __future__ import annotations

from gaia.connectors.errors import ConfigurationError
from gaia.connectors.spec import ConnectorSpec


def offsite_blocked_message(spec: ConnectorSpec) -> str | None:
    """Why ``spec`` is refused under Local only, or ``None`` if it may run."""
    if not spec.sends_data_offsite:
        return None
    from gaia.llm.local_only import SETTINGS_HINT, cloud_blocked

    if not cloud_blocked():
        return None
    return (
        f"{spec.display_name} is unavailable while Local only is on: "
        f"it sends data off this PC. {SETTINGS_HINT}"
    )


def refuse_offsite_connector(spec: ConnectorSpec) -> None:
    """Raise ``ConfigurationError`` when Local only forbids ``spec``."""
    message = offsite_blocked_message(spec)
    if message:
        raise ConfigurationError(message)


def offsite_server_blocked_message(server_name: str) -> str | None:
    """Why the MCP server saved as ``server_name`` is refused under Local only.

    Checked when a server starts and on every tool call, so a connector turned
    on before Local only cannot keep running or answering after it.
    """
    # Registers the catalog specs; a hand-added server isn't a catalog connector.
    import gaia.connectors.catalog.mcp_servers  # noqa: F401  # pylint: disable=unused-import
    from gaia.connectors.registry import REGISTRY

    if server_name not in REGISTRY:
        return None
    return offsite_blocked_message(REGISTRY.get(server_name))
