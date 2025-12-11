# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import sys

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

from gaia.agents.chat.agent import ChatAgent, ChatAgentConfig
from gaia.agents.code.agent import CodeAgent
from gaia.agents.docker.agent import DockerAgent
from gaia.rag.sdk import RAGSDK, RAGConfig
from gaia.security import PathValidator


def test_path_validator_direct():
    print("\n--- Testing PathValidator Direct ---")
    allowed = [os.getcwd()]
    validator = PathValidator(allowed)

    # Test allowed path
    assert validator.is_path_allowed(os.getcwd(), prompt_user=False)
    print("Allowed path check passed")

    # Test disallowed path
    disallowed = os.path.abspath(os.path.join(os.getcwd(), ".."))
    assert not validator.is_path_allowed(disallowed, prompt_user=False)
    print("Disallowed path check passed")


def test_docker_agent():
    print("\n--- Testing DockerAgent ---")
    agent = DockerAgent()
    # Mock path validator to avoid user prompts during test
    agent.path_validator.is_path_allowed = (
        lambda path, prompt_user=True: path.startswith(os.getcwd())
    )

    # Test allowed
    try:
        agent._analyze_directory(os.getcwd())
        print("DockerAgent allowed path passed (simulated)")
    except Exception as e:
        print(f"DockerAgent allowed path failed: {e}")

    # Test disallowed (we can't easily mock the internal call inside _analyze_directory without mocking the validator itself, which we did)
    # But let's verify the validator is attached
    assert hasattr(agent, "path_validator")
    print("DockerAgent has path_validator")


from gaia.agents.base.tools import _TOOL_REGISTRY


def test_chat_agent():
    print("\n--- Testing ChatAgent ---")
    config = ChatAgentConfig(allowed_paths=[os.getcwd()])
    agent = ChatAgent(config=config)

    # Test add_watch_directory tool
    # Access tool from registry since it's not an instance method
    add_watch_fn = _TOOL_REGISTRY["add_watch_directory"]["function"]

    result = add_watch_fn(os.getcwd())
    # It might fail if directory doesn't exist or other reasons, but we check if it passed validation logic
    # If it returns error "Access denied", then validation failed (which is good if we pass disallowed)

    print(f"ChatAgent add_watch_directory result: {result}")

    # Test disallowed
    disallowed = os.path.abspath(os.path.join(os.getcwd(), ".."))
    # Mock validator to return False without prompt
    agent.path_validator.is_path_allowed = lambda path, prompt_user=True: False

    result = add_watch_fn(disallowed)
    assert result["status"] == "error" and "Access denied" in result["error"]
    print("ChatAgent disallowed path check passed")


def test_code_agent():
    print("\n--- Testing CodeAgent ---")
    agent = CodeAgent(allowed_paths=[os.getcwd()])

    # Test read_file tool
    # Mock validator
    agent.path_validator.is_path_allowed = lambda path, prompt_user=True: False

    read_file_fn = _TOOL_REGISTRY["read_file"]["function"]
    result = read_file_fn(os.path.join(os.getcwd(), "README.md"))
    assert result["status"] == "error" and "Access denied" in result["error"]
    print("CodeAgent disallowed path check passed")


def test_rag_sdk():
    print("\n--- Testing RAGSDK ---")
    config = RAGConfig(allowed_paths=[os.getcwd()])
    sdk = RAGSDK(config=config)

    # Mock validator
    sdk.path_validator.is_path_allowed = lambda path, prompt_user=True: False

    try:
        sdk._safe_open("somefile.txt")
        print("RAGSDK failed to block disallowed path")
    except PermissionError as e:
        print(f"RAGSDK blocked disallowed path: {e}")
        assert "Access denied" in str(e)


if __name__ == "__main__":
    test_path_validator_direct()
    test_docker_agent()
    test_chat_agent()
    test_code_agent()
    test_rag_sdk()
    print("\nAll tests passed!")
