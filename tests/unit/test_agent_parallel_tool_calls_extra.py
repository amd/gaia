import json
import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY


def _make_agent(monkeypatch):
    class DummyAgent(Agent):
        def _register_tools(self):
            return None

    agent = DummyAgent(skip_lemonade=True, silent_mode=True)
    return agent


def test_parallel_calls_with_error(monkeypatch):
    # Tools: two success, one error
    def t_ok1(x=""):
        return {"status": "success", "value": f"ok1:{x}"}

    def t_err(y=""):
        return {"status": "error", "error": "boom"}

    def t_ok2(z=""):
        return {"status": "success", "value": f"ok2:{z}"}

    _TOOL_REGISTRY["ok1"] = {"function": t_ok1, "parameters": {}, "description": ""}
    _TOOL_REGISTRY["errtool"] = {"function": t_err, "parameters": {}, "description": ""}
    _TOOL_REGISTRY["ok2"] = {"function": t_ok2, "parameters": {}, "description": ""}

    agent = _make_agent(monkeypatch)

    envelope = {
        "__tool_calls__": [
            {"function": {"name": "ok1", "arguments": json.dumps({"x": "A"})}},
            {"function": {"name": "errtool", "arguments": json.dumps({"y": "B"})}},
            {"function": {"name": "ok2", "arguments": json.dumps({"z": "C"})}},
        ],
        "finish_reason": "",
    }

    # make send_messages return envelope
    responses = [type("R", (), {"text": json.dumps(envelope), "stats": {}})()]

    monkeypatch.setattr(agent.chat, "send_messages", lambda messages, system_prompt, tools: responses.pop(0))

    result = agent.process_query("run three tools", max_steps=10)

    # Ensure we got three tool entries in conversation
    tool_entries = [m for m in result["conversation"] if m.get("role") == "tool"]
    names = [t.get("name") for t in tool_entries]
    assert "ok1" in names and "errtool" in names and "ok2" in names

    # Find the errtool result and ensure it's an error
    err_entry = next((t for t in tool_entries if t.get("name") == "errtool"), None)
    assert err_entry is not None
    assert isinstance(err_entry.get("content"), dict) and err_entry["content"].get("status") == "error"


def test_plan_then_native_tool_calls(monkeypatch):
    # Tools
    def q(a=""):
        return {"status": "success", "value": f"q:{a}"}

    _TOOL_REGISTRY["q"] = {"function": q, "parameters": {}, "description": ""}

    agent = _make_agent(monkeypatch)

    envelope = {
        "__tool_calls__": [
            {"function": {"name": "q", "arguments": json.dumps({"a": "1"})}},
            {"function": {"name": "q", "arguments": json.dumps({"a": "2"})}},
        ],
        "finish_reason": "",
    }

    # Second LLM response will be a final answer
    final_answer = {"answer": "All done"}

    responses = [
        type("R", (), {"text": json.dumps(envelope), "stats": {}})(),
        type("R", (), {"text": json.dumps(final_answer), "stats": {}})(),
    ]

    def fake_send(messages, system_prompt, tools):
        return responses.pop(0)

    monkeypatch.setattr(agent.chat, "send_messages", fake_send)

    result = agent.process_query("do q twice and answer", max_steps=10)

    # Should have run two q tool calls and then returned the final answer
    tool_entries = [m for m in result["conversation"] if m.get("role") == "tool"]
    assert len([t for t in tool_entries if t.get("name") == "q"]) == 2
    assert result.get("result") and "All done" in result.get("result")


def test_notimplementederror_recovery_message(monkeypatch):
    agent = _make_agent(monkeypatch)

    # Make the parser raise NotImplementedError
    monkeypatch.setattr(agent, "_parse_llm_response", lambda r: (_ for _ in ()).throw(NotImplementedError("multiple")))

    # Make send_messages return something (will be ignored by parser)
    monkeypatch.setattr(agent.chat, "send_messages", lambda messages, system_prompt, tools: type("R", (), {"text": "{\"bad\":1}", "stats": {}})())

    result = agent.process_query("trigger parse error", max_steps=3)

    # Last user message in conversation should instruct about multiple tool calls
    user_msgs = [m for m in result["conversation"] if m.get("role") == "user"]
    assert any("MULTIPLE tool calls" in str(m.get("content")) or "single tool call" in str(m.get("content")) for m in user_msgs)