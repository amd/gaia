# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A failed call carries what the task has already learned about failing."""

from gaia.agents.base.task_lessons import TaskLessons, attach

DENIED = {"status": "error", "error": "Access denied: C:\\ is not in allowed paths"}
REFUSED = {"status": "error", "error": "'python -c' is not allowed"}
RAN = {"status": "success", "stdout": "ok"}


def shell(command):
    return ("run_shell_command", {"command": command})


def test_a_first_failure_adds_nothing_its_own_result_says_it():
    lessons = TaskLessons()
    assert lessons.record(*shell("dir C:\\"), DENIED) is None


def test_a_second_failure_carries_every_lesson_so_far():
    lessons = TaskLessons()
    lessons.record(*shell("dir C:\\"), DENIED)
    lines = lessons.record(*shell('python -c "print(1)"'), REFUSED)
    assert len(lines) == 2
    assert lines[0].startswith("run_shell_command (dir): `dir C:\\` failed: Access")
    assert "python -c" in lines[1]


def test_a_repeat_of_the_same_program_is_counted():
    lessons = TaskLessons()
    lessons.record(*shell("dir C:\\"), DENIED)
    [line] = lessons.record(*shell("dir C:\\Users"), DENIED)
    assert "(2x)" in line and "dir C:\\Users" in line


def test_what_later_worked_for_that_program_is_kept():
    lessons = TaskLessons()
    lessons.record(*shell('python -c "print(1)"'), REFUSED)
    lessons.record(*shell("python -m pytest -q"), RAN)
    lines = lessons.record(*shell("dir C:\\"), DENIED)
    assert "later worked: `python -m pytest -q`" in lines[0]


def test_a_program_exiting_non_zero_is_not_a_lesson():
    lessons = TaskLessons()
    lessons.record(*shell("pytest"), {"status": "error", "return_code": 1})
    assert lessons.lines() == []


def test_a_new_turn_starts_clean():
    lessons = TaskLessons()
    lessons.record(*shell("dir C:\\"), DENIED)
    lessons.begin_turn()
    assert lessons.lines() == []


def test_attach_adds_the_lessons_and_keeps_the_result():
    out = attach(DENIED, ["x"])
    assert out["error"] == DENIED["error"]
    assert out["task_lessons"]["lessons"] == ["x"]
    assert attach(DENIED, None) is DENIED


def test_the_agent_loop_puts_the_lessons_on_the_next_failure(tmp_path, monkeypatch):
    import json
    from unittest.mock import MagicMock, patch

    from gaia.agents.base.agent import Agent
    from gaia.agents.base.tools import _TOOL_REGISTRY, tool

    class LessonAgent(Agent):
        def _get_system_prompt(self):
            return "Use the tools."

        def _register_tools(self):
            @tool
            def fetch(name: str) -> dict:
                """Always refused here."""
                return {"status": "error", "error": f"{name} is not allowed"}

    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            agent = LessonAgent(silent_mode=True, skip_lemonade=True)
        agent.streaming = False
        agent._tool_requires_confirmation = lambda *a, **kw: False
        agent.console = MagicMock()
        agent.console.cancelled = None
        turns = [
            {"tool": "fetch", "tool_args": {"name": "a"}},
            {"tool": "fetch", "tool_args": {"name": "b"}},
            {"answer": "Both were refused."},
        ]
        sent = []

        def send(messages, *a, **kw):
            sent.append([dict(m) for m in messages])
            return MagicMock(text=json.dumps(turns.pop(0)), stats={})

        agent.chat = MagicMock()
        agent.chat.send_messages.side_effect = send
        agent.process_query("Fetch a and b")
        results = [m["content"] for m in sent[-1] if m.get("role") == "tool"]
        assert "task_lessons" not in str(results[0])
        assert "task_lessons" in str(results[1]) and "(2x)" in str(results[1])
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(snapshot)
