# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
GAIA Testing Utilities

Provides mocks, fixtures, and assertions for testing GAIA agents
without requiring real LLM/VLM services or databases.

Example:
    from gaia.testing import (
        ScriptedLLMClient,
        create_test_agent,
        assert_llm_called,
    )

    def test_my_agent():
        model = ScriptedLLMClient()
        model.push(call="search", query="data")
        model.push(say="Found results")
        agent = create_test_agent(MyAgent, llm_client=model)

        result = agent.process_query("Find data")

        assert_llm_called(model, times=2)
        assert "results" in result["result"].lower()
"""

# Assertions
from gaia.testing.assertions import (
    assert_agent_completed,
    assert_llm_called,
    assert_llm_prompt_contains,
    assert_no_errors,
    assert_result_has_keys,
    assert_result_value,
    assert_tool_args,
    assert_tool_called,
    assert_vlm_called,
)

# Fixtures
from gaia.testing.fixtures import (
    AgentTestContext,
    create_test_agent,
    temp_directory,
    temp_file,
)

# Mocks
from gaia.testing.mocks import (
    MockLLMProvider,
    MockToolExecutor,
    MockVLMClient,
)
from gaia.testing.scripted import ScriptedLLMClient, ScriptExhausted

# Re-export temp_db from database.testing for convenience
try:
    from gaia.database.testing import temp_db
except ImportError:
    # Database module may not be available
    temp_db = None

__all__ = [
    # Mocks
    "MockLLMProvider",
    "MockVLMClient",
    "MockToolExecutor",
    "ScriptedLLMClient",
    "ScriptExhausted",
    # Fixtures
    "create_test_agent",
    "temp_directory",
    "temp_file",
    "temp_db",
    "AgentTestContext",
    # Assertions
    "assert_llm_called",
    "assert_llm_prompt_contains",
    "assert_vlm_called",
    "assert_tool_called",
    "assert_tool_args",
    "assert_result_has_keys",
    "assert_result_value",
    "assert_agent_completed",
    "assert_no_errors",
]
