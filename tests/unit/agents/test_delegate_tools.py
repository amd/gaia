# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``delegate_task``: a fresh child agent does the subtask, the parent keeps only
its short result plus evidence, and pays for the child's tokens."""

# pylint: disable=protected-access,unused-argument

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools import delegate_tools
from gaia.agents.tools.delegate_tools import (
    DELEGATE_SYSTEM_PROMPT,
    ORCHESTRATE_SYSTEM_PROMPT,
    ORCHESTRATOR_TOOLS,
    DelegateToolsMixin,
)
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

_STATS = {"input_tokens": 100, "output_tokens": 10, "cached_tokens": 40}

#: Model replies per delegation depth; each agent copies its own queue at init.
SCRIPTS: Dict[int, List[Any]] = {}


@dataclass
class KidConfig:
    model_id: str = DEFAULT_MODEL_NAME
    max_steps: int = 6
    silent_mode: bool = True
    output_handler: Any = None
    delegate_mode: str = "tool"
    delegate_max_steps: int = 4
    delegate_depth: int = 0


class Kid(DelegateToolsMixin, Agent):
    """Minimal host: a dataclass config, a note-writing tool, a fake shell."""

    def __init__(self, config: KidConfig | None = None):
        self.config = config or KidConfig()
        with patch("gaia.agents.base.agent.AgentSDK"):
            super().__init__(
                model_id=self.config.model_id,
                max_steps=self.config.max_steps,
                silent_mode=self.config.silent_mode,
                skip_lemonade=True,
            )
        self.chat = _scripted_chat(SCRIPTS.get(self.config.delegate_depth, []))

    def _get_system_prompt(self) -> str:
        return "kid"

    def _register_tools(self) -> None:
        if self._resolve_delegate_enabled():
            self.register_delegate_tools()

        @tool
        def write_note(path: str, text: str) -> dict:
            """Write text to a file."""
            Path(path).write_text(text, encoding="utf-8")
            return {"status": "success", "path": path}

        @tool
        def run_shell_command(command: str) -> dict:
            """Run a shell command (fake)."""
            return {
                "status": "success",
                "stdout": "1 passed in 0.01s",
                "return_code": 0,
            }

        self._enforce_delegate_toggle()


def _scripted_chat(replies: List[Any]) -> MagicMock:
    queue = list(replies)
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})

    def _send(*_, **__):
        if not queue:
            raise AssertionError("the model was asked more often than scripted")
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(text=item, stats=dict(_STATS))

    chat.send_messages = MagicMock(side_effect=_send)
    return chat


def _call(name: str, **args) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": f"call_{name}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
            "finish_reason": "tool_calls",
        }
    )


def _answer(text: str) -> str:
    return json.dumps({"thought": "done", "answer": text})


_BRIEF = {
    "goal": "find the note",
    "scope": "the working directory",
    "done_when": "the note is found",
    "return_format": "one line",
}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon-home"))
    monkeypatch.delenv("GAIA_DELEGATE", raising=False)
    monkeypatch.delenv("GAIA_DELEGATE_MAX_STEPS", raising=False)
    monkeypatch.delenv("GAIA_DELEGATE_MAX_CHILDREN", raising=False)
    monkeypatch.delenv("GAIA_PROJECT_ROOT", raising=False)
    saved = dict(_TOOL_REGISTRY)
    SCRIPTS.clear()
    yield
    SCRIPTS.clear()
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "existing.txt").write_text("v1\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )
    monkeypatch.chdir(root)
    monkeypatch.setenv("GAIA_PROJECT_ROOT", str(root))
    return root


def _approving_parent() -> Kid:
    """A parent whose console pre-approves gated tools, as the bench's does."""
    parent = Kid()
    parent.console.auto_approve_gated_tools = True
    return parent


def _last_tool_result(agent: Kid) -> dict:
    """The delegate_task result as the parent's model saw it."""
    messages = agent.chat.send_messages.call_args_list[-1].kwargs["messages"]
    content = [m for m in messages if m.get("role") == "tool"][-1]["content"]
    if isinstance(content, list):
        content = "".join(block.get("text", "") for block in content)
    return json.loads(content)


# ---------------------------------------------------------------------------


def test_child_starts_fresh_with_identical_prompt_and_no_delegate_tool():
    parent = _approving_parent()
    parent.conversation_history = [{"role": "user", "content": "earlier turn"}]
    child = parent._spawn_child()
    assert child.conversation_history == []
    assert child.console.auto_approve_gated_tools is True
    assert child.config.delegate_depth == 1
    assert child.max_steps == 4
    assert child.silent_mode is True
    assert "delegate_task" in parent._tools_registry
    assert "delegate_task" not in child._tools_registry
    assert DELEGATE_SYSTEM_PROMPT in parent.system_prompt
    assert DELEGATE_SYSTEM_PROMPT not in child.system_prompt
    assert child.system_prompt == parent.system_prompt.replace(
        DELEGATE_SYSTEM_PROMPT + "\n\n", ""
    )


def test_prompt_unchanged_when_delegation_off():
    assert (
        DELEGATE_SYSTEM_PROMPT not in Kid(KidConfig(delegate_mode="off")).system_prompt
    )


def test_child_cannot_delegate_even_when_env_enables_it(monkeypatch):
    monkeypatch.setenv("GAIA_DELEGATE", "1")
    child = Kid()._spawn_child()
    assert "delegate_task" not in child._tools_registry
    assert child._resolve_delegate_enabled() is False


def test_tool_offered_only_when_enabled(monkeypatch):
    assert "delegate_task" not in Kid(KidConfig(delegate_mode="off"))._tools_registry
    monkeypatch.setenv("GAIA_DELEGATE", "1")
    assert "delegate_task" in Kid(KidConfig(delegate_mode="off"))._tools_registry
    monkeypatch.setenv("GAIA_DELEGATE", "0")
    assert "delegate_task" not in Kid(KidConfig(delegate_mode="tool"))._tools_registry


def test_step_budget_enforced_and_reported(repo, monkeypatch):
    monkeypatch.setenv("GAIA_DELEGATE_MAX_STEPS", "2")
    SCRIPTS[1] = [
        _call("run_shell_command", command="ls"),
        _call("run_shell_command", command="ls -a"),
        "ran out of steps",  # the cap's closing no-tool reply
    ]
    result = _approving_parent()._delegate_task(**_BRIEF)
    assert result["hit_step_limit"] is True
    # Two tool steps, plus the closing reply the cap asks for.
    assert result["steps"] == 3
    assert result["evidence"]["commands_run"] == ["ls", "ls -a"]
    assert result["result"].startswith("ran out of steps")
    assert result["status"] == "success"


def test_evidence_from_git_repo(repo):
    SCRIPTS[1] = [
        _call("write_note", path=str(repo / "new.txt"), text="hello"),
        _call("write_note", path=str(repo / "existing.txt"), text="v2\n"),
        _call("run_shell_command", command="pytest -q"),
        _answer("wrote the note"),
    ]
    result = _approving_parent()._delegate_task(**_BRIEF)
    evidence = result["evidence"]
    assert evidence["files_changed"] == [
        {"path": "existing.txt", "added": 1, "removed": 1},
        {"path": "new.txt", "added": 1, "removed": 0},
    ]
    assert evidence["commands_run"] == ["pytest -q"]
    assert evidence["commands_total"] == 1
    assert evidence["tests"] == {
        "ran": 1,
        "command": "pytest -q",
        "check": "pytest",
        "failed": False,
        "summary": "1 passed in 0.01s",
    }
    assert result["tool_calls"] == 3
    assert result["steps"] == 4
    assert result["tokens"] == {"input": 400, "output": 40, "cached": 160}


def test_evidence_from_plain_directory_uses_mtimes(tmp_path, monkeypatch):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "a.txt").write_text("a")
    monkeypatch.chdir(root)
    SCRIPTS[1] = [
        _call("write_note", path=str(root / "b.txt"), text="b"),
        _answer("ok"),
    ]
    parent = Kid()
    parent._verification_project_root = lambda: str(root)
    result = parent._delegate_task(**_BRIEF)
    assert result["evidence"]["files_changed"] == [{"path": "b.txt"}]


def test_missing_workdir_fails_loudly(monkeypatch, tmp_path):
    parent = Kid()
    parent._verification_project_root = lambda: str(tmp_path / "gone")
    result = parent._delegate_task(**_BRIEF)
    assert result["status"] == "error"
    assert "does not exist" in result["error"]


def test_child_provider_error_surfaces(repo):
    SCRIPTS[1] = [ConnectionError("backend refused the call")]
    result = Kid()._delegate_task(**_BRIEF)
    assert result["status"] == "error"
    assert "backend refused" in result["error"]
    assert result["transcript"].startswith("output_")


def test_child_construction_error_surfaces(repo, monkeypatch):
    def _boom(self):
        raise RuntimeError("no config for a worker")

    monkeypatch.setattr(Kid, "_child_config", _boom)
    result = Kid()._delegate_task(**_BRIEF)
    assert result["status"] == "error"
    assert "no config for a worker" in result["error"]
    assert result["transcript"] is None


def test_incomplete_brief_is_refused():
    result = Kid()._delegate_task("goal", " ", "done", "fmt")
    assert result["status"] == "error"
    assert "scope" in result["error"]


def test_parent_conversation_grows_only_by_small_result_and_tokens(repo):
    long_answer = "x" * 20000
    SCRIPTS[0] = [_call("delegate_task", **_BRIEF), _answer("parent done")]
    SCRIPTS[1] = [
        _call("run_shell_command", command="ls"),
        _call("run_shell_command", command="cat notes.txt"),
        _answer(long_answer),
    ]
    parent = _approving_parent()
    outcome = parent.process_query("do the thing")

    seen = _last_tool_result(parent)
    assert len(json.dumps(seen)) <= 8000
    assert seen["result"]["truncated"] is True
    assert seen["evidence"]["commands_run"] == ["ls", "cat notes.txt"]
    assert seen["result"]["artifact"] != seen["transcript"]
    assert "x" * 100 in seen["result"]["head"]
    # The whole answer and the child's conversation are one page-read away.
    transcript = json.loads(delegate_tools.store_for(parent).text(seen["transcript"]))
    assert transcript["result"].startswith(long_answer)
    assert len(transcript["conversation"]) > 3

    # Parent: 2 model calls; child: 3. All five counted in the parent's totals.
    assert outcome["input_tokens"] == 500
    assert outcome["output_tokens"] == 50
    assert parent.delegated_tokens == {
        "input": 300,
        "output": 30,
        "cached": 120,
        "children": 1,
    }
    delegated = [
        m
        for m in outcome["conversation"]
        if isinstance(m.get("content"), dict) and m["content"].get("delegated")
    ]
    assert len(delegated) == 1
    assert delegated[0]["content"]["performance_stats"]["input_tokens"] == 300
    # The child's own tool results never reach the parent's conversation.
    assert "notes.txt" not in json.dumps(
        [m for m in outcome["conversation"] if m.get("role") != "tool"]
    )


def _paragraphs(total: int, seed: str) -> str:
    """Prose with paragraph structure, ``total`` characters long."""
    text = ""
    n = 0
    while len(text) < total:
        text += f"{seed} paragraph {n} " + ("word " * 30).strip() + "\n\n"
        n += 1
    return text[:total]


def test_answer_up_to_6500_chars_comes_back_whole(repo):
    answer = _paragraphs(6500, "whole")
    SCRIPTS[1] = [_answer(answer)]
    result = Kid()._delegate_task(**_BRIEF)
    assert result["result"] == answer
    assert len(json.dumps(result)) <= 8000


def test_long_answer_comes_back_as_its_own_chunk_index(repo):
    answer = _paragraphs(20000, "long")
    SCRIPTS[1] = [_answer(answer)]
    parent = Kid()
    result = parent._delegate_task(**_BRIEF)
    assert len(json.dumps(result)) <= 8000
    assert result["truncated"] is True
    assert result["original_chars"] == len(answer)
    assert result["artifact"] != result["transcript"]
    assert result["transcript"].startswith("output_")
    assert result["shown"] and result["index"]
    store = delegate_tools.store_for(parent)
    assert store.text(result["artifact"]) == answer
    for entry in result["index"]:
        page = store.read(result["artifact"], entry=entry["n"])
        assert (
            page["content"]
            == answer[entry["offset"] : entry["offset"] + entry["length"]]
        )
    assert "long paragraph" in json.loads(store.text(result["transcript"]))["result"]


def test_structureless_long_answer_is_elided_with_its_own_archive(repo):
    answer = "y" * 50000
    SCRIPTS[1] = [_answer(answer)]
    parent = Kid()
    result = parent._delegate_task(**_BRIEF)
    assert len(json.dumps(result)) <= 8000
    assert result["result"]["original_chars"] >= 50000
    assert result["result"]["artifact"] != result["transcript"]
    assert delegate_tools.store_for(parent).text(result["result"]["artifact"]) == answer


def test_no_check_run_reports_zero(repo):
    SCRIPTS[1] = [_answer("looked only")]
    assert Kid()._delegate_task(**_BRIEF)["evidence"]["tests"] == {"ran": 0}


def test_prompt_fragment_names_the_implementation_split():
    prompt = Kid().system_prompt
    for phrase in (
        "Plan first",
        "implementation of one component or change including its tests",
        "which test command must pass",
        "no memory of this conversation",
        "trust files_changed and the test output",
        "final end-to-end verification",
    ):
        assert phrase in prompt


def test_commands_lose_the_cd_prefix_and_are_capped(repo, monkeypatch):
    monkeypatch.setenv("GAIA_DELEGATE_MAX_STEPS", "20")
    SCRIPTS[1] = [
        _call("run_shell_command", command=f"cd {repo} && pytest -q tests"),
        *[_call("run_shell_command", command=f"ls {i}") for i in range(14)],
        _answer("done"),
    ]
    result = _approving_parent()._delegate_task(**_BRIEF)
    commands = result["evidence"]["commands_run"]
    assert commands[0] == "pytest -q tests"
    assert len(commands) == 12
    assert result["evidence"]["commands_total"] == 15


def test_registry_exposes_delegate_mixin():
    from gaia.agents.registry import KNOWN_TOOLS

    assert KNOWN_TOOLS["delegate"] == (
        "gaia.agents.tools.delegate_tools",
        "DelegateToolsMixin",
    )


# ── modes, kinds and the children cap ────────────────────────────────────────


def test_env_parses_every_mode_and_rejects_the_rest(monkeypatch):
    for raw, mode in (
        ("1", "tool"),
        ("true", "tool"),
        ("on", "tool"),
        ("tool", "tool"),
        ("0", "off"),
        ("off", "off"),
        ("orchestrate", "orchestrate"),
        (" ORCHESTRATE ", "orchestrate"),
    ):
        monkeypatch.setenv("GAIA_DELEGATE", raw)
        assert delegate_tools.delegate_env_override() == mode, raw
    monkeypatch.setenv("GAIA_DELEGATE", "sometimes")
    with pytest.raises(ValueError, match="GAIA_DELEGATE must be one of"):
        delegate_tools.delegate_env_override()
    with pytest.raises(ValueError, match="GAIA_DELEGATE must be one of"):
        Kid()
    monkeypatch.delenv("GAIA_DELEGATE")
    with pytest.raises(ValueError, match="delegate_mode must be one of"):
        Kid(KidConfig(delegate_mode="banana"))


def test_max_children_env_is_validated(monkeypatch):
    monkeypatch.setenv("GAIA_DELEGATE_MAX_CHILDREN", "3")
    assert Kid()._delegate_max_children() == 3
    monkeypatch.setenv("GAIA_DELEGATE_MAX_CHILDREN", "0")
    with pytest.raises(ValueError, match="at least 1"):
        Kid()._delegate_max_children()
    monkeypatch.setenv("GAIA_DELEGATE_MAX_CHILDREN", "many")
    with pytest.raises(ValueError, match="must be an integer"):
        Kid()._delegate_max_children()
    monkeypatch.delenv("GAIA_DELEGATE_MAX_CHILDREN")
    assert Kid()._delegate_max_children() == 12


def test_orchestrate_paragraph_replaces_the_tool_one_and_never_reaches_the_child(
    monkeypatch,
):
    monkeypatch.setenv("GAIA_DELEGATE", "orchestrate")
    parent = Kid()
    assert parent._resolve_delegate_mode() == "orchestrate"
    assert ORCHESTRATE_SYSTEM_PROMPT in parent.system_prompt
    assert DELEGATE_SYSTEM_PROMPT not in parent.system_prompt
    for phrase in (
        "You are the orchestrator",
        "cannot read, search, run or edit anything",
        'kind="investigate"',
        'kind="implement"',
        'kind="verify"',
        "Answer only from the workers' evidence",
    ):
        assert phrase in ORCHESTRATE_SYSTEM_PROMPT
    child = parent._spawn_child()
    assert child._resolve_delegate_mode() == "off"
    assert ORCHESTRATE_SYSTEM_PROMPT not in child.system_prompt
    assert "delegate_task" not in child._tools_registry
    assert child._orchestrator_refusal("write_note") is None
    monkeypatch.delenv("GAIA_DELEGATE")
    tool_mode = Kid(KidConfig(delegate_mode="tool"))
    assert DELEGATE_SYSTEM_PROMPT in tool_mode.system_prompt
    assert ORCHESTRATE_SYSTEM_PROMPT not in tool_mode.system_prompt


def test_orchestrator_refuses_every_tool_but_its_own(monkeypatch):
    assert Kid()._orchestrator_refusal("write_note") is None
    monkeypatch.setenv("GAIA_DELEGATE", "orchestrate")
    parent = Kid()
    for name in ORCHESTRATOR_TOOLS:
        assert parent._orchestrator_refusal(name) is None
    refusal = parent._orchestrator_refusal("write_note")
    assert refusal["status"] == "error"
    assert refusal["executed"] is False
    assert "delegate_task and read_tool_output" in refusal["error"]


def test_kind_is_recorded_in_result_brief_and_stats(repo):
    SCRIPTS[1] = [_answer("all green")]
    parent = _approving_parent()
    parent._turn_conversation = []
    result = parent._delegate_task(**_BRIEF, kind="verify")
    assert result["kind"] == "verify"
    archived = json.loads(delegate_tools.store_for(parent).text(result["transcript"]))
    assert archived["kind"] == "verify"
    assert "Kind: verify — run the checks and report" in archived["brief"]
    assert parent._turn_conversation[-1]["content"]["kind"] == "verify"
    SCRIPTS[1] = [_answer("default")]
    assert _approving_parent()._delegate_task(**_BRIEF)["kind"] == "implement"


def test_unknown_kind_is_refused_without_spawning(repo):
    parent = _approving_parent()
    with patch.object(parent, "_spawn_child") as spawn:
        result = parent._delegate_task(**_BRIEF, kind="ponder")
    assert result["status"] == "error"
    assert "investigate, implement, verify" in result["error"]
    spawn.assert_not_called()


def test_children_cap_errors_on_the_next_call(repo, monkeypatch):
    monkeypatch.setenv("GAIA_DELEGATE_MAX_CHILDREN", "2")
    SCRIPTS[1] = [_answer("done")]
    parent = _approving_parent()
    assert parent._delegate_task(**_BRIEF)["status"] == "success"
    assert parent._delegate_task(**_BRIEF)["status"] == "success"
    with patch.object(parent, "_spawn_child") as spawn:
        third = parent._delegate_task(**_BRIEF)
    spawn.assert_not_called()
    assert third["status"] == "error"
    assert "2 workers have already run (delegate_max_children=2)" in third["error"]
    assert "finish now" in third["error"]
    assert parent.delegated_tokens["children"] == 2
