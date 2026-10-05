# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI remembers by default, and says why when it doesn't.

A new Agent UI user asked "remember my favourite fruit is mango" and got
"Memory is paused — this is a private session": memory defaulted off, and the
message named a private chat nobody had opened. The TUI remembered the same
sentence.
"""

from types import SimpleNamespace

from gaia.agents.base.memory import memory_off_reason
from gaia.ui.memory_settings import memory_enabled


class _Settings:
    def __init__(self, **values):
        self.values = values

    def get_setting(self, key, default=None):
        return self.values.get(key, default)


def test_memory_is_on_until_turned_off():
    assert memory_enabled(_Settings()) is True
    assert memory_enabled(_Settings(memory_enabled="false")) is False
    assert memory_enabled(_Settings(memory_enabled="true")) is True


def test_a_private_chat_says_it_is_private():
    agent = SimpleNamespace(_incognito_reason="private")
    assert "private chat" in memory_off_reason(agent)


def test_memory_turned_off_says_where_to_turn_it_on():
    message = memory_off_reason(SimpleNamespace(_incognito_reason="memory_off"))
    assert "Settings" in message
    assert "private" not in message


def test_an_unknown_reason_claims_no_private_chat():
    assert "private" not in memory_off_reason(SimpleNamespace())
