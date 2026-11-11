# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA may change five voice settings herself, and nothing else."""

import pytest

from gaia.config import GaiaConfig
from gaia.voice.settings import (
    AGENT_VOICE_SETTINGS,
    change_voice_setting,
    current_voice_settings,
)


@pytest.fixture(autouse=True)
def gaia_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))


def test_only_the_five_safe_settings_are_hers():
    assert set(AGENT_VOICE_SETTINGS) == {
        "voice",
        "speed",
        "volume",
        "wake_word",
        "captions",
    }


def test_a_voice_by_id_or_by_name():
    assert change_voice_setting("voice", "bm_george")["voice"] == "bm_george"
    assert change_voice_setting("voice", "Bella")["voice"] == "af_bella"
    assert GaiaConfig.load().read_aloud_voice == "af_bella"


def test_an_unknown_voice_lists_the_real_ones_and_changes_nothing():
    change_voice_setting("voice", "af_bella")
    result = change_voice_setting("voice", "Morgan Freeman")
    assert result["status"] == "error" and "bm_george" in result["error"]
    assert current_voice_settings()["voice"] == "af_bella"


@pytest.mark.parametrize(
    "value,saved", [("120", 120), ("85%", 85), ("1.25", 125), (150, 150)]
)
def test_speed_takes_a_percentage_or_a_multiplier(value, saved):
    assert change_voice_setting("speed", value)["speed"] == saved


@pytest.mark.parametrize("value", ["400", "fast", "10"])
def test_a_speed_out_of_range_is_refused(value):
    result = change_voice_setting("speed", value)
    assert result["status"] == "error" and "50" in result["error"]
    assert current_voice_settings()["speed"] == 100


def test_volume_is_zero_to_a_hundred():
    assert change_voice_setting("volume", "40")["volume"] == 40
    assert change_voice_setting("volume", "140")["status"] == "error"
    assert GaiaConfig.load().voice_volume == 40


@pytest.mark.parametrize("name", ["wake_word", "wake word", "Wake-Word", "captions"])
def test_switches_take_on_or_off(name):
    key = name.lower().replace(" ", "_").replace("-", "_")
    assert change_voice_setting(name, "off")[key] is False
    assert change_voice_setting(name, "on")[key] is True
    assert change_voice_setting(name, "maybe")["status"] == "error"


@pytest.mark.parametrize(
    "setting", ["asr_model", "default_model", "lemonade_base_url", "turn_taking", ""]
)
def test_any_other_setting_is_refused_and_points_to_settings(setting):
    result = change_voice_setting(setting, "anything")
    assert result["status"] == "error"
    assert "Settings" in result["error"]
    assert GaiaConfig.load() == GaiaConfig()
