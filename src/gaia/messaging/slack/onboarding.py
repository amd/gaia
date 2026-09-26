# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""When to offer Slack setup, and remembering the answer.

The rule this module exists to encode: **offer once, then only again when
something changed.** A prompt on every launch trains the user to dismiss it, and
a prompt that never returns strands the user who installed Slack after saying no.
So the decision is stored alongside whether Slack was detected *at the time it
was made*, and a ``skipped`` answer is reconsidered exactly when detection flips
from absent to present.

Detection itself is not implemented here. ``SystemDiscovery.scan_installed_apps``
already inventories installed applications on Windows, macOS and Linux and
already categorises Slack; a second detector would be a second thing to be wrong
on someone's machine.

State lives in its own file rather than in ``GaiaConfig``: that dataclass has a
fixed field set and ``save()`` writes only known fields, so a nested key added to
it would be silently dropped by the next ``gaia config set``.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from gaia.logger import get_logger

log = get_logger(__name__)

#: Never offered, never asked. The starting point for every install.
STATE_UNSET = "unset"
#: Tokens are stored and the bridge can run.
STATE_CONNECTED = "connected"
#: "Not now." Reconsidered only when Slack appears on a machine that lacked it.
STATE_SKIPPED = "skipped"
#: "Stop asking." Honoured forever; `gaia slack setup` still works on demand.
STATE_NEVER = "never"

VALID_STATES = frozenset({STATE_UNSET, STATE_CONNECTED, STATE_SKIPPED, STATE_NEVER})

#: The normalized entity slug ``scan_installed_apps`` stamps on a Slack record.
#: Records look like ``{"entity": "app:slack", "content": "Installed app: Slack
#: [Communication]"}`` — a memory *fact*, not an app row with a ``name`` field.
#: Matching the slug exactly is both the cheapest check and the strictest: it
#: cannot be fooled by "Slackware Tools" the way a substring search would be.
_SLACK_ENTITY = "app:slack"

#: Fallback for a record with no entity slug: the app name as it appears in the
#: rendered ``content`` line. Anchored on word boundaries for the same reason.
_SLACK_CONTENT = re.compile(r"^installed app:\s*slack\b", re.IGNORECASE)


def _state_path() -> Path:
    """Where the onboarding decision is stored.

    Honours ``GAIA_CONFIG_DIR`` so a test — and a user with a relocated GAIA
    home — never writes to the real one.
    """
    base = os.environ.get("GAIA_CONFIG_DIR")
    root = Path(base) if base else Path.home() / ".gaia"
    return root / "slack" / "onboarding.json"


@dataclass
class OnboardingState:
    """The stored decision, plus what was true when it was made."""

    state: str = STATE_UNSET
    #: Whether Slack was installed at the moment the decision was recorded. This
    #: is the whole mechanism behind "ask again if they install it later".
    detected_when_decided: bool = False
    #: Workspace the tokens belong to, shown in the TUI so a user with several
    #: workspaces can tell which one is wired up. Never used for authorization.
    team_name: str = ""
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        if self.state not in VALID_STATES:
            raise ValueError(
                f"Unknown Slack onboarding state {self.state!r}. "
                f"Valid states: {', '.join(sorted(VALID_STATES))}."
            )


class OnboardingStateError(RuntimeError):
    """The stored decision exists but cannot be read."""


def slack_is_installed(apps: Iterable[Dict[str, Any]]) -> bool:
    """True when an installed-app inventory contains Slack.

    Takes the inventory rather than calling the scanner, so the caller decides
    when to pay for a filesystem walk and a test needs no fake home directory.

    Reads ``entity`` first and ``content`` only as a fallback, because that is
    the shape ``scan_installed_apps`` actually returns — discovery facts, not
    rows with a ``name``. Pinned by
    ``tests/unit/messaging/test_slack_onboarding.py`` against a record captured
    from a real scan, so a change to the discovery format fails here rather than
    silently reporting that nobody has Slack.
    """
    for app in apps:
        entity = str(app.get("entity") or "").strip().lower()
        if entity == _SLACK_ENTITY:
            return True
        if not entity and _SLACK_CONTENT.match(str(app.get("content") or "").strip()):
            return True
    return False


def detect_slack() -> bool:
    """Scan this machine for Slack via the shared installed-app inventory.

    Imported lazily: ``SystemDiscovery`` pulls in sqlite, plistlib and a registry
    reader, none of which a caller that already has an inventory should pay for.
    """
    from gaia.agents.base.discovery import SystemDiscovery

    return slack_is_installed(SystemDiscovery().scan_installed_apps())


def load_state(path: Optional[Path] = None) -> OnboardingState:
    """Read the stored decision. A missing file means "never asked".

    A file that exists but is unreadable or malformed raises rather than
    defaulting: silently resetting to "unset" would re-prompt a user who
    explicitly said never, which is the one outcome the state exists to prevent.
    """
    state_file = path or _state_path()
    try:
        text = state_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return OnboardingState()
    except OSError as e:
        raise OnboardingStateError(
            f"Cannot read the Slack onboarding state at {state_file}: {e}. "
            f"Check permissions, or delete the file to be asked again."
        ) from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise OnboardingStateError(
            f"The Slack onboarding state at {state_file} is not valid JSON: {e}. "
            f"Delete the file to be asked again."
        ) from e
    if not isinstance(data, dict):
        raise OnboardingStateError(
            f"The Slack onboarding state at {state_file} must be a JSON object, "
            f"got {type(data).__name__}. Delete the file to be asked again."
        )

    known = {"state", "detected_when_decided", "team_name", "updated_at"}
    try:
        return OnboardingState(**{k: v for k, v in data.items() if k in known})
    except (TypeError, ValueError) as e:
        raise OnboardingStateError(
            f"The Slack onboarding state at {state_file} is not usable: {e}. "
            f"Delete the file to be asked again."
        ) from e


def save_state(state: OnboardingState, path: Optional[Path] = None) -> None:
    """Persist the decision, stamping the time it was made."""
    state_file = path or _state_path()
    state.updated_at = time.time()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(asdict(state), indent=2) + "\n", encoding="utf-8")
    log.debug("Saved Slack onboarding state %r to %s", state.state, state_file)


def record_decision(
    state_name: str,
    *,
    detected: bool,
    team_name: str = "",
    path: Optional[Path] = None,
) -> OnboardingState:
    """Store one answer to the setup offer."""
    state = OnboardingState(
        state=state_name, detected_when_decided=detected, team_name=team_name
    )
    save_state(state, path)
    return state


def should_offer(state: OnboardingState, detected: bool) -> bool:
    """Whether to put the setup offer in front of the user right now.

    ================  ==========  ====================================
    Stored state      Detected    Offer?
    ================  ==========  ====================================
    ``unset``         yes         yes — the first-boot offer
    ``unset``         no          no — nothing to connect to
    ``skipped``       yes         only if Slack was ABSENT when skipped
    ``never``         either      no, forever
    ``connected``     either      no — already wired up
    ================  ==========  ====================================
    """
    if not detected:
        return False
    if state.state == STATE_UNSET:
        return True
    if state.state == STATE_SKIPPED:
        # The user said no on a machine without Slack; they have since installed
        # it, so the answer they gave was to a different question.
        return not state.detected_when_decided
    return False
