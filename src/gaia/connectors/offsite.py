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
