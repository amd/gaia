# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CORE + bundle definitions for the ChatAgent ``doc`` profile tool loader (#1449).

These pin the dynamic-tool-loader configuration for the ``doc`` profile. CORE is
the small always-on set (cap- and eviction-exempt); bundles are cohesion groups
pulled in whole when any member is semantically matched.

``DOC_CORE_TOOLS`` ∪ all ``DOC_BUNDLES`` members must equal the doc-profile
registry **exactly**. The drift guard is the CI test
``tests/unit/test_chat_tool_bundles.py`` — it compares both sets and fails the
build if a registry tool is uncovered *or* a configured name is absent, so a new
doc-profile tool forces a conscious bundling decision instead of silently
shipping unselected. At runtime, ``ToolLoader.validate_registry`` (called once on
first ``select``) additionally fails loudly if any CORE/bundle name is missing
from the registry (the config→registry direction only).
"""

from __future__ import annotations

from gaia.agents.base.tool_loader import ToolBundle

# Always-on set (11 tools): memory v2, file-read + RAG-query entry points, loop
# control, and the Part-2 escape hatch. The design sketch listed a "finish" tool,
# dropped here — turn completion is protocol-level in GAIA, there is no such
# registry tool. ``load_tools`` (#1450) is CORE-only — never in a bundle — so it
# renders in both the text prompt and the native ``tools=`` schema every active
# turn, cap- and eviction-exempt, giving native models a way back to any tool a
# semantic miss didn't surface.
DOC_CORE_TOOLS = frozenset(
    {
        # memory v2 — persistent recall is always relevant
        "remember",
        "recall",
        "update_memory",
        "forget",
        "search_past_conversations",
        # file-read + RAG-query entry points — the doc profile's reason to exist
        "read_file",
        "query_documents",
        "query_specific_file",
        # loop control — autonomous-turn signalling
        "set_loop_state",
        "request_user_input",
        # escape hatch (#1450) — always-on explicit tool loader for native models
        "load_tools",
    }
)

# Cohesion groups. Kept small (≤6 members) so a single bundle pull-in cannot
# blow past the dynamic-slot budget. Members overlapping CORE (e.g. read_file,
# the memory and loop-control tools) are intentional — the union must cover the
# whole registry, and CORE is a subset of that union.
DOC_BUNDLES = [
    ToolBundle(
        name="rag_query",
        members=frozenset(
            {
                "query_documents",
                "query_specific_file",
                "search_indexed_chunks",
                "summarize_document",
                "dump_document",
                "evaluate_retrieval",
            }
        ),
        description="Query and read indexed documents (RAG retrieval).",
    ),
    ToolBundle(
        name="rag_index",
        members=frozenset(
            {
                "index_document",
                "index_directory",
                "list_indexed_documents",
                "rag_status",
                "add_watch_directory",
            }
        ),
        description="Index documents and inspect the RAG index.",
    ),
    ToolBundle(
        name="file_search",
        members=frozenset(
            {
                "search_file",
                "search_directory",
                "search_file_content",
            }
        ),
        description="Find files and search file contents.",
    ),
    ToolBundle(
        name="file_browse",
        members=frozenset(
            {
                "browse_directory",
                "get_file_info",
                "list_recent_files",
            }
        ),
        description="Browse directories and inspect file metadata.",
    ),
    ToolBundle(
        name="file_edit",
        members=frozenset(
            {
                "read_file",
                "write_file",
                "edit_file",
            }
        ),
        description="Read, write, and edit files.",
    ),
    ToolBundle(
        name="data",
        members=frozenset({"analyze_data_file"}),
        description="Analyze structured data files (CSV/Excel).",
    ),
    ToolBundle(
        name="shell",
        members=frozenset({"run_shell_command", "get_system_info"}),
        description="Run shell commands and query the system.",
    ),
    ToolBundle(
        name="clipboard",
        members=frozenset({"read_clipboard", "write_clipboard"}),
        description="Read from and write to the system clipboard.",
    ),
    ToolBundle(
        name="desktop",
        members=frozenset({"notify_desktop", "list_windows", "text_to_speech"}),
        description="Desktop notifications, window listing, and text-to-speech.",
    ),
    ToolBundle(
        name="vision",
        members=frozenset({"analyze_image", "answer_question_about_image"}),
        description="Analyze images and answer questions about them (VLM).",
    ),
    ToolBundle(
        name="memory",
        members=frozenset(
            {
                "remember",
                "recall",
                "update_memory",
                "forget",
                "search_past_conversations",
            }
        ),
        description="Persistent memory: store, recall, update, and forget facts.",
    ),
    ToolBundle(
        name="loop_control",
        members=frozenset({"set_loop_state", "request_user_input"}),
        description="Control the autonomous loop and ask the user questions.",
    ),
]
