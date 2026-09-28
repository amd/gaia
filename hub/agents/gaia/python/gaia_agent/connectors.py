# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship's declared connector requirements.

Separate from :mod:`gaia_agent.agent` so ``build_gaia()`` can publish the same
literal the agent class declares without importing the heavy agent module at
registry-discovery time.
"""

from __future__ import annotations

from typing import Tuple

from gaia.agents.tools._email.scopes import (
    DECLARED_SCOPES,
    GOOGLE_CONNECTOR_ID,
    MICROSOFT_CONNECTOR_ID,
)
from gaia.connectors.providers.base import ConnectorRequirement

MAILBOX_REQUIREMENTS: Tuple[ConnectorRequirement, ...] = (
    ConnectorRequirement(
        connector_id=GOOGLE_CONNECTOR_ID,
        scopes=list(DECLARED_SCOPES[GOOGLE_CONNECTOR_ID]),
        reason="Read and search your Gmail so the agent can triage your inbox.",
    ),
    ConnectorRequirement(
        connector_id=MICROSOFT_CONNECTOR_ID,
        scopes=list(DECLARED_SCOPES[MICROSOFT_CONNECTOR_ID]),
        reason="Read and search your Outlook mail so the agent can triage your inbox.",
    ),
)

__all__ = ["MAILBOX_REQUIREMENTS"]
