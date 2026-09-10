# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for voice-session command handling."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from gaia.talk.sdk import TalkConfig, TalkSDK

# asyncio uses a local socketpair for its Windows event-loop wakeup.
pytestmark = pytest.mark.allow_network


def _talk_sdk_with_captured_voice_processor():
    """Build a TalkSDK seam without creating real audio or model clients."""
    talk = TalkSDK.__new__(TalkSDK)
    talk.config = TalkConfig(enable_tts=False)
    talk.log = MagicMock()
    talk.show_stats = False
    talk.chat_sdk = MagicMock()
    talk.audio_client = MagicMock()
    talk.audio_client.start_voice_chat = AsyncMock()
    return talk


def _run(coro):
    """Run async SDK code without the Windows proactor socketpair."""
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    previous_policy = asyncio.get_event_loop_policy()
    if selector_policy:
        asyncio.set_event_loop_policy(selector_policy())
    try:
        return asyncio.run(coro)
    finally:
        asyncio.set_event_loop_policy(previous_policy)


def test_restart_voice_command_clears_history_without_model_call(capsys):
    """The documented restart command clears history and is not sent to the LLM."""
    talk = _talk_sdk_with_captured_voice_processor()

    _run(talk.start_voice_session())
    voice_processor = talk.audio_client.start_voice_chat.call_args.args[0]

    _run(voice_processor("  Restart! "))

    talk.chat_sdk.clear_history.assert_called_once_with()
    talk.chat_sdk.send.assert_not_called()
    assert "Conversation history cleared." in capsys.readouterr().out


def test_regular_voice_input_still_reaches_model():
    """Only the exact restart command is intercepted."""
    talk = _talk_sdk_with_captured_voice_processor()
    talk.chat_sdk.send.return_value = MagicMock(text="response", stats=None)

    _run(talk.start_voice_session())
    voice_processor = talk.audio_client.start_voice_chat.call_args.args[0]

    _run(voice_processor("restart the server"))

    talk.chat_sdk.clear_history.assert_not_called()
    talk.chat_sdk.send.assert_called_once_with("restart the server")
