# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""When to offer a messaging bridge's setup, and remembering the answer.

The rule: **offer once, then only again when something changed.** A prompt on
every launch trains the user to dismiss it, and a prompt that never returns
strands the user who installed the app after saying no. So the decision is
stored alongside whether the app was detected *at the time it was made*, and a
``skipped`` answer is reconsidered exactly when detection flips from absent to
present.

Shared by every bridge; each passes its own state file and a ``label`` naming
the platform in errors.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Pattern

from gaia.logger import get_logger

log = get_logger(__name__)

#: Never offered, never asked. The starting point for every install.
STATE_UNSET = "unset"
#: Credentials are stored and the bridge can run.
STATE_CONNECTED = "connected"
#: "Not now." Reconsidered only when the app appears on a machine that lacked it.
STATE_SKIPPED = "skipped"
#: "Stop asking." Honoured forever; setup still works on demand.
STATE_NEVER = "never"

VALID_STATES = frozenset({STATE_UNSET, STATE_CONNECTED, STATE_SKIPPED, STATE_NEVER})


@dataclass
class OnboardingState:
    """The stored decision, plus what was true when it was made."""

    state: str = STATE_UNSET
    #: Whether the app was installed when the decision was recorded — the whole
    #: mechanism behind "ask again if they install it later".
    detected_when_decided: bool = False
    #: Workspace or server the credentials belong to, for display only.
    team_name: str = ""
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        if self.state not in VALID_STATES:
            raise ValueError(
                f"Unknown onboarding state {self.state!r}. "
                f"Valid states: {', '.join(sorted(VALID_STATES))}."
            )


class OnboardingStateError(RuntimeError):
    """The stored decision exists but cannot be read."""


def app_is_installed(
    apps: Iterable[Dict[str, Any]], entity: str, content: Pattern[str]
) -> bool:
    """True when an installed-app inventory holds the app.

    Reads ``entity`` (``app:slack``) first and the rendered ``content`` line
    only as a fallback — the shape ``SystemDiscovery.scan_installed_apps``
    returns. An exact slug cannot be fooled by "Slackware Tools".
    """
    for app in apps:
        slug = str(app.get("entity") or "").strip().lower()
        if slug == entity:
            return True
        if not slug and content.match(str(app.get("content") or "").strip()):
            return True
    return False


def installed_content_pattern(name: str) -> Pattern[str]:
    """``Installed app: <name>`` anchored on a word boundary."""
    return re.compile(rf"^installed app:\s*{re.escape(name)}\b", re.IGNORECASE)


def load_state(path: Path, label: str) -> OnboardingState:
    """Read the stored decision. A missing file means "never asked".

    An unreadable or malformed file raises: silently resetting to "unset" would
    re-prompt a user who said never, the one outcome the state exists to stop.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return OnboardingState()
    except OSError as e:
        raise OnboardingStateError(
            f"Cannot read the {label} onboarding state at {path}: {e}. "
            f"Check permissions, or delete the file to be asked again."
        ) from e

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise OnboardingStateError(
            f"The {label} onboarding state at {path} is not valid JSON: {e}. "
            f"Delete the file to be asked again."
        ) from e
    if not isinstance(data, dict):
        raise OnboardingStateError(
            f"The {label} onboarding state at {path} must be a JSON object, "
            f"got {type(data).__name__}. Delete the file to be asked again."
        )

    known = {"state", "detected_when_decided", "team_name", "updated_at"}
    try:
        return OnboardingState(**{k: v for k, v in data.items() if k in known})
    except (TypeError, ValueError) as e:
        raise OnboardingStateError(
            f"The {label} onboarding state at {path} is not usable: {e}. "
            f"Delete the file to be asked again."
        ) from e


def save_state(state: OnboardingState, path: Path) -> None:
    """Persist the decision, stamping the time it was made."""
    state.updated_at = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2) + "\n", encoding="utf-8")
    log.debug("Saved onboarding state %r to %s", state.state, path)


def record_decision(
    state_name: str, *, detected: bool, path: Path, team_name: str = ""
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
    ``skipped``       yes         only if the app was ABSENT when skipped
    ``never``         either      no, forever
    ``connected``     either      no — already wired up
    ================  ==========  ====================================
    """
    if not detected:
        return False
    if state.state == STATE_UNSET:
        return True
    if state.state == STATE_SKIPPED:
        # Said no on a machine without the app; it has since been installed, so
        # the answer given was to a different question.
        return not state.detected_when_decided
    return False
