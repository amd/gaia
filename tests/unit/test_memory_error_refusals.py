# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tool errors stored as memory must be facts about the world, not the rules.

Auto-stored tool errors are replayed into every later system prompt under
"Known errors to avoid". In a benchmark sweep, 114 of 161 stored memories were
auto-stored errors, and most were permission refusals ("Command 'rm' is not in
the allowed list", "Access denied: ... is not in allowed paths"). Those describe
the permission settings of that run, go stale when the settings change, and
taught later sessions to avoid calls that were allowed.

Two rules:

* a call the permission layer refused before running (``executed: False``) is
  never stored, and it does not retire what is already stored either — a
  declined confirmation is no evidence the operation works now;
* a success retires only the errors stored for the same operation — a working
  ``ls`` is no evidence that ``pytest`` is on PATH.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_monitor_tools import FileToolsMixin
from gaia.agents.tools.rag_tools import RAGToolsMixin
from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.security import PathValidator


class _ToolBase:
    """Stands in for ``Agent._execute_tool``: returns the queued result."""

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        return self.next_result


class _Host(MemoryMixin, _ToolBase):
    def __init__(self, store: MemoryStore):
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = "s1"
        self.next_result: Any = None

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")

    def run(self, tool_name: str, tool_args: Dict[str, Any], result: Any) -> Any:
        self.next_result = result
        return self._execute_tool(tool_name, tool_args)


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


@pytest.fixture
def host(store):
    return _Host(store)


def _stored_errors(store: MemoryStore):
    return store.get_by_category("error", context="global", limit=100)


@contextmanager
def _registered(mixin: Any, register: str):
    """Register a mixin's tools into an empty global registry, then restore it."""
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        getattr(mixin, register)()
        yield _TOOL_REGISTRY
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


class TestRefusalsAreNotStored:
    def test_a_refused_shell_command_is_not_stored(self, host, store):
        refusal, _ = ShellToolsMixin()._validate_shell_command("rm -rf build")
        assert refusal["executed"] is False

        host.run("run_shell_command", {"command": "rm -rf build"}, refusal)

        assert _stored_errors(store) == []

    def test_a_refused_shell_operator_is_not_stored(self, host, store):
        refusal, _ = ShellToolsMixin()._validate_shell_command("ls && rm x")

        host.run("run_shell_command", {"command": "ls && rm x"}, refusal)

        assert _stored_errors(store) == []

    def test_a_path_outside_the_allowlist_is_not_stored(self, host, store, tmp_path):
        allowed = tmp_path / "work"
        allowed.mkdir()
        outside = tmp_path / "elsewhere" / "run_tests.py"
        mixin = FileIOToolsMixin()
        mixin.console = None
        mixin.path_validator = PathValidator(allowed_paths=[str(allowed)])
        saved = dict(_TOOL_REGISTRY)
        _TOOL_REGISTRY.clear()
        try:
            mixin.register_file_io_tools()
            write_file = _TOOL_REGISTRY["write_file"]["function"]
            refusal = write_file(file_path=str(outside), content="print(1)")
        finally:
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(saved)
        assert refusal["status"] == "error"
        assert refusal["executed"] is False
        assert not outside.exists()

        host.run("write_file", {"file_path": str(outside)}, refusal)

        assert _stored_errors(store) == []

    def test_a_refused_watch_directory_is_not_stored(self, host, store, tmp_path):
        allowed = tmp_path / "work"
        allowed.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        mixin = FileToolsMixin()
        mixin.path_validator = PathValidator(allowed_paths=[str(allowed)])
        mixin.watch_directories = []
        with _registered(mixin, "register_file_tools") as registry:
            refusal = registry["add_watch_directory"]["function"](
                directory=str(outside)
            )
        assert refusal["status"] == "error"
        assert refusal["executed"] is False
        assert mixin.watch_directories == []

        host.run("add_watch_directory", {"directory": str(outside)}, refusal)

        assert _stored_errors(store) == []

    def test_a_refused_index_document_is_not_stored(self, host, store, tmp_path):
        allowed = tmp_path / "work"
        allowed.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        doc = outside / "notes.txt"
        doc.write_text("hello")
        validator = PathValidator(allowed_paths=[str(allowed)])
        mixin = RAGToolsMixin()
        mixin.rag = object()  # truthy; the refusal fires before RAG is touched
        mixin.indexed_files = set()
        mixin._is_path_allowed = lambda path: validator.is_path_allowed(
            path, prompt_user=False
        )
        with _registered(mixin, "register_rag_tools") as registry:
            refusal = registry["index_document"]["function"](file_path=str(doc))
        assert refusal["status"] == "error"
        assert refusal["executed"] is False
        assert mixin.indexed_files == set()

        host.run("index_document", {"file_path": str(doc)}, refusal)

        assert _stored_errors(store) == []

    def test_a_tool_that_ran_and_failed_is_still_stored(self, host, store):
        failure = {
            "status": "error",
            "error": "Skill 'coding' needs the 'pytest' command, which is not on PATH.",
        }

        host.run("load_skill", {"name": "coding"}, failure)

        rows = _stored_errors(store)
        assert [r["content"] for r in rows] == [
            "load_skill: Skill 'coding' needs the 'pytest' command, "
            "which is not on PATH."
        ]
        assert rows[0]["source"] == "error_auto"
        assert rows[0]["metadata"] == {"operation": "load_skill coding"}


class TestSuccessRetiresOnlyTheSameOperation:
    def _store_pytest_error(self, host):
        host.run(
            "run_shell_command",
            {"command": "pytest tests/ -q"},
            {"status": "error", "error": "pytest: error: unrecognized arguments"},
        )

    def test_an_unrelated_success_keeps_the_error(self, host, store):
        self._store_pytest_error(host)

        host.run("run_shell_command", {"command": "ls -la"}, {"status": "success"})

        assert len(_stored_errors(store)) == 1

    def test_the_same_binary_working_again_retires_it(self, host, store):
        self._store_pytest_error(host)

        host.run(
            "run_shell_command",
            {"command": "pytest tests/unit"},
            {"status": "success"},
        )

        assert _stored_errors(store) == []

    def test_path_tools_are_keyed_by_path(self, host, store):
        host.run(
            "read_file",
            {"file_path": "/work/a.txt"},
            {"status": "error", "error": "UnicodeDecodeError: invalid start byte"},
        )

        host.run("read_file", {"file_path": "/work/b.txt"}, {"status": "success"})
        assert len(_stored_errors(store)) == 1

        host.run("read_file", {"file_path": "/work/a.txt"}, {"status": "success"})
        assert _stored_errors(store) == []

    def test_a_row_stored_without_an_operation_is_retired_by_tool(self, host, store):
        """Rows written before the operation key existed keep the old rule."""
        store.store(
            category="error",
            content="run_shell_command: Command 'rm' is not in the allowed list",
            source="error_auto",
            context="global",
        )

        host.run("run_shell_command", {"command": "ls"}, {"status": "success"})

        assert _stored_errors(store) == []


class _ScriptedConsole(AgentConsole):
    """A real console whose confirmation answer each test step sets."""

    approve = True

    def confirm_tool_execution(self, tool_name, tool_args):
        return self.approve


class _MemoryAgent(MemoryMixin, Agent):
    """A real agent on a real store, with one confirmation-gated tool."""

    CONFIRMATION_REQUIRED_TOOLS = frozenset({"purge_cache"})

    def __init__(self, store: MemoryStore, **kwargs):
        self._console_override = _ScriptedConsole()
        self.ran: list = []
        self.outcome: Dict[str, Any] = {"status": "success"}
        super().__init__(**kwargs)
        self._memory_store = store
        self._memory_context = "global"
        self._memory_session_id = "s1"

    def _get_system_prompt(self) -> str:
        return "memory"

    def _create_console(self):
        return self._console_override

    def _embed_text(self, text):
        raise RuntimeError("no embedder in unit tests")

    def _register_tools(self) -> None:
        agent = self

        @tool
        def purge_cache(path: str) -> Dict[str, Any]:
            """Irreversible cache purge. Requires confirmation."""
            agent.ran.append(path)
            return agent.outcome


@pytest.fixture
def gated_agent(store):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            yield _MemoryAgent(store, silent_mode=True, skip_lemonade=True)
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


class TestADeclinedConfirmationRetiresNothing:
    """A refusal is not a success, so it must not clear the operation's errors.

    ``Agent._execute_tool`` answers a declined confirmation with
    ``{"status": "denied"}`` — not ``"error"`` — so the retire branch used to
    fire for an operation that never ran.
    """

    def test_a_decline_keeps_the_stored_error(self, gated_agent, store):
        args = {"path": "/work/cache"}
        gated_agent.outcome = {
            "status": "error",
            "error": "purge failed: cache is locked by another process",
        }
        gated_agent._execute_tool("purge_cache", args)
        assert len(_stored_errors(store)) == 1

        gated_agent.console.approve = False
        denied = gated_agent._execute_tool("purge_cache", args)

        assert denied["status"] == "denied"
        assert gated_agent.ran == ["/work/cache"]  # the body never ran again
        assert len(_stored_errors(store)) == 1

    def test_a_real_success_still_retires_it(self, gated_agent, store):
        args = {"path": "/work/cache"}
        gated_agent.outcome = {"status": "error", "error": "cache is locked"}
        gated_agent._execute_tool("purge_cache", args)
        assert len(_stored_errors(store)) == 1

        gated_agent.outcome = {"status": "success"}
        gated_agent._execute_tool("purge_cache", args)

        assert _stored_errors(store) == []


class TestOperationKey:
    @pytest.mark.parametrize(
        "tool_name,args,expected",
        [
            ("run_shell_command", {"command": "pytest -q"}, "run_shell_command pytest"),
            (
                "run_shell_command",
                {"command": "/usr/local/bin/Pytest x"},
                "run_shell_command pytest",
            ),
            ("read_file", {"file_path": "/a/b.py"}, "read_file /a/b.py"),
            ("load_skill", {"name": "coding"}, "load_skill coding"),
            (
                "find_files",
                {"query": "x", "limit": 3},
                'find_files {"limit": 3, "query": "x"}',
            ),
            ("list_windows", None, "list_windows {}"),
            (
                "run_shell_command",
                {"command": "env TOYBOX_CLOCK=frozen pytest -q"},
                "run_shell_command pytest",
            ),
            (
                "run_shell_command",
                {"command": "PYTHONPATH=. python3 -m pytest tests"},
                "run_shell_command pytest",
            ),
            ("run_shell_command", {"command": "python -m"}, "run_shell_command python"),
            ("run_shell_command", {"command": "env"}, "run_shell_command env"),
        ],
    )
    def test_keys(self, tool_name, args, expected):
        assert MemoryMixin._operation_key(tool_name, args) == expected
