# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Declarative per-profile prompt/tool composition for ``ChatAgent`` (#2323).

Replaces the ``if profile == …`` chains that used to live inline in
``agent.py``'s ``_get_system_prompt()`` and ``_register_tools()`` with a small
lookup table. Two axes compose independently and must stay that way:

* the **profile** identity (this table) — "chat" / "doc" / "file" / "data" /
  "web" / "full".
* the per-instance ``ChatAgentConfig.enable_*`` toggles — ``enable_filesystem``,
  ``enable_scratchpad``, ``enable_browser``.

A gated *prompt* section (filesystem/scratchpad/browser) is included when
EITHER the profile inherently carries that capability OR the matching
``enable_*`` flag is set (composed with OR, never replaced — see
``ProfileSpec.capabilities`` and its callers in ``agent.py``). Tool
*registration* for those same capabilities, by contrast, is driven by profile
membership alone — the ``enable_*`` flags never add tools, only prompt text
(this is intentional current-main behavior: e.g. ``chat`` + ``enable_filesystem
=True`` describes filesystem tools in the prompt without registering any).

This module only holds data (which blocks/groups a profile uses); the actual
prose blocks and mixin ``register_*`` methods it names are unchanged and still
live in ``agent.py`` / the tool mixins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Tuple

# Named tool-registration groups. Each maps to the exact ordered sequence of
# mixin registrar method names ``_register_tools()`` used to call inline for
# that combination — preserved verbatim (including the historical duplicate
# ``register_file_tools``/``register_file_search_tools`` calls that "full"
# picks up from both "doc_rag" and "file_fs", since both groups are idempotent
# registrations into the same tool registry).
TOOL_GROUP_REGISTRARS: Dict[str, Tuple[str, ...]] = {
    "doc_rag": (
        "register_rag_tools",
        "register_file_tools",
        "register_file_search_tools",
    ),
    "file_fs": (
        "register_file_tools",
        "register_filesystem_tools",
        "register_file_search_tools",
        "register_file_io_tools",
    ),
    "data_scratch": ("register_scratchpad_tools",),
    # Historically gated on ``if profile == "data"`` nested inside the
    # ("data", "full") condition — "full" doesn't need this group since it
    # already gets file_tools/file_search_tools/file_io_tools from "file_fs".
    "data_only_files": (
        "register_file_tools",
        "register_file_search_tools",
        "register_file_io_tools",
    ),
    "web_browse": ("register_browser_tools",),
    "full_screenshot": ("register_screenshot_tools",),
}


@dataclass(frozen=True)
class ProfileSpec:
    """Declarative prompt/tool-registration profile for ``ChatAgent``.

    Attributes:
        prompt_blocks: ordered keys into the block-string dict assembled by
            ``_get_system_prompt()`` — concatenated in this order (after the
            universal ``base_prompt`` prefix) to build this profile's prompt.
        capabilities: the gated capability names this profile inherently
            carries (``"filesystem"`` / ``"scratchpad"`` / ``"browser"``).
            Each composes with the matching ``ChatAgentConfig.enable_*``
            override at the prompt-block gate (OR, never replaced) but does
            NOT affect tool registration on its own — see ``tool_groups``.
        tool_groups: named groups from ``TOOL_GROUP_REGISTRARS`` this profile
            registers, in order.
        early_return: True only for "chat" — ``_register_tools()`` registers
            just shell + conditional-external tools and returns immediately,
            skipping memory/RAG/file/VLM/SD/web/desktop tool registration
            entirely (the lean-chat-profile invariant).
        generic_file_ops: True when this profile also gets the inline
            ``list_files``/``execute_python_file`` tools and is subject to the
            ``_chat_exclude`` pop of code-writing tool names after
            ``_snapshot_tools()`` (profiles: file, data, full).
        web_tools: True when this profile also gets the inline
            ``open_url``/``fetch_webpage`` tools (profiles: web, full).
    """

    prompt_blocks: Tuple[str, ...]
    capabilities: FrozenSet[str] = field(default_factory=frozenset)
    tool_groups: Tuple[str, ...] = ()
    early_return: bool = False
    generic_file_ops: bool = False
    web_tools: bool = False


PROFILE_SPECS: Dict[str, ProfileSpec] = {
    "chat": ProfileSpec(
        # Minimal: personality only, but explicitly-enabled tool sections
        # (via config.enable_*) still render — see ``capabilities`` docstring.
        prompt_blocks=("filesystem_section", "scratchpad_section", "browser_section"),
        early_return=True,
    ),
    "doc": ProfileSpec(
        prompt_blocks=(
            "indexed_docs_section",
            "tool_rules",
            "discovery_rules",
            "discovery_rules_tail",
            "rag_query_rules",
            "load_tools_menu",
        ),
        tool_groups=("doc_rag",),
    ),
    "file": ProfileSpec(
        prompt_blocks=(
            "tool_rules",
            "discovery_rules",
            "filesystem_section",
            "discovery_rules_tail",
        ),
        capabilities=frozenset({"filesystem"}),
        tool_groups=("file_fs",),
        generic_file_ops=True,
    ),
    "data": ProfileSpec(
        prompt_blocks=("tool_rules", "scratchpad_section", "data_file_rules"),
        capabilities=frozenset({"scratchpad"}),
        tool_groups=("data_scratch", "data_only_files"),
        generic_file_ops=True,
    ),
    "web": ProfileSpec(
        prompt_blocks=("browser_section",),
        capabilities=frozenset({"browser"}),
        tool_groups=("web_browse",),
        web_tools=True,
    ),
    "full": ProfileSpec(
        prompt_blocks=(
            "indexed_docs_section",
            "tool_rules",
            "discovery_rules",
            "filesystem_section",
            "scratchpad_section",
            "browser_section",
            "discovery_rules_tail",
            "rag_query_rules",
            "data_file_rules",
        ),
        capabilities=frozenset({"filesystem", "scratchpad", "browser"}),
        tool_groups=(
            "doc_rag",
            "file_fs",
            "data_scratch",
            "web_browse",
            "full_screenshot",
        ),
        generic_file_ops=True,
        web_tools=True,
    ),
}


def get_profile_spec(profile: str) -> ProfileSpec:
    """Return the :class:`ProfileSpec` for *profile*.

    Falls back to the "full" spec for any value that isn't one of the five
    named profiles — mirroring current-main's implicit final branch (the
    pre-refactor ``_get_system_prompt``/``_register_tools`` treated any
    unrecognized ``prompt_profile`` the same as "full").
    """
    return PROFILE_SPECS.get(profile, PROFILE_SPECS["full"])
