# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os

# ChatAgent ships as the standalone gaia-agent-chat wheel (#1102).
try:
    from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig
except ImportError:
    raise SystemExit(
        "gaia-agent-chat is not installed — run: pip install gaia-agent-chat"
    )

from gaia.agents.base.tools import _TOOL_REGISTRY


def test_shell_injection():
    print("\n--- Testing Shell Injection ---")
    config = ChatAgentConfig(allowed_paths=[os.getcwd()])
    agent = ChatAgent(config=config)

    run_shell = _TOOL_REGISTRY["run_shell_command"]["function"]

    # Test 1: Chaining runs, but every command on the line is still validated
    print("Test 1: Command Chaining (ls; rm -rf /tmp/x)")
    result = run_shell("ls; rm -rf /tmp/x", working_directory=os.getcwd())
    if result["status"] == "error" and result.get("executed") is False:
        print(f"PASS: whole line refused before anything ran - {result['error']}")
    else:
        print("FAIL: a command outside the allowlist ran in a chain!")

    print("\nTest 1b: Allowed chaining (ls && pwd)")
    result = run_shell("ls && pwd", working_directory=os.getcwd())
    if result["status"] == "success" and len(result.get("steps", [])) == 2:
        print(f"PASS: both commands ran - steps={result['steps']}")
    else:
        print(f"FAIL: chaining did not run - {result.get('error')}")

    # Test 2: Pipe
    print("\nTest 2: Pipe (ls | grep py)")
    result = run_shell("ls | grep py", working_directory=os.getcwd())
    if result["status"] == "success" and ".py" in result.get("stdout", ""):
        print("PASS: the pipeline ran as a pipeline")
    else:
        print(f"CHECK: {result.get('error') or result.get('stderr')[:50]}...")


def test_argument_path_traversal():
    print("\n--- Testing Argument Path Traversal ---")
    config = ChatAgentConfig(allowed_paths=[os.getcwd()])
    agent = ChatAgent(config=config)

    # Mock validator to strictly enforce allowed paths
    # We need to patch the validator instance attached to the tool's 'self'
    # But the tool is a bound method of the agent instance (via mixin)
    # Wait, _TOOL_REGISTRY stores the function. When called, does it have access to 'self'?
    # The function in _TOOL_REGISTRY is the inner function 'run_shell_command'.
    # It captures 'self' from 'register_shell_tools'.
    # So if we modify 'agent.path_validator', it should affect the tool if 'self' refers to 'agent'.
    # Yes, ChatAgent inherits ShellToolsMixin, so 'self' is the ChatAgent instance.

    agent.path_validator.is_path_allowed = (
        lambda path, prompt_user=True: path.startswith(os.getcwd())
    )

    run_shell = _TOOL_REGISTRY["run_shell_command"]["function"]

    # Test 3: Cat disallowed file
    print("Test 3: Cat disallowed file (cat ../README.md)")
    # Assuming README.md is in parent (it is, based on previous context, but let's try a dummy parent path)
    parent_file = os.path.abspath(os.path.join(os.getcwd(), "..", "secret.txt"))

    # We expect this to be BLOCKED if we implement argument validation.
    # Currently it is NOT blocked by the code I saw.
    result = run_shell(f"cat {parent_file}", working_directory=os.getcwd())

    if result["status"] == "error" and "Access denied" in result["error"]:
        print("PASS: Path traversal blocked")
    else:
        print(f"FAIL: Path traversal allowed! Status: {result['status']}")
        # It might fail because file doesn't exist, but we want it to fail due to SECURITY.


if __name__ == "__main__":
    test_shell_injection()
    test_argument_path_traversal()
