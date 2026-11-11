# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The voice settings GAIA may change herself when asked out loud.

Only how she sounds and listens: her voice, speed and volume, captions, and
whether she waits for her name. Everything else stays the user's to change in
Settings; :func:`change_voice_setting` says so instead of guessing.
"""

from __future__ import annotations

from typing import Any, Dict

from gaia.config import (
    READ_ALOUD_MAX_SPEED,
    READ_ALOUD_MIN_SPEED,
    VOICE_MAX_VOLUME,
    GaiaConfig,
    GaiaConfigError,
)

#: Spoken name of each setting -> its config key.
AGENT_VOICE_SETTINGS = {
    "voice": "read_aloud_voice",
    "speed": "read_aloud_speed",
    "volume": "voice_volume",
    "wake_word": "voice_wake_word",
    "captions": "voice_captions",
}
_ELSEWHERE = (
    "GAIA can change only her voice, speed, volume, captions and wake word. "
    "The user changes every other setting themselves in Settings."
)
_ON = {"on", "true", "yes", "enable", "enabled", "1"}
_OFF = {"off", "false", "no", "disable", "disabled", "0"}


def _voice_id(value: str) -> str:
    """A Kokoro voice id from an id ("bm_george") or a name ("George")."""
    from gaia.audio.lemonade_tts import KOKORO_VOICES, voice_catalog

    wanted = value.strip().lower()
    if wanted in KOKORO_VOICES:
        return wanted
    matches = [v["id"] for v in voice_catalog() if v["name"].lower() == wanted]
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(
        f"{v['name']} ({v['accent']} {v['gender']}, {v['id']})" for v in voice_catalog()
    )
    raise GaiaConfigError(f"No voice is called {value!r}. Voices: {names}.")


def _percent(setting: str, value: Any, low: int, high: int) -> int:
    try:
        number = float(str(value).strip().rstrip("%"))
    except ValueError as e:
        raise GaiaConfigError(
            f"{setting} is a percentage from {low} to {high}, got {value!r}."
        ) from e
    # "1.2" for speed means 1.2 times normal.
    if setting == "speed" and number <= 3:
        number *= 100
    whole = int(round(number))
    if not low <= whole <= high:
        raise GaiaConfigError(
            f"{setting} is a percentage from {low} to {high}, got {value!r}."
        )
    return whole


def _on_off(setting: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    word = str(value).strip().lower()
    if word in _ON:
        return True
    if word in _OFF:
        return False
    raise GaiaConfigError(f"{setting} is on or off, got {value!r}.")


def current_voice_settings() -> Dict[str, Any]:
    """What the five settings are now."""
    from gaia.audio.lemonade_tts import DEFAULT_VOICE

    config = GaiaConfig.load()
    return {
        "voice": config.read_aloud_voice or DEFAULT_VOICE,
        "speed": config.read_aloud_speed,
        "volume": config.voice_volume,
        "wake_word": config.voice_wake_word,
        "captions": config.voice_captions,
    }


def change_voice_setting(setting: str, value: Any) -> Dict[str, Any]:
    """Save one setting; returns ``status`` and the settings as they now are.

    A setting outside :data:`AGENT_VOICE_SETTINGS`, or a value it can't take,
    comes back as ``status: "error"`` with what to tell the user.
    """
    name = str(setting or "").strip().lower().replace(" ", "_").replace("-", "_")
    key = AGENT_VOICE_SETTINGS.get(name)
    if key is None:
        return {
            "status": "error",
            "error": f"{setting!r} is not one of them. {_ELSEWHERE}",
        }
    try:
        if name == "voice":
            saved: Any = _voice_id(str(value))
        elif name == "speed":
            saved = _percent(name, value, READ_ALOUD_MIN_SPEED, READ_ALOUD_MAX_SPEED)
        elif name == "volume":
            saved = _percent(name, value, 0, VOICE_MAX_VOLUME)
        else:
            saved = _on_off(name, value)
        GaiaConfig.save_keys({key: saved})
        return {"status": "success", "changed": name, **current_voice_settings()}
    except GaiaConfigError as e:
        return {"status": "error", "error": str(e)}
