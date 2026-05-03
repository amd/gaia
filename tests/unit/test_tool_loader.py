# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for the ToolLoader — bundle-based tool visibility (#688 Phase 1).

Covers:
- Bundle registration and resolution
- Activation policies (always, session, keyword)
- Per-turn tool filtering
- Tool-use recording and warm-window behaviour
- Regression: scratchpad.query_data vs memory.recall disambiguation
"""

import time

import pytest

from gaia.agents.base.tool_loader import (
    ActivationPolicy,
    ToolBundle,
    ToolLoader,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(*tool_names: str) -> dict:
    """Build a minimal fake _TOOL_REGISTRY for testing."""
    return {
        name: {
            "name": name,
            "description": f"Tool {name}",
            "parameters": {},
            "function": lambda: None,
            "atomic": False,
        }
        for name in tool_names
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def loader():
    return ToolLoader()


@pytest.fixture()
def full_registry():
    """Simulates the post-#495/#606 ChatAgent registry (~27 tools)."""
    return _make_registry(
        # core
        "read_file",
        "list_files",
        "run_shell_command",
        # rag
        "query_documents",
        "query_specific_file",
        "index_document",
        "list_indexed_documents",
        "search_indexed_chunks",
        "index_directory",
        # filesystem (from PR #495)
        "browse_directory",
        "tree",
        "file_info",
        "find_files",
        "bookmark",
        # scratchpad (from PR #495)
        "create_table",
        "insert_data",
        "query_data",
        "list_tables",
        "drop_table",
        # browser (from PR #495)
        "fetch_page",
        "search_web",
        "download_file",
        # memory (from PR #606)
        "remember",
        "recall",
        "update_memory",
        "forget",
        "search_past_conversations",
    )


CORE_BUNDLE = ToolBundle(
    name="core",
    tools=frozenset({"read_file", "list_files", "run_shell_command"}),
    policy=ActivationPolicy.ALWAYS,
)

RAG_BUNDLE = ToolBundle(
    name="rag",
    tools=frozenset(
        {
            "query_documents",
            "query_specific_file",
            "index_document",
            "list_indexed_documents",
            "search_indexed_chunks",
            "index_directory",
        }
    ),
    policy=ActivationPolicy.KEYWORD,
    keywords=frozenset({r"document|pdf|index|rag|summarize"}),
)

FILESYSTEM_BUNDLE = ToolBundle(
    name="filesystem",
    tools=frozenset(
        {"browse_directory", "tree", "file_info", "find_files", "bookmark"}
    ),
    policy=ActivationPolicy.KEYWORD,
    keywords=frozenset({r"file|folder|directory|path|browse|tree"}),
)

SCRATCHPAD_BUNDLE = ToolBundle(
    name="scratchpad",
    tools=frozenset(
        {"create_table", "insert_data", "query_data", "list_tables", "drop_table"}
    ),
    policy=ActivationPolicy.SESSION,
    keywords=frozenset({r"table|spreadsheet|csv|data.*entry|scratchpad"}),
)

BROWSER_BUNDLE = ToolBundle(
    name="browser",
    tools=frozenset({"fetch_page", "search_web", "download_file"}),
    policy=ActivationPolicy.KEYWORD,
    keywords=frozenset({r"https?://|url|website|web|search.*online"}),
)

MEMORY_BUNDLE = ToolBundle(
    name="memory",
    tools=frozenset(
        {
            "remember",
            "recall",
            "update_memory",
            "forget",
            "search_past_conversations",
        }
    ),
    policy=ActivationPolicy.SESSION,
    keywords=frozenset(
        {r"remember|recall|forgot|memory|learned|last.*(week|time|session)"}
    ),
)

ALL_BUNDLES = [
    CORE_BUNDLE,
    RAG_BUNDLE,
    FILESYSTEM_BUNDLE,
    SCRATCHPAD_BUNDLE,
    BROWSER_BUNDLE,
    MEMORY_BUNDLE,
]


# ---------------------------------------------------------------------------
# Tests — bundle registration
# ---------------------------------------------------------------------------


class TestBundleRegistration:
    def test_register_single_bundle(self, loader):
        loader.register_bundle(CORE_BUNDLE)
        assert "core" in loader._bundles

    def test_register_multiple_bundles(self, loader):
        loader.register_bundles(ALL_BUNDLES)
        assert len(loader._bundles) == len(ALL_BUNDLES)

    def test_tool_to_bundle_index(self, loader):
        loader.register_bundles(ALL_BUNDLES)
        assert loader.get_bundle_for_tool("query_data") == "scratchpad"
        assert loader.get_bundle_for_tool("recall") == "memory"
        assert loader.get_bundle_for_tool("read_file") == "core"

    def test_overwrite_bundle(self, loader):
        loader.register_bundle(CORE_BUNDLE)
        new_core = ToolBundle(
            name="core",
            tools=frozenset({"read_file"}),
            policy=ActivationPolicy.ALWAYS,
        )
        loader.register_bundle(new_core)
        assert loader._bundles["core"].tools == frozenset({"read_file"})


# ---------------------------------------------------------------------------
# Tests — always-on policy
# ---------------------------------------------------------------------------


class TestAlwaysPolicy:
    def test_core_always_visible(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        result = loader.resolve("Hi there!", full_registry)
        assert "read_file" in result
        assert "list_files" in result
        assert "run_shell_command" in result

    def test_core_visible_regardless_of_message(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        for msg in ["", "hello", "what is 2+2?", "tell me a joke"]:
            result = loader.resolve(msg, full_registry)
            for tool in CORE_BUNDLE.tools:
                assert tool in result, f"Core tool {tool} missing for message: {msg!r}"


# ---------------------------------------------------------------------------
# Tests — keyword activation
# ---------------------------------------------------------------------------


class TestKeywordActivation:
    def test_rag_activated_by_document_keyword(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        result = loader.resolve("Summarize the document", full_registry)
        assert "query_documents" in result
        assert "index_document" in result

    def test_filesystem_activated_by_path(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        result = loader.resolve("Show me files in /home/user", full_registry)
        assert "browse_directory" in result
        assert "find_files" in result

    def test_browser_activated_by_url(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        result = loader.resolve("Fetch https://example.com/data", full_registry)
        assert "fetch_page" in result
        assert "search_web" in result

    def test_unrelated_message_hides_keyword_bundles(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        result = loader.resolve("What is the capital of France?", full_registry)
        # Keyword bundles should NOT be active
        assert "query_documents" not in result
        assert "browse_directory" not in result
        assert "fetch_page" not in result

    def test_rag_stays_warm_after_first_keyword_activation(self, loader, full_registry):
        """Keyword bundle that was activated should stay warm on subsequent turns."""
        loader.register_bundles(ALL_BUNDLES)
        # First turn: activates RAG via keyword
        loader.resolve("Summarize the document", full_registry)
        # Simulate tool use
        loader.record_tool_use("query_documents")
        # Second turn: no keyword, but bundle should be warm
        result = loader.resolve("What else does it say?", full_registry)
        assert "query_documents" in result


# ---------------------------------------------------------------------------
# Tests — session activation
# ---------------------------------------------------------------------------


class TestSessionActivation:
    def test_scratchpad_hidden_until_used(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        result = loader.resolve("What is the weather?", full_registry)
        assert "query_data" not in result
        assert "create_table" not in result

    def test_scratchpad_activates_on_keyword(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        result = loader.resolve("Create a table for my expenses", full_registry)
        assert "create_table" in result
        assert "query_data" in result

    def test_scratchpad_stays_active_after_use(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        # First turn: hidden
        result = loader.resolve("Hello", full_registry)
        assert "query_data" not in result
        # Simulate: user creates a table, tool gets executed
        loader.record_tool_use("create_table")
        # Second turn: scratchpad should be warm
        result = loader.resolve("Now query my expenses", full_registry)
        assert "query_data" in result
        assert "create_table" in result

    def test_memory_hidden_until_used(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        result = loader.resolve("What time is it?", full_registry)
        assert "recall" not in result
        assert "remember" not in result

    def test_memory_activates_on_keyword(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        result = loader.resolve("What did I learn about FTS5 last week?", full_registry)
        assert "recall" in result

    def test_memory_stays_warm_after_remember(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        loader.record_tool_use("remember")
        result = loader.resolve("OK, what else?", full_registry)
        assert "recall" in result


# ---------------------------------------------------------------------------
# Tests — scratchpad vs memory disambiguation (#688 collision)
# ---------------------------------------------------------------------------


class TestDisambiguation:
    """Verify that query_data and recall are not both visible unless justified."""

    def test_spending_query_activates_scratchpad_not_memory(
        self, loader, full_registry
    ):
        """'What did I spend on groceries in March?' → query_data, not recall."""
        loader.register_bundles(ALL_BUNDLES)
        # Pre-activate scratchpad (user created a table earlier)
        loader.record_tool_use("create_table")
        result = loader.resolve(
            "What did I spend on groceries in March?", full_registry
        )
        assert "query_data" in result
        # Memory should NOT be active (no memory keywords matched)
        assert "recall" not in result

    def test_learning_query_activates_memory_not_scratchpad(
        self, loader, full_registry
    ):
        """'What did I learn about FTS5 last week?' → recall, not query_data."""
        loader.register_bundles(ALL_BUNDLES)
        result = loader.resolve("What did I learn about FTS5 last week?", full_registry)
        assert "recall" in result
        # Scratchpad should NOT be active (never used, no keywords)
        assert "query_data" not in result

    def test_both_visible_when_both_justified(self, loader, full_registry):
        """Both visible if scratchpad was used AND memory keywords present."""
        loader.register_bundles(ALL_BUNDLES)
        loader.record_tool_use("create_table")
        result = loader.resolve(
            "Do I remember anything about the data in my table from last week?",
            full_registry,
        )
        # Both should be active: scratchpad (session-warm), memory (keyword)
        assert "query_data" in result
        assert "recall" in result


# ---------------------------------------------------------------------------
# Tests — unbundled tools
# ---------------------------------------------------------------------------


class TestUnbundledTools:
    def test_unbundled_tools_always_visible(self, loader):
        """Tools not assigned to any bundle should appear in every turn."""
        registry = _make_registry("read_file", "custom_tool", "another_tool")
        loader.register_bundle(CORE_BUNDLE)
        result = loader.resolve("Hello", registry)
        # read_file is in core → visible
        assert "read_file" in result
        # custom_tool and another_tool are unbundled → visible
        assert "custom_tool" in result
        assert "another_tool" in result


# ---------------------------------------------------------------------------
# Tests — token savings
# ---------------------------------------------------------------------------


class TestTokenSavings:
    def test_typical_session_reduces_tool_count(self, loader, full_registry):
        """A typical greeting should only expose core + unbundled tools."""
        loader.register_bundles(ALL_BUNDLES)
        result = loader.resolve("Hi there, what's up?", full_registry)
        # Only core tools (3) should be visible from bundled tools
        assert len(result) == len(CORE_BUNDLE.tools)

    def test_max_tools_with_all_activated(self, loader, full_registry):
        """Even with all bundles active, we get the full set."""
        loader.register_bundles(ALL_BUNDLES)
        # Activate everything
        for bundle in ALL_BUNDLES:
            for tool_name in bundle.tools:
                loader.record_tool_use(tool_name)
        result = loader.resolve("Do everything", full_registry)
        assert len(result) == len(full_registry)


# ---------------------------------------------------------------------------
# Tests — session reset
# ---------------------------------------------------------------------------


class TestSessionReset:
    def test_reset_clears_activation(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        loader.record_tool_use("create_table")
        # Verify activated
        assert loader._state["scratchpad"].activated is True
        # Reset
        loader.reset_session()
        assert loader._state["scratchpad"].activated is False
        result = loader.resolve("Hello", full_registry)
        assert "query_data" not in result


# ---------------------------------------------------------------------------
# Tests — warm window
# ---------------------------------------------------------------------------


class TestWarmWindow:
    def test_recent_use_keeps_bundle_warm(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        # Manually inject a recent use into history
        loader._tool_history.append(("fetch_page", time.time()))
        result = loader.resolve("Tell me something", full_registry)
        assert "fetch_page" in result

    def test_old_use_does_not_keep_bundle_warm(self, loader, full_registry):
        loader.register_bundles(ALL_BUNDLES)
        # Inject an old use (26 hours ago)
        old_ts = time.time() - 26 * 3600
        loader._tool_history.append(("fetch_page", old_ts))
        result = loader.resolve("Tell me something", full_registry)
        assert "fetch_page" not in result


# ---------------------------------------------------------------------------
# Tests — mid-conversation pivot
# ---------------------------------------------------------------------------


class TestMidConversationPivot:
    def test_pivot_from_files_to_web(self, loader, full_registry):
        """Session starts with file browsing, pivots to web research."""
        loader.register_bundles(ALL_BUNDLES)

        # Turn 1: file browsing
        result1 = loader.resolve("Show me files in /projects", full_registry)
        assert "browse_directory" in result1
        assert "fetch_page" not in result1
        loader.record_tool_use("browse_directory")

        # Turn 2: pivot to web
        result2 = loader.resolve(
            "Now search the web for React tutorials", full_registry
        )
        assert "fetch_page" in result2
        # Filesystem should still be warm (was used recently)
        assert "browse_directory" in result2

    def test_pivot_from_chat_to_rag(self, loader, full_registry):
        """General chat pivots to document analysis."""
        loader.register_bundles(ALL_BUNDLES)

        # Turn 1: general chat
        result1 = loader.resolve("Hi, how are you?", full_registry)
        assert "query_documents" not in result1

        # Turn 2: mentions a document
        result2 = loader.resolve("Summarize the quarterly report PDF", full_registry)
        assert "query_documents" in result2
        assert "index_document" in result2
