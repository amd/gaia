# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ChatAgent defaults and console reporting, moved from gaia-agent-chat (#6099)."""


def test_config_defaults():
    from gaia_agent.chat_agent import ChatAgentConfig

    cfg = ChatAgentConfig()
    assert cfg.use_claude is False
    assert cfg.model_id is None
    assert cfg.prompt_profile == "full"


def test_memory_failure_is_reported_to_chat_console():
    from gaia.agents.base.memory import (
        MEMORY_UNAVAILABLE_DISABLED_BY_ENV,
        MEMORY_UNAVAILABLE_MODEL_NOT_PULLED,
        MemoryMixin,
    )

    class Console:
        def __init__(self):
            self.messages = []

        def print_warning(self, message):
            self.messages.append(message)

    class Agent:
        def __init__(self, reason):
            self._memory_unavailable_reason = reason
            self.console = Console()
            self._memory_unavailable_warning_reported = False

        def memory_unavailable_message(self):
            return "Embedding model is unavailable; pull it and restart."

    reported = Agent(MEMORY_UNAVAILABLE_MODEL_NOT_PULLED)
    assert MemoryMixin.report_memory_unavailable(reported) is True
    assert MemoryMixin.report_memory_unavailable(reported) is False
    assert reported.console.messages == [
        "Embedding model is unavailable; pull it and restart."
    ]

    for reason in (MEMORY_UNAVAILABLE_DISABLED_BY_ENV, None):
        quiet = Agent(reason)
        assert MemoryMixin.report_memory_unavailable(quiet) is False
        assert quiet.console.messages == []
