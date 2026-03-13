# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for SharedAgentState — thread-safe singleton holding MemoryDB + KnowledgeDB.

Tests singleton pattern, thread safety, two-DB-only constraint,
and no gaia_code imports.
"""

import ast
import inspect
import threading

import pytest

from gaia.agents.base.shared_state import SharedAgentState, get_shared_state


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the SharedAgentState singleton before each test."""
    SharedAgentState._instance = None
    yield
    SharedAgentState._instance = None


@pytest.fixture
def workspace(tmp_path):
    """Provide a temp workspace directory."""
    return tmp_path / "workspace"


class TestSharedStateSingleton:
    """test_shared_state_singleton: Two calls to get_shared_state() return the same instance."""

    def test_singleton_same_instance(self, workspace):
        """Two calls to get_shared_state() return the exact same object."""
        state1 = get_shared_state(workspace)
        state2 = get_shared_state(workspace)
        assert state1 is state2

    def test_singleton_via_class(self, workspace):
        """Two direct instantiations return the same singleton."""
        state1 = SharedAgentState(workspace)
        state2 = SharedAgentState(workspace)
        assert state1 is state2

    def test_singleton_has_memory_and_knowledge(self, workspace):
        """Singleton exposes .memory and .knowledge attributes."""
        state = get_shared_state(workspace)
        assert hasattr(state, "memory")
        assert hasattr(state, "knowledge")
        assert state.memory is not None
        assert state.knowledge is not None


class TestSharedStateThreadSafety:
    """test_shared_state_thread_safety: Concurrent writes from multiple threads don't corrupt data."""

    def test_concurrent_memory_writes(self, workspace):
        """Multiple threads writing to MemoryDB simultaneously don't corrupt data."""
        state = get_shared_state(workspace)
        errors = []
        num_threads = 10
        writes_per_thread = 50

        def writer(thread_id):
            try:
                for i in range(writes_per_thread):
                    state.memory.store_memory(
                        f"thread_{thread_id}_key_{i}",
                        f"value_{thread_id}_{i}",
                    )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(t,)) for t in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"

        # Verify all writes succeeded
        for thread_id in range(num_threads):
            for i in range(writes_per_thread):
                value = state.memory.get_memory(f"thread_{thread_id}_key_{i}")
                assert value == f"value_{thread_id}_{i}"

    def test_concurrent_knowledge_writes(self, workspace):
        """Multiple threads writing to KnowledgeDB simultaneously don't corrupt data."""
        state = get_shared_state(workspace)
        errors = []
        insight_ids = []
        lock = threading.Lock()
        num_threads = 10

        # Use distinct categories per thread to avoid dedup entirely
        categories = [
            "physics",
            "chemistry",
            "biology",
            "astronomy",
            "geology",
            "music",
            "painting",
            "sculpture",
            "poetry",
            "dance",
        ]

        def writer(thread_id):
            try:
                # Each thread uses a distinct category AND fully unique content
                insight_id = state.knowledge.store_insight(
                    category=categories[thread_id],
                    content=f"Specialized {categories[thread_id]} knowledge #{thread_id * 7919}",
                )
                with lock:
                    insight_ids.append(insight_id)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(t,)) for t in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"
        assert len(insight_ids) == num_threads

    def test_concurrent_singleton_creation(self, workspace):
        """Multiple threads getting singleton don't create multiple instances."""
        instances = []
        lock = threading.Lock()
        num_threads = 20

        def get_instance():
            state = get_shared_state(workspace)
            with lock:
                instances.append(id(state))

        threads = [threading.Thread(target=get_instance) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should get the same instance
        assert (
            len(set(instances)) == 1
        ), f"Got {len(set(instances))} different instances"


class TestSharedStateTwoDBsOnly:
    """test_shared_state_two_dbs_only: Creates exactly 2 DB files: memory.db and knowledge.db."""

    def test_creates_exactly_two_dbs(self, workspace):
        """SharedAgentState creates exactly memory.db and knowledge.db — no extras."""
        get_shared_state(workspace)

        db_files = sorted([f.name for f in workspace.iterdir() if f.suffix == ".db"])
        assert db_files == [
            "knowledge.db",
            "memory.db",
        ], f"Expected exactly [knowledge.db, memory.db], got {db_files}"

    def test_no_skills_tools_agents_dbs(self, workspace):
        """No skills.db, tools.db, or agents.db should exist."""
        get_shared_state(workspace)

        all_files = [f.name for f in workspace.iterdir()]
        assert "skills.db" not in all_files
        assert "tools.db" not in all_files
        assert "agents.db" not in all_files
        assert "logs.db" not in all_files


class TestSharedStateNoGaiaCodeDeps:
    """test_shared_state_no_gaia_code_deps: shared_state.py imports nothing from gaia_code/."""

    def test_no_gaia_code_imports(self):
        """shared_state.py must not import from gaia_code/ or any specific agent module."""
        import gaia.agents.base.shared_state as module

        source_file = inspect.getfile(module)
        with open(source_file, "r") as f:
            source = f.read()

        tree = ast.parse(source)
        forbidden_prefixes = [
            "gaia_code",
            "gaia.agents.chat",
            "gaia.agents.code",
            "gaia.agents.blender",
            "gaia.agents.jira",
        ]

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for prefix in forbidden_prefixes:
                        assert not alias.name.startswith(
                            prefix
                        ), f"shared_state.py imports '{alias.name}' — must be agent-agnostic"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    for prefix in forbidden_prefixes:
                        assert not node.module.startswith(
                            prefix
                        ), f"shared_state.py imports from '{node.module}' — must be agent-agnostic"

    def test_stdlib_only_imports(self):
        """shared_state.py uses only stdlib modules — no external dependencies."""
        import gaia.agents.base.shared_state as module

        source_file = inspect.getfile(module)
        with open(source_file, "r") as f:
            source = f.read()

        tree = ast.parse(source)
        allowed_stdlib = {
            "sqlite3",
            "threading",
            "uuid",
            "json",
            "pathlib",
            "logging",
            "re",
            "datetime",
            "collections",
            "typing",
            "os",
            "time",
            "dataclasses",
            "hashlib",
            "abc",
        }

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level = alias.name.split(".")[0]
                    assert (
                        top_level in allowed_stdlib
                    ), f"shared_state.py imports '{alias.name}' — only stdlib allowed"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_level = node.module.split(".")[0]
                    assert (
                        top_level in allowed_stdlib
                    ), f"shared_state.py imports from '{node.module}' — only stdlib allowed"


class TestSharedStateResetSession:
    """Tests for reset_session() behavior."""

    def test_reset_clears_working_memory(self, workspace):
        """reset_session clears working memory but keeps knowledge."""
        state = get_shared_state(workspace)

        # Store working memory and knowledge
        state.memory.store_memory("temp_fact", "will be cleared")
        state.knowledge.store_insight(
            category="fact",
            content="Persistent knowledge about GAIA framework features",
        )
        state.knowledge.store_preference("theme", "dark")

        state.reset_session()

        # Working memory should be cleared
        assert state.memory.get_memory("temp_fact") is None

        # Knowledge should persist
        results = state.knowledge.recall("GAIA framework")
        assert len(results) >= 1
        assert state.knowledge.get_preference("theme") == "dark"

    def test_reset_preserves_conversation_history(self, workspace):
        """reset_session preserves conversation history."""
        state = get_shared_state(workspace)

        state.memory.store_conversation_turn("s1", "user", "Hello")
        state.memory.store_conversation_turn("s1", "assistant", "Hi there!")

        state.reset_session()

        history = state.memory.get_conversation_history("s1")
        assert len(history) == 2
