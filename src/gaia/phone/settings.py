# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Phone settings: defaults and the validation every write goes through."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict

from gaia.phone.errors import PhoneError

PROVIDERS = ("twilio", "simulated")
STEPS = ("notify", "notify_text", "notify_text_call")
RULES = ("schedule_failed", "approval_waiting", "gaia_down", "agent")
SIMULATED_OUTCOMES = ("human", "machine_start", "no-answer", "busy")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "provider": "simulated",
    "from_number": "",
    "texts_enabled": False,
    "quiet_hours": None,
    "limits": {"calls_per_day": 6, "texts_per_day": 20, "agent_calls_per_day": 2},
    "delays": {"text_after_minutes": 5, "call_after_minutes": 5},
    "cooldown_minutes": 30,
    "simulated_outcome": "human",
    "keep_awake": True,
    "talk_on_calls": True,
    "voice": "af_nicole",
    "rules": {
        "schedule_failed": {"on": True, "steps": "notify_text_call", "urgent": False},
        "approval_waiting": {
            "on": True,
            "steps": "notify_text",
            "urgent": False,
            "after_minutes": 10,
        },
        "gaia_down": {"on": True, "steps": "notify_text_call", "urgent": False},
        "agent": {"on": False, "steps": "notify_text_call", "urgent": False},
    },
}

_LIMIT_RANGES = {
    "calls_per_day": (0, 20),
    "texts_per_day": (0, 50),
    "agent_calls_per_day": (0, 5),
}
_DELAY_RANGE = (0, 120)


def with_defaults(stored: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(DEFAULTS)
    for key, value in (stored or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            if key == "rules":
                for rule, cfg in value.items():
                    merged["rules"].setdefault(rule, {}).update(cfg)
            else:
                merged[key].update(value)
        else:
            merged[key] = value
    return merged


def apply_patch(current: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """*current* with *patch* applied, validated. Raises ``bad_request``."""
    out = copy.deepcopy(current)
    for key, value in patch.items():
        if key == "enabled":
            out[key] = _bool(key, value)
        elif key == "provider":
            if value not in PROVIDERS:
                raise _bad(f"provider must be one of {', '.join(PROVIDERS)}")
            out[key] = value
        elif key == "voice":
            from gaia.audio.lemonade_tts import KOKORO_VOICES

            if value not in KOKORO_VOICES:
                raise _bad("voice must be one of the Kokoro voices")
            out[key] = value
        elif key in ("texts_enabled", "keep_awake", "talk_on_calls"):
            out[key] = _bool(key, value)
        elif key == "quiet_hours":
            out[key] = _quiet(value)
        elif key == "limits":
            for name, number in _dict(key, value).items():
                low, high = _LIMIT_RANGES.get(name, (None, None))
                if low is None:
                    raise _bad(f"unknown limit {name}")
                out["limits"][name] = _int(name, number, low, high)
        elif key == "delays":
            for name, number in _dict(key, value).items():
                if name not in DEFAULTS["delays"]:
                    raise _bad(f"unknown delay {name}")
                out["delays"][name] = _int(name, number, *_DELAY_RANGE)
        elif key == "cooldown_minutes":
            out[key] = _int(key, value, 0, 1440)
        elif key == "simulated_outcome":
            if value not in SIMULATED_OUTCOMES:
                raise _bad(f"simulated_outcome must be one of {SIMULATED_OUTCOMES}")
            out[key] = value
        elif key == "rules":
            for rule, cfg in _dict(key, value).items():
                if rule not in RULES:
                    raise _bad(f"unknown rule {rule}")
                for field, v in _dict(rule, cfg).items():
                    if field in ("on", "urgent"):
                        out["rules"][rule][field] = _bool(field, v)
                    elif field == "steps":
                        if v not in STEPS:
                            raise _bad(f"steps must be one of {', '.join(STEPS)}")
                        out["rules"][rule][field] = v
                    elif field == "after_minutes" and rule == "approval_waiting":
                        out["rules"][rule][field] = _int(field, v, 1, 240)
                    else:
                        raise _bad(f"unknown setting {rule}.{field}")
        elif key == "from_number":
            raise _bad("from_number is set with the provider keys")
        else:
            raise _bad(f"unknown phone setting {key}")
    return out


def _quiet(value: Any):
    if value is None:
        return None
    window = _dict("quiet_hours", value)
    start, end = window.get("start"), window.get("end")
    if not (isinstance(start, str) and _HHMM.match(start)):
        raise _bad("quiet_hours.start must be HH:MM, 24-hour")
    if not (isinstance(end, str) and _HHMM.match(end)):
        raise _bad("quiet_hours.end must be HH:MM, 24-hour")
    if start == end:
        raise _bad("quiet hours need different start and end times")
    return {"start": start, "end": end}


def _bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise _bad(f"{name} must be true or false")
    return value


def _int(name: str, value: Any, low: int, high: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not low <= value <= high
    ):
        raise _bad(f"{name} must be a whole number from {low} to {high}")
    return value


def _dict(name: str, value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise _bad(f"{name} must be an object")
    return value


def _bad(message: str) -> PhoneError:
    return PhoneError("bad_request", message)
