# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship sends earlier requests' reasoning back, so turns stay cached."""

# pylint: disable=protected-access

import pytest
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig
from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

PRIOR_TURN = [
    {"role": "user", "content": "Read notes.md"},
    {
        "role": "assistant",
        "content": None,
        "reasoning_content": "Read the file first.",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }
        ],
    },
    {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "x"},
    {"role": "assistant", "content": "It says x."},
]


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    monkeypatch.setenv("GAIA_PROJECT_MAP_AUTO_INDEX", "0")
    return monkeypatch


def test_flagship_resends_earlier_reasoning_by_default(env):
    agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True))
    assert agent.resend_reasoning_across_requests is True

    agent.conversation_history = [dict(m) for m in PRIOR_TURN]
    # The step the model already saw must come back byte-identical, reasoning
    # included, or the next request diverges at the previous turn's first step.
    assert agent._history_for_request() == PRIOR_TURN


def test_chat_agent_keeps_its_default_and_the_flagship_can_opt_out(env):
    assert (
        ChatAgent(
            config=ChatAgentConfig(silent_mode=True)
        ).resend_reasoning_across_requests
        is False
    )
    agent = GaiaAgent(
        config=GaiaAgentConfig(silent_mode=True, resend_reasoning_across_requests=False)
    )
    agent.conversation_history = [dict(m) for m in PRIOR_TURN]
    assert all("reasoning_content" not in m for m in agent._history_for_request())


class _ReasonerClient:
    """A Lemonade-proxied reasoner: forwarded reasoning, yet its API refuses
    another model's reasoning in input."""

    accepts_reasoning_history = True


def _switch_to_cloud_reasoner(monkeypatch, agent, target):
    from gaia_agent import stdio

    monkeypatch.setattr(stdio, "_lemonade_url", lambda agent: "http://x/api/v1")
    monkeypatch.setattr(stdio, "_lemonade_models", lambda base_url: [target])
    monkeypatch.setattr(stdio, "create_client", lambda **kw: _ReasonerClient())
    return stdio.switch_model(agent, target)


def test_model_switch_stops_sending_the_old_models_reasoning(env):
    agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True))
    agent.conversation_history = [dict(m) for m in PRIOR_TURN]

    _switch_to_cloud_reasoner(env, agent, "fireworks.deepseek-reasoner")

    assert agent.chat.llm_client.accepts_reasoning_history is True
    wire = [
        agent.chat._structure_history_message(m) for m in agent._history_for_request()
    ]
    assert all("reasoning_content" not in m for m in wire)
    # The conversation itself survives the switch untouched.
    assert agent.conversation_history == PRIOR_TURN


def test_failed_model_switch_keeps_resending_reasoning(env):
    agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True))

    def broken_rebuild():
        raise ValueError("prompt composition failed")

    env.setattr(agent, "rebuild_system_prompt", broken_rebuild)
    with pytest.raises(RuntimeError, match="rolled back"):
        _switch_to_cloud_reasoner(env, agent, "fireworks.deepseek-reasoner")

    assert agent.resend_reasoning_across_requests is True
