# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A follow-up result is marked told only after a turn that answered.

Built with ``__new__`` so no RAG or LLM backend is needed; the memory hook the
override chains to is stubbed.
"""

import pytest

from gaia.agents.base.memory import MemoryMixin


@pytest.fixture
def agent(monkeypatch):
    from gaia_agent_chat.agent import ChatAgent

    monkeypatch.setattr(MemoryMixin, "_after_process_query", lambda self, u, a: None)
    built = ChatAgent.__new__(ChatAgent)
    built.told = []
    built.observers = []
    monkeypatch.setattr(
        ChatAgent, "follow_ups_told", lambda self, answered: self.told.append(answered)
    )
    return built


@pytest.mark.parametrize(
    "status,answer,told",
    [
        ("success", "The build passed.", True),
        ("incomplete", "Here is what I found so far.", True),
        ("failed", "The model server is not reachable.", False),
        ("success", "   ", False),
    ],
)
def test_only_an_answered_turn_marks_results_told(agent, status, answer, told):
    agent.last_result = {"status": status, "result": answer}

    agent._after_process_query("what happened?", answer)

    assert agent.told == [told]


def test_a_fired_runs_session_id_is_one_the_agent_accepts():
    from gaia_agent_chat.session import validate_session_id

    from gaia.daemon import followups

    validate_session_id(followups.run_session_id("0" * 32))
