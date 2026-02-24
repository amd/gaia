# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for TalkConfig mic_threshold and AudioClient configuration threading."""

from unittest.mock import MagicMock, patch

from gaia.audio.audio_client import AudioClient
from gaia.talk.sdk import TalkConfig, TalkSDK


def test_talk_config_mic_threshold_default():
    """TalkConfig default mic_threshold is 0.003."""
    config = TalkConfig()
    assert config.mic_threshold == 0.003


def test_talk_config_mic_threshold_custom():
    """TalkConfig accepts a custom mic_threshold."""
    config = TalkConfig(mic_threshold=0.01)
    assert config.mic_threshold == 0.01


def test_talk_sdk_passes_mic_threshold_to_audio_client():
    """TalkSDK passes mic_threshold from TalkConfig through to AudioClient."""
    with (
        patch("gaia.talk.sdk.AudioClient") as MockAudioClient,
        patch("gaia.talk.sdk.ChatSDK"),
    ):
        MockAudioClient.return_value = MagicMock()
        config = TalkConfig(mic_threshold=0.007, enable_tts=False)
        TalkSDK(config)
        call_kwargs = MockAudioClient.call_args[1]
        assert call_kwargs["mic_threshold"] == 0.007


def test_audio_client_stores_mic_threshold():
    """AudioClient stores a custom mic_threshold attribute."""
    with patch("gaia.audio.audio_client.create_client"):
        client = AudioClient(mic_threshold=0.005)
        assert client.mic_threshold == 0.005


def test_audio_client_default_mic_threshold():
    """AudioClient default mic_threshold is 0.003."""
    with patch("gaia.audio.audio_client.create_client"):
        client = AudioClient()
        assert client.mic_threshold == 0.003
