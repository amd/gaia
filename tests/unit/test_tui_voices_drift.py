# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The TUI's embedded voice list must match the voices Python sends to Lemonade."""

import json
from pathlib import Path

from gaia.audio.lemonade_tts import DEFAULT_VOICE, KOKORO_VOICES
from gaia.config import READ_ALOUD_MAX_SPEED, READ_ALOUD_MIN_SPEED

TUI_SPEECH = Path(__file__).parents[2] / "tui" / "internal" / "speech"


def _voices_doc():
    return json.loads((TUI_SPEECH / "voices.json").read_text(encoding="utf-8"))


def test_tui_voices_match_kokoro_voices():
    doc = _voices_doc()
    tui = [(v["id"], v["name"]) for v in doc["voices"]]
    python = [(voice_id, meta["name"]) for voice_id, meta in KOKORO_VOICES.items()]
    assert tui == python


def test_tui_default_voice_matches():
    assert _voices_doc()["default"] == DEFAULT_VOICE


def test_tui_speed_range_matches():
    source = (TUI_SPEECH / "voices.go").read_text(encoding="utf-8")
    assert f"MinSpeed     = {READ_ALOUD_MIN_SPEED}" in source
    assert f"MaxSpeed     = {READ_ALOUD_MAX_SPEED}" in source
