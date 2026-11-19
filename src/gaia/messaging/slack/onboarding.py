# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""When to offer Slack setup, and remembering the answer.

The state machine is shared with every bridge (``gaia.messaging.onboarding``);
this module binds it to Slack's state file and detector.

Detection itself is not implemented here. ``SystemDiscovery.scan_installed_apps``
already inventories installed applications on Windows, macOS and Linux and
already categorises Slack; a second detector would be a second thing to be wrong
on someone's machine.

State lives in its own file rather than in ``GaiaConfig``: that dataclass has a
fixed field set and ``save()`` writes only known fields, so a nested key added to
it would be silently dropped by the next ``gaia config set``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from gaia.messaging import onboarding as _shared
from gaia.messaging.onboarding import (
    STATE_CONNECTED,
    STATE_NEVER,
    STATE_SKIPPED,
    STATE_UNSET,
    VALID_STATES,
    OnboardingState,
    OnboardingStateError,
    should_offer,
)

__all__ = [
    "STATE_CONNECTED",
    "STATE_NEVER",
    "STATE_SKIPPED",
    "STATE_UNSET",
    "VALID_STATES",
    "OnboardingState",
    "OnboardingStateError",
    "detect_slack",
    "load_state",
    "record_decision",
    "save_state",
    "should_offer",
    "slack_is_installed",
]

#: The normalized entity slug ``scan_installed_apps`` stamps on a Slack record.
#: Records look like ``{"entity": "app:slack", "content": "Installed app: Slack
#: [Communication]"}`` — a memory *fact*, not an app row with a ``name`` field.
_SLACK_ENTITY = "app:slack"
_SLACK_CONTENT = _shared.installed_content_pattern("slack")
_LABEL = "Slack"


def _state_path() -> Path:
    """Where the onboarding decision is stored; honours ``GAIA_CONFIG_DIR``."""
    base = os.environ.get("GAIA_CONFIG_DIR")
    root = Path(base) if base else Path.home() / ".gaia"
    return root / "slack" / "onboarding.json"


def slack_is_installed(apps: Iterable[Dict[str, Any]]) -> bool:
    """True when an installed-app inventory contains Slack.

    Pinned by ``tests/unit/messaging/test_slack_onboarding.py`` against a record
    captured from a real scan, so a change to the discovery format fails there
    rather than silently reporting that nobody has Slack.
    """
    return _shared.app_is_installed(apps, _SLACK_ENTITY, _SLACK_CONTENT)


def detect_slack() -> bool:
    """Scan this machine for Slack via the shared installed-app inventory."""
    from gaia.agents.base.discovery import SystemDiscovery

    return slack_is_installed(SystemDiscovery().scan_installed_apps())


def load_state(path: Optional[Path] = None) -> OnboardingState:
    """Read the stored decision. A missing file means "never asked"."""
    return _shared.load_state(path or _state_path(), _LABEL)


def save_state(state: OnboardingState, path: Optional[Path] = None) -> None:
    """Persist the decision, stamping the time it was made."""
    _shared.save_state(state, path or _state_path())


def record_decision(
    state_name: str,
    *,
    detected: bool,
    team_name: str = "",
    path: Optional[Path] = None,
) -> OnboardingState:
    """Store one answer to the setup offer."""
    return _shared.record_decision(
        state_name, detected=detected, path=path or _state_path(), team_name=team_name
    )
