# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CORE + bundle definitions for the ChatAgent tool loader (#1449).

One ``DOC_*`` / ``FULL_*`` pair per profile that supports dynamic tool loading,
wired together by :data:`PROFILE_TOOL_CONFIGS`. CORE is the small always-on set
(cap- and eviction-exempt); bundles are cohesion groups pulled in whole when any
member is semantically matched.

A profile's ``CORE`` ∪ all its bundle members must equal that profile's registry
**exactly**. The drift guard is the CI test
``tests/unit/test_chat_tool_bundles.py`` — it compares both sets and fails the
build if a registry tool is uncovered *or* a configured name is absent, so a new
tool forces a conscious bundling decision instead of silently shipping
unselected. At runtime, ``ToolLoader.validate_registry`` (called once on first
``select``) additionally fails loudly if any CORE/bundle name is missing from the
registry — minus the profile's declared ``optional`` names, which are absent by
construction on some installs (see :data:`FULL_OPTIONAL_TOOLS`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List

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


# -- "full" profile (the flagship GaiaAgent) ---------------------------------
#
# Same contract as the DOC_* pair above, sized for the ``full`` registry: 66
# tools instead of 37, so the un-trimmed native ``tools=`` payload costs ~10.2K
# tiktoken tokens on every LLM call of a 2-5 call ReAct turn.
#
# Always-on set (12 tools). Deliberately a smaller share of the registry than
# the doc CORE, because a general-purpose agent has no single reason to exist:
# memory (recall is relevant to every turn), loop control (protocol-level turn
# signalling), the ``load_tools`` escape hatch, ``load_skill`` for proactive
# skill discovery, and exactly two universal entry points -- ``read_file`` and
# ``query_documents`` -- that answer "what is in this file / what do my
# documents say" without a round trip. Everything else, shell and the web
# included, is a bundle: it arrives when the turn asks for it. Both entry
# points are bundle members too, so a file-shaped or document-shaped turn pulls
# their whole cohort in with them.
FULL_CORE_TOOLS = frozenset(
    {
        # memory v2 -- persistent recall is always relevant
        "remember",
        "recall",
        "update_memory",
        "forget",
        "search_past_conversations",
        # universal entry points
        "read_file",
        "query_documents",
        # loop control -- autonomous-turn signalling
        "set_loop_state",
        "request_user_input",
        # escape hatch (#1450)
        "load_tools",
        # proactive skill discovery (#3235) — the shortlist prompt tells the
        # model to call this even when the skills bundle was not selected.
        "load_skill",
    }
)

# Cohesion groups covering the rest of the full-profile registry. Same
# <=6-member rule as DOC_BUNDLES so one pull-in cannot exhaust the dynamic-slot
# budget (see GaiaAgentConfig.dynamic_tools_max).
FULL_BUNDLES = [
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
        name="file_discovery",
        members=frozenset(
            {
                "find_files",
                "list_files",
                "tree",
                "file_info",
                "bookmark",
            }
        ),
        description="Locate files by name or metadata, list and bookmark paths.",
    ),
    # analyze_data_file rides with the scratchpad rather than standing alone.
    # Measured: on "plot the top 5 products by revenue from sales.csv" it scores
    # below 16 other tools and never gets a slot as a singleton, while the
    # scratchpad tools do — and it is the tool the prompt tells the model to
    # reach for on a CSV. Same workflow anyway: read the file, table it, query it.
    ToolBundle(
        name="data",
        members=frozenset(
            {
                "analyze_data_file",
                "create_table",
                "insert_data",
                "query_data",
                "drop_table",
                "list_tables",
            }
        ),
        description="Analyze CSV/Excel files and query SQL scratchpad tables.",
    ),
    ToolBundle(
        name="web",
        members=frozenset(
            {
                "search_web",
                "search_documentation",
                "fetch_page",
                "fetch_webpage",
                "open_url",
                "download_file",
            }
        ),
        description="Search the web, fetch pages, and download files.",
    ),
    ToolBundle(
        name="code_index",
        members=frozenset(
            {
                "index_codebase",
                "search_code_index",
                "get_index_status",
                "clear_code_index",
            }
        ),
        description="Semantic search over a codebase (index, search, status).",
    ),
    ToolBundle(
        name="skills",
        members=frozenset(
            {
                "list_skills",
                "load_skill",
                "unload_skill",
                "skill_status",
            }
        ),
        description="List, load, and unload the skills installed on this machine.",
    ),
    ToolBundle(
        name="skill_hub",
        members=frozenset(
            {
                "search_skill_hub",
                "install_skill",
                "remove_skill",
            }
        ),
        description="Search the Agent Hub for new skills, install and remove them.",
    ),
    ToolBundle(
        name="shell",
        members=frozenset(
            {
                "run_shell_command",
                "execute_python_file",
                "get_system_info",
            }
        ),
        description="Run shell commands and Python scripts, and query the system.",
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
        name="screenshot",
        members=frozenset({"take_screenshot"}),
        description="Capture a screenshot of the current screen.",
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
    ToolBundle(
        name="email",
        members=frozenset(
            {
                "check_mailbox_access",
                "list_inbox",
                "search_email",
                "read_email",
                "list_mail_folders",
            }
        ),
        description="Read a connected mailbox: list, search, and read messages.",
    ),
]

# Bundle members a healthy ``full`` registry may legitimately lack. Handed to
# ToolLoader as ``optional_tools`` so ``validate_registry`` tolerates exactly
# these and still fails loudly on a typo or a deleted tool. Three structural
# reasons, not "it might be missing, who knows":
#
# 1. Environment-conditional registration -- ``search_documentation`` needs npx
#    on PATH; ChatAgent skips it rather than register a tool whose backend
#    always fails. (``search_web`` is NOT optional here: the browser mixin
#    registers it unconditionally for this profile.)
# 2. Subclass-provided mixins -- the skill-library and code-index tools come
#    from GaiaAgent, so a plain ChatAgent on prompt_profile="full" has none.
# 3. Memory. MemoryMixin registers nothing when the embedder is unreachable, so
#    a degraded-but-running agent has a store and no memory tools. Selection
#    already skips CORE names absent from the registry; without this, validation
#    turned that survivable state into a hard ValueError on the first turn.
#
# The CI drift guard checks the other direction against the flagship registry,
# where every one of these IS present, so a rename still fails the build.
FULL_OPTIONAL_TOOLS = frozenset(
    {
        "search_documentation",
        "remember",
        "recall",
        "update_memory",
        "forget",
        "search_past_conversations",
        "index_codebase",
        "search_code_index",
        "get_index_status",
        "clear_code_index",
        "list_skills",
        "load_skill",
        "unload_skill",
        "skill_status",
        "search_skill_hub",
        "install_skill",
        "remove_skill",
        "check_mailbox_access",
        "list_inbox",
        "search_email",
        "read_email",
        "list_mail_folders",
    }
)


@dataclass(frozen=True)
class ProfileToolConfig:
    """The CORE / bundle / optional triple configuring one profile's loader."""

    core: FrozenSet[str]
    bundles: List[ToolBundle]
    optional: FrozenSet[str] = frozenset()


#: Profiles that support dynamic tool loading. A profile absent from this map
#: gets no loader at all (``_maybe_build_tool_loader`` returns ``None``) and
#: stays on the full-registry legacy path.
PROFILE_TOOL_CONFIGS: Dict[str, ProfileToolConfig] = {
    "doc": ProfileToolConfig(core=DOC_CORE_TOOLS, bundles=DOC_BUNDLES),
    "full": ProfileToolConfig(
        core=FULL_CORE_TOOLS, bundles=FULL_BUNDLES, optional=FULL_OPTIONAL_TOOLS
    ),
}
