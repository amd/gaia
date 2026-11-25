# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared fixtures for the flagship agent's hub-package tests.

These tests cover the flagship's whole tool surface, so they run it on the full
tier. The default model is Gemma, whose compact tier withholds some tools; that
feature tier has its own tests in ``tests/unit/agents/test_model_features.py``.
"""

import pytest

from gaia.agents.base.model_features import TIER_ENV


# Module scope: module-scoped fixtures here build agents before any
# function-scoped fixture would run, and autouse ones go first within a scope.
@pytest.fixture(autouse=True, scope="module")
def _full_feature_tier():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(TIER_ENV, "full")
        yield


@pytest.fixture(autouse=True)
def _empty_hub_skill_catalog(monkeypatch):
    """Capture consults the hub catalog; these tests see an empty one by default."""
    from gaia.skills import capture

    monkeypatch.setattr(capture, "published_skill_names", lambda: frozenset())


@pytest.fixture
def user_terminal(monkeypatch):
    """Run the test as the user's terminal, not an agent tool or its child.

    Trust-changing skill calls refuse under an agent's marker, and pytest
    inherits it when GAIA runs this suite. Opt in per module with
    ``pytestmark = pytest.mark.usefixtures("user_terminal")``.
    """
    from gaia.skills import human_only

    monkeypatch.delenv(human_only.AGENT_CHILD_ENV, raising=False)
    token = human_only._IN_AGENT_TOOL.set(False)
    yield
    human_only._IN_AGENT_TOOL.reset(token)


@pytest.fixture
def flagship(make_flagship):
    """A network-isolated flagship for the prompt-prefix contract tests."""
    return make_flagship()


@pytest.fixture
def make_flagship(monkeypatch, tmp_path):
    """Build network-isolated flagships, one fresh session per call.

    Every session shares one GAIA home (one profile). ``build(work)`` starts a
    session in *work* (default ``tmp_path / "work"``) and returns ``(agent,
    model, doc)``. Imports are intentionally local: most hub tests do not build
    this agent and should not need its optional runtime dependencies merely to
    collect.
    """
    import contextlib
    import hashlib
    import importlib
    import json

    import numpy as np
    from gaiapy.agent import GaiaAgent
    from gaiapy.chat_agent import ChatAgent

    from gaia.agents.base.history import SessionHistory
    from gaia.agents.base.memory import MemoryMixin
    from gaia.agents.base.skill_synthesis import (
        DistilledProcedure,
        load_synthesis_config,
    )
    from gaia.agents.base.tools import _TOOL_REGISTRY

    def fake_embedding(text: str) -> np.ndarray:
        vec = np.zeros(768, dtype=np.float32)
        for word in str(text).lower().split():
            vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % 768] += 1.0
        norm = np.linalg.norm(vec)
        return vec / norm if norm else vec

    @contextlib.contextmanager
    def isolated_registry():
        saved = dict(_TOOL_REGISTRY)
        _TOOL_REGISTRY.clear()
        try:
            yield
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)

    class ScriptedModel:
        def __init__(self):
            self.requests = []
            self.slots = []
            self.script = []

        def chat(self, messages, model=None, stream=False, **kwargs):
            tools = kwargs.get("tools")
            if not tools:
                reply = "[]"
            else:
                self.requests.append(
                    (json.loads(json.dumps(messages)), json.loads(json.dumps(tools)))
                )
                self.slots.append(kwargs.get("id_slot"))
                step = self.script.pop(0)
                if isinstance(step, dict):
                    reply = json.dumps(
                        {
                            "__tool_calls__": [
                                {
                                    "id": step["id"],
                                    "type": "function",
                                    "function": {
                                        "name": step["name"],
                                        "arguments": json.dumps(step["args"]),
                                    },
                                }
                            ],
                            "finish_reason": "tool_calls",
                            "content": None,
                        }
                    )
                else:
                    reply = step
            return iter([reply]) if stream else reply

    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "home"))
    # Outranks GAIA_HOME, and the root conftest points it at one file per session.
    monkeypatch.setenv("GAIA_MEMORY_DB", str(tmp_path / "home" / "memory.db"))
    monkeypatch.setenv("GAIA_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.setenv("GAIA_PROJECT_MAP_AUTO_INDEX", "0")
    # Unroutable: nothing here may reach a developer's running server.
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9/api/v1")
    monkeypatch.delenv("GAIA_MEMORY_DISABLED", raising=False)
    monkeypatch.delenv("GAIA_DYNAMIC_TOOLS", raising=False)
    monkeypatch.setattr(MemoryMixin, "_embed_text", lambda self, t: fake_embedding(t))
    monkeypatch.setattr(
        ChatAgent,
        "_embed_texts_batch",
        lambda self, texts: np.stack([fake_embedding(t) for t in texts]),
    )
    built = []

    def build(work=None):
        work = work or tmp_path / "work"
        work.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(work)
        doc = work / "handbook.md"
        doc.write_text(
            "# Safety handbook\n\nWear goggles in the lab.\n", encoding="utf-8"
        )
        agent_config = importlib.import_module(
            "test_prompt_prefix_stability"
        ).GaiaAgentConfig
        agent = GaiaAgent(
            config=agent_config(
                silent_mode=True,
                streaming=True,
                # GaiaAgentConfig defaults allowed_paths to the user's home
                # directory, not cwd. CI's pytest temp dir is outside $HOME.
                allowed_paths=[str(work)],
            )
        )
        assert agent._memory_store is not None, "memory must be on for this test"
        agent.conversation_history = SessionHistory(
            str(tmp_path / f"history-{len(built)}.sqlite3")
        )

        class FakeRag:
            def __init__(self):
                self.indexed_files = set()
                self.chunks = []
                self.file_metadata = {}
                self.file_to_chunk_indices = {}

            def index_document(self, path, progress_callback=None, wait_callback=None):
                self.indexed_files.add(path)
                return {"success": True, "file_name": "handbook.md", "num_chunks": 1}

            def _retrieve_chunks_from_file(self, query, file_path):
                return [], []

            def document_scripts(self, _file_path):
                return ["Latin"]

        agent.rag = FakeRag()

        procedure = DistilledProcedure(
            name="read-the-handbook",
            when_to_use="questions about the safety handbook",
            body="1. index_document the handbook. 2. query_specific_file it.",
            tools_required=["index_document"],
        )
        monkeypatch.setattr(
            agent,
            "_recall_skills_for_turn",
            lambda goal: (
                [procedure] if "handbook" in goal.lower() else [],
                load_synthesis_config({}),
            ),
        )

        model = ScriptedModel()
        monkeypatch.setattr(agent.chat.llm_client, "chat", model.chat)
        for name, value in (
            ("get_last_usage", None),
            ("get_last_ttft_seconds", None),
            ("get_last_finish_reason", "stop"),
            ("get_last_reasoning", None),
        ):
            monkeypatch.setattr(
                agent.chat.llm_client, name, lambda *a, _v=value, **k: _v
            )
        monkeypatch.setattr(agent.chat, "get_stats", lambda: {})
        monkeypatch.setattr(
            agent.chat.llm_client._backend, "slot_count", lambda _model: 3
        )
        built.append(agent)
        return agent, model, doc

    with isolated_registry():
        try:
            yield build
        finally:
            for agent in built:
                agent.close()


class ScriptedApprover:
    """Answers a chat's approval cards from a script and records each one.

    Answers the way a person at ``AgentConsole`` does, so the agent files a
    deny as the user's own no. A card past the end of the script is denied and
    still recorded, so ``asked`` shows it.
    """

    def __init__(self, console):
        self.console = console
        self.answers = []
        self.asked = []
        console.confirm_tool_execution = self._confirm

    def _confirm(self, tool_name, tool_args):
        from gaia.agents.base.console import user_denial_message

        self.asked.append((tool_name, dict(tool_args or {})))
        self.console.note_prompt_answered(tool_name)
        if self.answers and self.answers.pop(0) == "allow":
            self.console._last_denial = None
            return True
        return self.console.deny_tool_execution(
            tool_name, user_denial_message(tool_name)
        )


class ScriptedChat:
    """One chat on the real turn loop: a scripted model and a scripted approver.

    ``say`` runs a turn the way the stdio host does and returns the answer.
    A model step is ``(tool_name, args)`` for a tool call or a string for
    text. ``results`` then holds what each call of that turn fed back to the
    model, in order, and ``offered`` the tool names of its last request.
    """

    def __init__(self, agent, model, work):
        self.agent = agent
        self.model = model
        self.work = work
        self.approver = ScriptedApprover(agent.console)
        self._calls = 0
        self._turn_ids = []
        self._turn_start = 0

    def say(self, query, model, approvals=()):
        from gaiapy import stdio

        steps = []
        self._turn_ids = []
        for step in model:
            if isinstance(step, tuple):
                self._calls += 1
                call_id = f"call-{id(self):x}-{self._calls}"
                self._turn_ids.append(call_id)
                step = {"id": call_id, "name": step[0], "args": step[1]}
            steps.append(step)
        self.model.script[:] = steps
        self.approver.answers[:] = list(approvals)
        self.approver.asked.clear()
        self._turn_start = len(self.model.requests)
        self.agent.conversation_history.prepare(self.agent, query)
        result = self.agent.process_query(query)
        stdio._record_turn(self.agent, query, result["result"], result)
        return result["result"]

    @property
    def unused_steps(self):
        """Model steps the turn ended without asking for."""
        return list(self.model.script)

    @property
    def requests(self):
        """This turn's model requests, as ``(messages, tools)`` pairs."""
        return self.model.requests[self._turn_start :]

    @property
    def offered(self):
        return {tool["function"]["name"] for tool in self.requests[-1][1]}

    @property
    def results(self):
        import json

        fed = {}
        for messages, _tools in self.requests:
            for message in messages:
                call_id = message.get("tool_call_id")
                if message.get("role") == "tool" and call_id in self._turn_ids:
                    content = message.get("content")
                    try:
                        fed[call_id] = json.loads(content)
                    except (TypeError, ValueError):
                        fed[call_id] = content
        return [fed[i] for i in self._turn_ids if i in fed]


@pytest.fixture
def open_chat(make_flagship, tmp_path):
    """Open chats in ONE process and one profile, each with its own folder."""
    opened = []

    def open_one():
        work = tmp_path / f"chat-{len(opened)}"
        agent, model, _doc = make_flagship(work)
        opened.append(ScriptedChat(agent, model, work))
        return opened[-1]

    return open_one
