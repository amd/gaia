import json
import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY


def test_process_query_executes_multiple_native_tool_calls(monkeypatch):
    # Register two simple tools for the test
    def tool_one(a=""):
        return {"status": "success", "value": f"one:{a}"}

    def tool_two(b=""):
        return {"status": "success", "value": f"two:{b}"}

    _TOOL_REGISTRY["tool_one"] = {
        "function": tool_one,
        "parameters": {"a": {"type": "str", "required": False}},
        "description": "Test tool one",
    }
    _TOOL_REGISTRY["tool_two"] = {
        "function": tool_two,
        "parameters": {"b": {"type": "str", "required": False}},
        "description": "Test tool two",
    }

    class DummyAgent(Agent):
        def _register_tools(self):
            # No-op; tests inject tools directly into registry
            return None

    agent = DummyAgent(skip_lemonade=True, silent_mode=True)

    # Prepare a native envelope with two tool_calls (as Lemonade encodes them)
    envelope = {
        "__tool_calls__": [
            {"function": {"name": "tool_one", "arguments": json.dumps({"a": "X"})}},
            {"function": {"name": "tool_two", "arguments": json.dumps({"b": "Y"})}},
        ],
        "finish_reason": "",
    }

    # Monkeypatch send_messages to return our envelope as the LLM response
    # AgentSDK.send_messages returns an object with .text and .stats attributes
    monkeypatch.setattr(
        agent.chat,
        "send_messages",
        lambda messages, system_prompt, tools: type(
            "R", (), {"text": json.dumps(envelope), "stats": {}}
        )(),
    )

    result = agent.process_query("execute both tools", max_steps=6)

    # Verify both tool results were appended to conversation
    tool_names = [m.get("name") for m in result["conversation"] if m.get("role") == "tool"]
    assert "tool_one" in tool_names
    assert "tool_two" in tool_names
