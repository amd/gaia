# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deleting a chat deletes its conversation from Memory (#5931).

The delete finds a chat's memory turns by its id, so the agent built for that
chat must file them under it. The delete itself is tested in
``tests/unit/chat/ui/test_delete_chat_memory.py``.
"""

from unittest.mock import patch

import pytest

from gaia_agent.chat_agent import ChatAgent, ChatAgentConfig


def test_the_chat_agent_files_memory_under_the_chat_id():
    """ChatAgent hands init_memory its UI session id, even across a rebuild."""
    seen = []

    def fake_init(self, **kwargs):
        seen.append(kwargs.get("session_id"))
        raise RuntimeError("stop after init_memory")

    with patch.object(ChatAgent, "init_memory", fake_init):
        for _ in range(2):
            with pytest.raises(RuntimeError, match="stop after init_memory"):
                ChatAgent(ChatAgentConfig(ui_session_id="chat-123", silent_mode=True))
    assert seen == ["chat-123", "chat-123"]
