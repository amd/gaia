# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""CORE + bundle definitions for the ChatAgent tool loader (#1449).

One ``DOC_*`` / ``FULL_*`` pair per profile that supports dynamic tool loading,
wired together by :data:`PROFILE_TOOL_CONFIGS`. CORE is the list every session starts on;
bundles group the rest, so a new tool forces a conscious decision.

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

# Always-on set (12 tools): memory v2, file-read + RAG-query entry points, loop
# control, and the Part-2 escape hatch. The design sketch listed a "finish" tool,
# dropped here — turn completion is protocol-level in GAIA, there is no such
# registry tool. ``search_tools`` is CORE-only — never in a bundle — so it renders
# in both the text prompt and the native ``tools=`` schema every turn, giving
# models a way to any tool retrieval didn't surface.
DOC_CORE_TOOLS = frozenset(
    {
        # memory v2 — persistent recall is always relevant
        "remember",
        "recall",
        "update_memory",
        "forget",
        "forget_everything",
        "search_past_conversations",
        # file-read + RAG-query entry points — the doc profile's reason to exist
        "read_file",
        "query_documents",
        "query_specific_file",
        # loop control — autonomous-turn signalling
        "set_loop_state",
        "request_user_input",
        # finds any tool or skill the list lacks
        "search_tools",
        "read_tool_output",
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
        members=frozenset(
            {"run_shell_command", "wait_for_condition", "get_system_info"}
        ),
        description="Run shell commands, wait on a condition, and query the system.",
    ),
    ToolBundle(
        name="shell_session",
        members=frozenset({"get_shell_state", "reset_shell_session"}),
        description=(
            "Read the directory earlier shell commands left the session in, "
            "or return it to where the task started."
        ),
    ),
    ToolBundle(
        name="cli_setup",
        members=frozenset(
            {
                "check_cli_setup",
                "install_cli",
                "sign_in_cli",
            }
        ),
        description=(
            "Check whether a command-line tool a skill needs (e.g. the GitHub "
            "CLI 'gh') is installed and signed in, install it, and sign in to it."
        ),
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
                "forget_everything",
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
# Same contract as the DOC_* pair above, sized for the ``full`` registry: 67
# tools instead of 37, so the un-trimmed native ``tools=`` payload costs ~10.2K
# tiktoken tokens on every LLM call of a 2-5 call ReAct turn.
#
# Always-on set (27 tools). Deliberately a smaller share of the registry than
# the doc CORE, because a general-purpose agent has no single reason to exist:
# memory (recall is relevant to every turn), loop control (protocol-level turn
# signalling, plus ``sleep``: a rate limit arrives mid-turn, when no selection
# runs), ``search_tools`` to reach the rest, ``read_tool_output`` to page
# through a result that was cut short, ``load_skill`` for the skill
# catalogue, two universal entry points -- ``read_file`` and
# ``query_documents`` -- that answer "what is in this file / what do my
# documents say" without a round trip, ``run_python`` so a number is computed
# rather than guessed, and the two file-edit tools. Editing is
# always on because semantic selection cannot rank it: on explicit edit
# requests the edit tools lost the dynamic slots to unrelated bundles (#3752).
# The rest of the set is what real sessions call most. Everything else is a
# bundle, reached by the session's one move to the full list.
FULL_CORE_TOOLS = frozenset(
    {
        # memory v2 -- persistent recall is always relevant
        "remember",
        "recall",
        "update_memory",
        "forget",
        "forget_everything",
        "search_past_conversations",
        # universal entry points
        "read_file",
        "query_documents",
        # file editing -- ranked out of the dynamic slots on edit requests (#3752)
        "write_file",
        "edit_file",
        # computation -- without it the model does arithmetic in its head or
        # writes a throwaway script into the user's repo to get a number
        "run_python",
        # loop control -- autonomous-turn signalling
        "set_loop_state",
        "request_user_input",
        "sleep",
        # finds any tool or skill the list lacks
        "search_tools",
        "read_tool_output",
        # the skill catalogue (#3764) tells the model to call this even when
        # the skills bundle was not selected.
        "load_skill",
        # the next most-called tools in the eval baseline: core alone covered 41%
        # of calls, these bring it to 77%, and a turn that needs none of the rest
        # never pays for them
        "query_specific_file",
        "index_document",
        "summarize_document",
        "analyze_data_file",
        "find_files",
        "search_file",
        "browse_directory",
        "search_web",
        "fetch_page",
        "run_shell_command",
    }
)

# Cohesion groups covering the rest of the full-profile registry. Same
# bundle per concern, as in DOC_BUNDLES.
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
    # Separate from "web" on purpose. These carry a live Chromium — seconds
    # and hundreds of MB of RAM — where the "web" bundle is one HTTP GET, and
    # a turn that only needs to read an article must not drag a browser in.
    ToolBundle(
        name="browser_use",
        members=frozenset(
            {
                "browser_open",
                "browser_snapshot",
                "browser_click",
                "browser_type",
                "browser_login",
                "browser_sessions",
            }
        ),
        description=(
            "Drive a real browser: open pages that need JavaScript, click and "
            "type, and sign in to sites."
        ),
    ),
    # Second bundle rather than growing the first past MAX_BUNDLE_MEMBERS: one
    # pull-in must not be able to exhaust the dynamic slots. These two are the
    # navigation half — needed only when a path dead-ends or the page is longer
    # than a snapshot shows.
    ToolBundle(
        name="browser_nav",
        members=frozenset({"browser_back", "browser_find"}),
        description=(
            "Go back from a dead end or a new tab, and search a long page for "
            "text a snapshot did not show."
        ),
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
                "remember_skill_lesson",
            }
        ),
        description=(
            "List, load, and unload the skills installed on this machine, and "
            "change a loaded skill's instructions — when they are wrong, or "
            "when the user wants its output a different way."
        ),
    ),
    ToolBundle(
        name="skill_hub",
        members=frozenset(
            {
                "search_skill_hub",
                "install_skill",
                "capture_skill",
                "remove_skill",
            }
        ),
        description=(
            "Search the Agent Hub for new skills, install, capture "
            "(paste/URL/folder), and remove them."
        ),
    ),
    ToolBundle(
        name="shell",
        members=frozenset(
            {
                "run_shell_command",
                "wait_for_condition",
                "execute_python_file",
                "run_python",
                "get_system_info",
            }
        ),
        description=(
            "Run shell commands and Python scripts, wait on a condition, and "
            "query the system."
        ),
    ),
    ToolBundle(
        name="shell_session",
        members=frozenset({"get_shell_state", "reset_shell_session"}),
        description=(
            "Read the directory earlier shell commands left the session in, "
            "or return it to where the task started."
        ),
    ),
    ToolBundle(
        name="cli_setup",
        members=frozenset(
            {
                "check_cli_setup",
                "install_cli",
                "sign_in_cli",
            }
        ),
        description=(
            "Check whether a command-line tool a skill needs (e.g. the GitHub "
            "CLI 'gh') is installed and signed in, install it, and sign in to it."
        ),
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
        name="image_gen",
        members=frozenset(
            {"generate_image", "list_sd_models", "get_generation_history"}
        ),
        description="Generate images from a text prompt (Lemonade).",
    ),
    ToolBundle(
        name="audio_generation",
        members=frozenset(
            {"generate_music", "generate_sound_effect", "list_audio_generation_models"}
        ),
        description="Generate local music and sound effects using Lemonade.",
    ),
    ToolBundle(
        name="memory",
        members=frozenset(
            {
                "remember",
                "recall",
                "update_memory",
                "forget",
                "forget_everything",
                "search_past_conversations",
            }
        ),
        description="Persistent memory: store, recall, update, and forget facts.",
    ),
    ToolBundle(
        name="loop_control",
        members=frozenset(
            {"set_loop_state", "request_user_input", "sleep", "set_reasoning_effort"}
        ),
        description=(
            "Control the autonomous loop, wait before retrying, set how hard "
            "the model reasons, and ask the user questions."
        ),
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
    # Split from "email" to stay under the per-bundle cap. Its own menu line
    # names the questions only these answer, so the loader can pick it alone.
    ToolBundle(
        name="email_threads",
        members=frozenset(
            {"get_thread", "list_waiting_on_you", "list_connected_mailboxes"}
        ),
        description=(
            "Read a whole email conversation, find mail waiting on the user's "
            "reply, and say which mailbox account is connected."
        ),
    ),
    # Two bundles because of the per-bundle cap. Draft-then-send is one flow, so
    # send_draft rides with the drafts; the rest send with no draft step.
    ToolBundle(
        name="email_drafts",
        members=frozenset(
            {
                "list_drafts",
                "create_draft",
                "update_draft",
                "discard_draft",
                "send_draft",
            }
        ),
        description=(
            "Write an email for later: save, change, list, throw away or send a "
            "draft, including a draft reply or forward."
        ),
    ),
    ToolBundle(
        name="email_send",
        members=frozenset({"send_now", "reply_to_message", "forward_message"}),
        description="Send an email now, reply to a message, or forward one.",
    ),
    # The description carries the file extensions deliberately: a real turn
    # says "summarize this meeting: <path>.mp4" and never says "transcribe",
    # so an extension-shaped query needs something to score against. Without
    # this bundle ``transcribe_media`` was orphaned — in no bundle and not in
    # CORE — so the loader could never surface it and the model fell back to
    # summarize_document on the raw .mp4, which fails.
    # index_document and summarize_document ride along on purpose. They live in
    # the rag bundles too, but a recording turn does not reliably select those,
    # and without them in reach the model pulled chunks with query_documents and
    # asked the user what to dig into instead of summarizing. These tools are
    # one pipeline; they have to arrive together.
    ToolBundle(
        name="media_transcription",
        members=frozenset(
            {
                "transcribe_media",
                "refine_transcript",
                "analyze_transcript",
                "export_transcript",
                "index_document",
                "summarize_document",
            }
        ),
        description=(
            "Transcribe speech from an audio or video recording (mp4, mkv, "
            "mov, m4a, mp3, wav) or a video link such as a YouTube URL into a "
            "timestamped text transcript, then "
            "label the speakers, list everything of a kind with timestamps, "
            "or write subtitles — meetings, calls, interviews, voice memos, "
            "lectures, workshops, tutorials, podcasts. Use for any request to "
            "transcribe, summarize, take notes on, or pull action items or a "
            "structured list out of a recording."
        ),
    ),
]

# Bundle members a healthy ``full`` registry may legitimately lack. Handed to
# ToolLoader as ``optional_tools`` so ``validate_registry`` tolerates exactly
# these and still fails loudly on a typo or a deleted tool. Four structural
# reasons, not "it might be missing, who knows":
#
# 1. Environment-conditional registration -- ``search_documentation`` needs npx
#    on PATH; ChatAgent skips it rather than register a tool whose backend
#    always fails. (``search_web`` is NOT optional here: the browser mixin
#    registers it unconditionally for this profile.)
# 2. Subclass-provided mixins -- the skill-library, skill-learning, and
#    code-index tools come from GaiaAgent, so a plain ChatAgent on
#    prompt_profile="full" has none.
# 3. Memory. MemoryMixin registers nothing when the embedder is unreachable, so
#    a degraded-but-running agent has a store and no memory tools. Selection
#    already skips CORE names absent from the registry; without this, validation
#    turned that survivable state into a hard ValueError on the first turn.
# 4. Config-gated registration -- the SD tools register only under
#    ``enable_sd_tools``, which only GaiaAgentConfig turns on.
#
# The CI drift guard checks the other direction against the flagship registry,
# where every one of these IS present, so a rename still fails the build.
FULL_OPTIONAL_TOOLS = frozenset(
    {
        "search_documentation",
        # Registered only on a model that grades its reasoning (gpt-oss).
        "set_reasoning_effort",
        # Browser-use: registered only when the [browser] extra is installed,
        # so a core install legitimately has none of these.
        "browser_open",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_login",
        "browser_sessions",
        "browser_back",
        "browser_find",
        "remember",
        "recall",
        "update_memory",
        "forget",
        "forget_everything",
        "search_past_conversations",
        "index_codebase",
        "search_code_index",
        "get_index_status",
        "clear_code_index",
        "list_skills",
        "load_skill",
        "unload_skill",
        "skill_status",
        "remember_skill_lesson",
        "search_skill_hub",
        "install_skill",
        "capture_skill",
        "remove_skill",
        "check_mailbox_access",
        "list_inbox",
        "search_email",
        "read_email",
        "list_mail_folders",
        "get_thread",
        "list_connected_mailboxes",
        "list_waiting_on_you",
        "list_drafts",
        "create_draft",
        "update_draft",
        "discard_draft",
        "send_draft",
        "send_now",
        "reply_to_message",
        "forward_message",
        "generate_image",
        "list_sd_models",
        "get_generation_history",
        "generate_music",
        "generate_sound_effect",
        "list_audio_generation_models",
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
