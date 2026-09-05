# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Generic Agent class for building domain-specific agents.
"""

from __future__ import annotations

# Standard library imports
import abc
import ast
import datetime
import inspect
import json
import logging
import math
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Dict,
    FrozenSet,
    List,
    Literal,
    Optional,
    Tuple,
    Union,
)

from gaia.agents.base.console import AgentConsole, SilentConsole
from gaia.agents.base.errors import format_execution_trace
from gaia.agents.base.tools import _TOOL_REGISTRY

# First-party imports
from gaia.chat.sdk import AgentConfig, AgentSDK
from gaia.llm.lemonade_client import (
    DEFAULT_MODEL_NAME,
    budget_for_ctx,
    is_context_overflow_error,
    profile_ctx_size,
    truncation_budget,
)

if TYPE_CHECKING:
    from gaia.agents.base.goal_store import Goal, Proposal
    from gaia.connectors.providers.base import ConnectorRequirement
    from gaia.skills import Skill, SkillManager, SkillSetResolution, SkillSets
    from gaia.skills.binaries import BinaryGrants

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _skill_validation_error(message: str) -> Exception:
    """Build a ``SkillValidationError`` without importing skills at module load.

    ``gaia.skills`` is imported lazily throughout this module so an agent that
    composes no skills never pays for it; the error type has to follow the same
    rule or the lazy import is defeated by the raise site.
    """
    from gaia.skills.errors import SkillValidationError

    return SkillValidationError(message)


#: How much of a skill's description the always-on menu line carries (#2848
#: follow-up). A loaded-but-inactive skill still needs to be discoverable
#: without paying for its full body, and some skills (ported from other
#: ecosystems) ship trigger descriptions over 1,000 characters — capped so the
#: menu itself never becomes what it exists to avoid.
_SKILL_MENU_DESCRIPTION_CHARS = 160


def _skill_menu_description(description: str) -> str:
    """First non-empty line of *description*, capped for the resident menu."""
    text = ""
    for line in (description or "").splitlines():
        if line.strip():
            text = line.strip()
            break
    if len(text) <= _SKILL_MENU_DESCRIPTION_CHARS:
        return text
    return text[:_SKILL_MENU_DESCRIPTION_CHARS].rstrip() + "…"


# Content truncation thresholds
CHUNK_TRUNCATION_THRESHOLD = 5000
CHUNK_TRUNCATION_SIZE = 2500

# Global default for how many reasoning/tool steps an agent may take before it
# stops and reports progress. This is the single knob for the whole fleet:
# change DEFAULT_MAX_STEPS here, or set GAIA_AGENT_MAX_STEPS=<n> at runtime to
# override every agent at once. Agents that genuinely need more (e.g. CodeAgent
# for multi-file generation) override it explicitly in their own config.
DEFAULT_MAX_STEPS = 50


def default_max_steps() -> int:
    """Resolve the global default agent step limit.

    Reads ``GAIA_AGENT_MAX_STEPS`` at call time (not import) so the env var can
    be set after this module is imported and still take effect. Returns
    ``DEFAULT_MAX_STEPS`` when the var is unset; raises on a present-but-invalid
    value so a typo surfaces immediately instead of silently capping agents.
    """
    raw = os.environ.get("GAIA_AGENT_MAX_STEPS")
    if raw is None or raw == "":
        return DEFAULT_MAX_STEPS
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError(
            f"GAIA_AGENT_MAX_STEPS must be a positive integer, got {raw!r}. "
            f"Unset it to use the default ({DEFAULT_MAX_STEPS})."
        ) from e
    if value <= 0:
        raise ValueError(
            f"GAIA_AGENT_MAX_STEPS must be a positive integer, got {value}. "
            f"Unset it to use the default ({DEFAULT_MAX_STEPS})."
        )
    return value


# Default per-tool execution limit (seconds). Bounds a single ``tool(**args)``
# call so a hung tool (e.g. a stuck connector/network call) surfaces an
# actionable error in a sensible window instead of blocking the agent loop —
# and the producer thread — indefinitely. Well under the UI's 600s consumer cap.
# Tools that legitimately run longer (e.g. ``generate_image``, which may
# download a model) opt out via ``@tool(timeout=...)``.
DEFAULT_TOOL_TIMEOUT = 180.0


def tool_execution_timeout() -> float:
    """Resolve the global default per-tool execution timeout in seconds.

    Reads ``GAIA_AGENT_TOOL_TIMEOUT`` at call time (not import) so the env var
    can be set after this module is imported and still take effect. Returns
    ``DEFAULT_TOOL_TIMEOUT`` when the var is unset; raises on a present-but-
    invalid value so a typo surfaces immediately instead of silently removing
    the guard.
    """
    raw = os.environ.get("GAIA_AGENT_TOOL_TIMEOUT")
    if raw is None or raw == "":
        return DEFAULT_TOOL_TIMEOUT
    try:
        value = float(raw)
    except ValueError as e:
        raise ValueError(
            f"GAIA_AGENT_TOOL_TIMEOUT must be a positive number of seconds, "
            f"got {raw!r}. Unset it to use the default ({DEFAULT_TOOL_TIMEOUT})."
        ) from e
    if value <= 0:
        raise ValueError(
            f"GAIA_AGENT_TOOL_TIMEOUT must be a positive number of seconds, "
            f"got {value}. Unset it to use the default ({DEFAULT_TOOL_TIMEOUT})."
        )
    return value


class ToolExecutionTimeout(Exception):
    """Raised when a tool body exceeds its bounded execution window.

    Caught inside ``Agent._execute_tool`` and converted into a fail-loud,
    actionable error result; it is not meant to propagate to callers.
    """

    def __init__(self, tool_name: str, timeout: float):
        self.tool_name = tool_name
        self.timeout = timeout
        super().__init__(
            f"Tool '{tool_name}' exceeded its {timeout:g}s execution limit"
        )


# Generic dangerous tools that require explicit user confirmation before
# execution, regardless of which agent runs them (shell / file mutation).
# Agent-specific gated tools (e.g. email send/RSVP) are declared on the owning
# agent class via ``Agent.CONFIRMATION_REQUIRED_TOOLS`` and merged with this
# base set at runtime — see ``Agent.confirmation_required_tools`` (#1440).
#
# Adding a tool name here (or to a subclass's ``CONFIRMATION_REQUIRED_TOOLS``)
# causes _execute_tool() to call console.confirm_tool_execution() and block
# until the user responds.
#
# This set only covers tools whose names GAIA controls. Tools registered at
# runtime under a third-party name (MCP) carry a ``requires_confirmation`` flag
# on their registry entry instead — see ``Agent._tool_requires_confirmation``.
TOOLS_REQUIRING_CONFIRMATION = {
    "run_shell_command",
    "run_cli_command",
    # Runs a .py file in a subprocess — arbitrary code execution, and unlike
    # run_shell_command there is no read-only allowlist behind it.
    "execute_python_file",
    "write_file",
    "write_python_file",
    "edit_file",
    "edit_python_file",
    "write_markdown_file",
    "replace_function",
    "update_gaia_md",
}


@dataclass(frozen=True)
class HardwareRequirement:
    """Declarative hardware requirement for Agents.

    Fields:
        min_device: one of 'cpu', 'amd_igpu', 'amd_npu'
        reason: optional human-friendly reason displayed on error
    """

    min_device: Literal["cpu", "amd_igpu", "amd_npu", "amd_dgpu"]
    reason: str = ""


# Prefixes for tools that represent SD (Stable Diffusion) capability.
# Used to detect whether the agent has attempted image-generation tools.
_SD_CAPABILITY_TOOLS: Tuple[str, ...] = ("generate_image",)


# Final answer when a turn still overflows the model's context window after
# the one-shot shrink-and-retry (#2763) -- shared across every agent, so it
# names the constraint generically (no tool- or domain-specific vocabulary)
# rather than assuming a search/date-range shape. The prior copy ("re-ask in
# a fresh chat with just the essentials") never said WHAT was too big or HOW
# to shrink it, so every occurrence read identically regardless of cause --
# this repo's fail-loud rule requires naming the constraint and a next step.
_CONTEXT_STILL_OVERFLOWING_MESSAGE = (
    "This request needs more than fits in the model's context window, even "
    "after trimming older results. Try narrowing it — fewer results, a "
    "shorter date range, or a more specific query — or start a fresh "
    "conversation and ask again."
)


# Tools that mutate external state (mark read, archive, star, …). A small
# model that loses track of sequential state may re-issue an identical
# mutation (same tool + same id). Unlike query dedup we key on the *args*,
# not the result — re-issuing mark_read on the same id returns a *different*
# result ("already read"), so a result hash would miss the repeat (#1317).
_MUTATION_TOOLS: Tuple[str, ...] = (
    "mark_read",
    "mark_read_batch",
    "mark_unread",
    "mark_unread_batch",
    "archive_message",
    "archive_message_batch",
    "add_star",
    "add_star_batch",
    "remove_star",
    "remove_star_batch",
    "trash_message",
)


def _repair_invalid_json_escapes(s: str) -> str:
    """Repair invalid JSON backslash escapes using pair-consumption.

    This implementation repeatedly replaces a backslash followed by a
    non-JSON-escape character with a doubled backslash and the character,
    using a regex-based pair-consumption approach. The operation is
    idempotent: applying it multiple times will not further change a
    previously-repaired string.
    """
    # Valid JSON escape characters after a backslash
    valid = '"\\/bfnrtu'

    # Single-pass consumption: replace a backslash followed by a single
    # character; if that character is not a valid JSON escape (and is not
    # itself a backslash), double the backslash. This keeps the operation
    # idempotent on already-repaired inputs and avoids non-terminating
    # repeated-replacement loops.
    def _fix(m: re.Match) -> str:
        ch = m.group(1)
        # Preserve already-double-backslashes and valid JSON escapes
        if ch == "\\" or ch in valid:
            return "\\" + ch
        # Otherwise double the backslash so the JSON parser accepts it
        return "\\\\" + ch

    return re.sub(r"\\(.)", _fix, s)


def _find_matching_close_paren(text: str, open_pos: int) -> Optional[int]:
    """Return the index of the ``)`` that closes ``text[open_pos]`` (a ``(``).

    Depth- and quote-aware (mirrors the brace matcher used for embedded JSON
    tool calls) so a ``)`` inside a quoted argument value doesn't close the
    call early. Returns ``None`` if the call is never terminated.
    """
    depth = 0
    in_str = False
    quote_char = ""
    escape = False
    for j in range(open_pos, len(text)):
        ch = text[j]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if in_str:
            if ch == quote_char:
                in_str = False
            continue
        if ch in ("'", '"'):
            in_str = True
            quote_char = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return j
    return None


def _safe_number(value: Any) -> int:
    """Coerce a usage-stat value to a non-negative int; anything else
    (string, None, nested structure, bool) is untrusted input and yields 0
    rather than raising — a malformed stat must never break the run that
    carries it."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return 0


def _sum_conversation_tokens(
    conversation: List[Dict[str, Any]],
    tool_usage_entries: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[int, int]:
    """Sum input/output tokens from per-step 'stats' entries already appended
    to conversation, plus any tool-reported usage folded in separately (see
    ``_extract_tool_usage``). Returns (total_input, total_output)."""
    total_input = 0
    total_output = 0
    for entry in conversation:
        if entry.get("role") == "system" and isinstance(entry.get("content"), dict):
            content = entry["content"]
            if content.get("type") == "stats" and "performance_stats" in content:
                stats = content["performance_stats"]
                total_input += _safe_number(stats.get("input_tokens"))
                total_output += _safe_number(stats.get("output_tokens"))
    for usage in tool_usage_entries or []:
        total_input += _safe_number(
            usage.get("prompt_tokens") or usage.get("input_tokens")
        )
        total_output += _safe_number(
            usage.get("completion_tokens") or usage.get("output_tokens")
        )
    return total_input, total_output


def _query_ttft_seconds(conversation: List[Dict[str, Any]]) -> Optional[float]:
    """Turn's ttft = the FIRST step's own time_to_first_token; a later step's
    value would drop all earlier tool-decision latency. None when step 1 has
    no positive value — never a fabricated 0.0."""
    for entry in conversation:
        if entry.get("role") == "system" and isinstance(entry.get("content"), dict):
            content = entry["content"]
            if content.get("type") == "stats" and "performance_stats" in content:
                if content.get("step") != 1:
                    # Step 1's own poll failed/was skipped — never misattribute
                    # a later step's latency as the turn's ttft.
                    return None
                stats = content["performance_stats"]
                ttft = (
                    stats.get("time_to_first_token")
                    if isinstance(stats, dict)
                    else None
                )
                if (
                    isinstance(ttft, (int, float))
                    and not isinstance(ttft, bool)
                    and math.isfinite(ttft)
                    and ttft > 0
                ):
                    return float(ttft)
                return None
    return None


# Only these field names are ever accepted from a tool's self-reported
# ``usage`` dict — deliberately narrower than "any dict under a `usage` key",
# so a tool with an unrelated `usage` value (rate-limit/quota/disk usage, not
# LLM tokens) is never misread as token accounting (#2899).
_TOOL_USAGE_TOKEN_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "input_tokens",
    "output_tokens",
)


def _extract_tool_usage(tool_result: Any) -> Optional[Dict[str, Any]]:
    """Pull a tool-reported usage dict off a tool's own return payload, if
    present and shaped like real token accounting. Some tools make their own
    internal LLM calls outside the normal per-step chat-completion accounting
    (e.g. a triage tool that classifies many items with its own client calls)
    and report the aggregate on their own return value instead of through the
    per-step stats path. Never raises — a malformed payload (bad JSON, wrong
    shape, non-numeric fields) yields ``None``, the same as "no usage to
    report"."""
    try:
        payload = tool_result
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            return None
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return None
        has_real_token_field = any(
            isinstance(usage.get(f), (int, float))
            and not isinstance(usage.get(f), bool)
            for f in _TOOL_USAGE_TOKEN_FIELDS
        )
        return usage if has_real_token_field else None
    except (ValueError, TypeError):
        return None


# Suffix appended to the last tool-result message when ``single_tool_per_turn``
# agents have completed their one tool call. The model sees this and emits a
# short final reply instead of calling another tool. Greppable for fixtures
# that need to strip it from recorded role history.
_SINGLE_TOOL_DONE_SUFFIX = (
    "\n\n[SYSTEM: Tool call complete. "
    "Write your one-sentence response to the user now. "
    "Do not call any more tools.]"
)


class Agent(abc.ABC):
    """
    Base Agent class that provides core functionality for domain-specific agents.

    The Agent class handles the core conversation loop, tool execution, and LLM
    interaction patterns. It provides:
    - Conversation management with an LLM
    - Tool registration and execution framework
    - JSON response parsing and validation
    - Error handling and recovery
    - State management for multi-step plans
    - Output formatting and file writing
    - Configurable prompt display for debugging

    Key Parameters:
        debug: Enable general debug output and logging
        show_prompts: Display prompts sent to LLM (useful for debugging prompts)
        debug_prompts: Include prompts in conversation history for analysis
        streaming: Enable real-time streaming of LLM responses
        silent_mode: Suppress all console output for JSON-only usage
    """

    # Per-instance tool snapshot.  ``None`` → fall back to global
    # ``_TOOL_REGISTRY`` (backward compat for agents that don't snapshot).
    _instance_tools: Optional[Dict[str, Any]] = None

    # Dynamic tool loader (#1449): the sorted subset of tool names to surface
    # this turn, or ``None`` to render the full registry (legacy, byte-identical).
    # Set by ``_select_tools_for_turn`` at the top of each query; consulted by
    # both render paths and the ``_openai_tools`` property.
    _active_tool_filter: Optional[List[str]] = None

    # Re-entrancy guard for tool timing. A tool body may call another tool
    # (CodeAgent orchestrates that way); only the outermost call is timed.
    _tool_timing_depth: int = 0

    # Seconds spent waiting on a human confirmation, excluded from tool time.
    _confirmation_wait_s: float = 0.0

    # Which mixin contributed each system-prompt fragment.
    _mixin_prompt_origins: "Optional[Dict[str, Any]]" = None

    # Per-turn performance record, live only for the duration of one turn and
    # only when GAIA_TURN_LOG is set. ``None`` is the off state everywhere.
    _turn_recorder: Optional[Any] = None

    # Skills (#888): lazily built manager + the skills loaded into this agent.
    # Instance-level once set, so one agent's skills never leak into a sibling.
    _skill_manager: Optional[Any] = None
    _loaded_skills: Optional[Dict[str, Any]] = None
    # ``shell:execute:<binary>`` grants held by the currently loaded skills.
    _granted_binaries: Optional[Any] = None

    # Lazy skill-body activation (#2848 follow-up): the sorted subset of
    # LOADED skill names whose full body renders this turn, or ``None`` to
    # render every loaded skill's body unconditionally (legacy, byte-identical
    # — the default for every agent that hasn't opted in). Set by
    # ``_select_skills_for_turn`` at the top of each query; consulted by
    # ``get_skills_system_prompt``. Unlike the tool filter this is NOT
    # monotonic: a stale match has to be dropped, or lazy loading reproduces
    # the exact bug it fixes (a skill body riding along on every turn once
    # matched once). Always-on skills (the manifest's plain ``skills:`` list)
    # are exempt and always render in full — see ``_always_on_skill_names``.
    _active_skill_filter: Optional[List[str]] = None

    #: Explicit re-activations (``load_skill`` on an already-loaded skill)
    #: pinned active for this many upcoming turns. Without it the escape
    #: hatch survived less than one exchange: the next turn's fresh selection
    #: wiped it, and a follow-up like "yes, continue" collapsed the recipe
    #: the model was mid-way through executing.
    STICKY_SKILL_TURNS: ClassVar[int] = 3

    #: name -> turns of pinning remaining; decremented each refresh.
    _sticky_skill_turns: Optional[Dict[str, int]] = None

    #: Proactive skill discovery: matches the user's turn against skills that
    #: are INSTALLED BUT NOT LOADED and activates the winner, so a user never
    #: has to know a skill's name. ``None`` (the default) leaves every existing
    #: agent's behavior and composed prompt byte-identical; GaiaAgent builds one.
    #: See :mod:`gaia.agents.base.skill_discovery`.
    _skill_discovery: Optional[Any] = None

    #: This turn's discovery note, rendered by
    #: ``get_skill_discovery_system_prompt``. Cleared and recomputed per turn.
    _skill_discovery_result: Optional[Any] = None

    # Skill sets (#2466): the parsed manifest declarations, the explicit
    # ``--skill-set`` request, and the set that actually resolved.
    _skill_sets: Optional[Any] = None
    _requested_skill_set: Optional[str] = None
    _active_skill_set: Optional[str] = None
    # Names the last ``load_skill_set`` loaded, so switching sets unloads only
    # what a set brought in — never a skill the agent loaded itself.
    _skill_set_loaded: Optional[List[str]] = None

    # Define state constants
    STATE_PLANNING = "PLANNING"
    STATE_EXECUTING_PLAN = "EXECUTING_PLAN"
    STATE_DIRECT_EXECUTION = "DIRECT_EXECUTION"
    STATE_ERROR_RECOVERY = "ERROR_RECOVERY"
    STATE_COMPLETION = "COMPLETION"

    #: What to show while a non-streaming LLM call blocks. A local model can sit
    #: here for a minute with nothing else on the wire, and the old label —
    #: "Thinking" — described the harness rather than the work (#2804).
    _STATE_PROGRESS_LABELS = {
        STATE_PLANNING: "Working out how to answer",
        STATE_EXECUTING_PLAN: "Working through the plan",
        STATE_DIRECT_EXECUTION: "Figuring out what to do",
        STATE_ERROR_RECOVERY: "Recovering from a failed step",
        STATE_COMPLETION: "Putting the answer together",
    }

    # When True, the agent stops after the first tool call per turn and treats
    # the model's next response as the final answer.  Designed for action-only
    # agents (e.g. the OEM MCP) that execute exactly one tool per user request.
    single_tool_per_turn: bool = False

    # T-X2 (issue #915): declarative external-OAuth scope requirement.
    # Subclasses override this to declare which provider+scopes their tool
    # bodies need. The registry surfaces these to AgentUI's consent dialog and
    # the CLI ``gaia connectors grants`` command, and the runtime gates each
    # ``get_access_token`` call on a per-agent grant for these scopes.
    # Empty list = no external connections required (the default for built-ins).
    REQUIRED_CONNECTORS: ClassVar[List[ConnectorRequirement]] = []

    # Registry reads this to include dynamic MCP consumers in the Settings "Active for" panel.
    CONSUMES_MCP_SERVERS: ClassVar[bool] = False

    # Agent-bundled skill directories (issue #888) — the highest-precedence
    # discovery root. A packaged agent points this at its own ``skills/`` folder
    # so the skills it ships always win over a same-named user or Claude Code
    # copy. Empty = the agent bundles no skills.
    SKILL_DIRS: ClassVar[List[str]] = []

    # Path to the ``gaia-agent.yaml`` whose ``skills:`` / ``skill_sets:`` blocks
    # this agent composes (#2466, #2467 scope D). The base ``__init__`` reads it
    # and loads the resolved set at startup. ``None`` auto-detects the manifest
    # beside the agent's own module — which is why a custom harness needs no code
    # change to consume an installed hub skill. Relative paths resolve against
    # the agent's module dir.
    SKILL_MANIFEST: ClassVar[Optional[str]] = None

    # Set False to skip the automatic ``load_declared_skills()`` in ``__init__``.
    # For an agent that must decide its skill set at run time (e.g. from user
    # input) and calls ``load_skill`` itself.
    AUTOLOAD_DECLARED_SKILLS: ClassVar[bool] = True

    # Agent-specific tools that must be gated behind explicit user confirmation
    # (#1440). Subclasses override this to declare their own destructive/external
    # tools (e.g. email send, calendar RSVP). It is UNIONED with the generic
    # ``TOOLS_REQUIRING_CONFIRMATION`` base set at runtime — see
    # ``confirmation_required_tools`` — so an agent never has to re-list the
    # generic shell/file-mutation tools. Empty by default.
    CONFIRMATION_REQUIRED_TOOLS: ClassVar[frozenset] = frozenset()

    # ``get_*_system_prompt`` fragments whose text changes during a session.
    # ``_compose_system_prompt`` emits these LAST so a mid-session change never
    # invalidates the backend's KV-cache prefix for the static text above them.
    # Add a name here only if its fragment genuinely varies within one session —
    # a static fragment listed here just loses its position, harmlessly.
    VOLATILE_PROMPT_FRAGMENTS: ClassVar[frozenset] = frozenset(
        {
            "get_memory_system_prompt",  # changes on any remember()/forget()
            "get_skills_system_prompt",  # per-turn body selection (#2848)
            "get_recalled_skills_system_prompt",  # per-turn procedural recall
        }
    )

    # Declarative per-agent hardware requirement.  Agents that need a
    # minimum tier (e.g., NPU) should set this ClassVar to a
    # `HardwareRequirement` instance. Example:
    #   REQUIRED_HARDWARE: ClassVar[Optional[HardwareRequirement]] = HardwareRequirement(min_device="amd_npu")
    REQUIRED_HARDWARE: ClassVar[Optional["HardwareRequirement"]] = None
    # Response format templates — agents select via response_mode attribute.
    # "planning" (default): JSON-only responses with thought/goal/plan/tool structure.
    # "conversational": plain text for conversation, JSON only for tool calls.
    _PLANNING_FORMAT = """
==== RESPONSE FORMAT ====
You must respond ONLY in valid JSON. No text before { or after }.

**To call a tool:**
{"thought": "reasoning", "goal": "objective", "tool": "tool_name", "tool_args": {"arg1": "value1"}}

**To create a multi-step plan:**
{
  "thought": "reasoning",
  "goal": "objective",
  "plan": [
    {"tool": "tool1", "tool_args": {"arg": "val"}},
    {"tool": "tool2", "tool_args": {"arg": "val"}}
  ],
  "tool": "tool1",
  "tool_args": {"arg": "val"}
}

**Dynamic placeholders in plans:**
- $PREV.field - reference a field from the previous step's result
- $STEP_N.field - reference a field from step N's result (0-indexed)

**To provide a final answer:**
{"thought": "reasoning", "goal": "achieved", "answer": "response to user"}

**RULES:**
1. ALWAYS use tools for real data - NEVER hallucinate
2. Plan steps MUST be objects like {"tool": "x", "tool_args": {}}, NOT strings
3. After tool results, provide an "answer" summarizing them
4. Use the full tool name exactly as registered. A name with only the server prefix (e.g. ending in `_mcp`) is incomplete.
"""

    _CONVERSATIONAL_FORMAT = """
==== RESPONSE FORMAT ====
Respond in plain text for normal conversation.

When you need to call a tool, output ONLY a JSON object on a single line:
{"tool": "tool_name", "tool_args": {"arg1": "value1"}}

Use the full tool name exactly as registered. A name with only the
server prefix (e.g. ending in `_mcp`) is incomplete.

When responding conversationally (no tool call needed), just write plain text.
Do NOT wrap conversational replies in JSON.
"""

    _FORMAT_TEMPLATES = {
        "planning": _PLANNING_FORMAT,
        "conversational": _CONVERSATIONAL_FORMAT,
    }

    def __init__(
        self,
        use_claude: bool = False,
        use_chatgpt: bool = False,
        claude_model: str = "claude-sonnet-5",
        base_url: Optional[str] = None,
        model_id: str = None,
        max_steps: Optional[int] = None,
        debug_prompts: bool = False,
        show_prompts: bool = False,
        output_dir: str = None,
        streaming: bool = False,
        show_stats: bool = False,
        silent_mode: bool = False,
        debug: bool = False,
        output_handler=None,
        max_plan_iterations: int = 3,
        max_consecutive_repeats: int = 4,
        min_context_size: int = 32768,
        skip_lemonade: bool = False,
        device: Optional[str] = None,
        skill_set: Optional[str] = None,
    ):
        """
        Initialize the Agent with LLM client.

        Args:
            use_claude: If True, uses Claude API (default: False)
            use_chatgpt: If True, uses ChatGPT/OpenAI API (default: False)
            claude_model: Claude model to use when use_claude=True (default: "claude-sonnet-5")
            base_url: Base URL for local LLM server (default: reads from LEMONADE_BASE_URL env var, falls back to http://localhost:13305/api/v1)
            model_id: The ID of the model to use with LLM server (default for local)
            max_steps: Maximum number of steps the agent can take before terminating.
                When None, falls back to the global default_max_steps() (env
                GAIA_AGENT_MAX_STEPS, else DEFAULT_MAX_STEPS).
            debug_prompts: If True, includes prompts in the conversation history
            show_prompts: If True, displays prompts sent to LLM in console (default: False)
            output_dir: Directory for storing JSON output files (default: current directory)
            streaming: If True, enables real-time streaming of LLM responses (default: False)
            show_stats: If True, displays LLM performance stats after each response (default: False)
            silent_mode: If True, suppresses all console output for JSON-only usage (default: False)
            debug: If True, enables debug output for troubleshooting (default: False)
            output_handler: Custom OutputHandler for displaying agent output (default: None, creates console based on silent_mode)
            max_plan_iterations: Maximum number of plan-execute-replan cycles (default: 3, 0 = unlimited)
            max_consecutive_repeats: Maximum consecutive identical tool calls before stopping (default: 4)
            min_context_size: Minimum context size required for this agent (default: 32768).
            skip_lemonade: If True, skip Lemonade server initialization (default: False).
                          Use this when connecting to a different OpenAI-compatible backend.
            skill_set: Explicit skill set to activate (the generic
                          ``--skill-set`` override, #2466). Highest precedence —
                          beats the agent's ``select_skill_set()`` hook and the
                          manifest's ``default_skill_set``. An undeclared name
                          fails loudly, naming the valid sets. Only meaningful
                          when ``SKILL_MANIFEST`` declares ``skill_sets:``.
            device: Runtime device selector ('cpu', 'gpu', 'npu') chosen by the
                          user (Agent UI dropdown / CLI --device). Validated against
                          detected hardware at startup via LemonadeManager.ensure_ready;
                          an unavailable device fails loudly (default: None = no check).

        Note: Uses local LLM server by default unless use_claude or use_chatgpt is True.
        """
        self.device = device
        # Stored before _register_tools so an agent's selector hook and the
        # post-registration skill-set load both see the explicit request.
        self._requested_skill_set = skill_set
        self.error_history = []  # Store error history for learning
        # Safe default so _execute_tool -> _fold_tool_usage never AttributeErrors
        # if called outside the normal process_query loop (e.g. directly in a
        # test); _process_query_impl resets this per-turn (#2899).
        self._tool_reported_usage: List[Dict[str, Any]] = []
        self.conversation_history = (
            []
        )  # Store conversation history for session persistence
        self.max_steps = max_steps if max_steps is not None else default_max_steps()
        self.debug_prompts = debug_prompts
        self.show_prompts = show_prompts  # Separate flag for displaying prompts
        self.output_dir = output_dir if output_dir else os.getcwd()
        self.streaming = streaming
        self.show_stats = show_stats
        self.silent_mode = silent_mode
        self.debug = debug
        self.last_result = None  # Store the most recent result
        self.max_plan_iterations = max_plan_iterations
        self.max_consecutive_repeats = max_consecutive_repeats
        self._current_query: Optional[str] = (
            None  # Store current query for error context
        )
        # Optional cooperative cancel signal. When set (e.g. by the Agent UI's
        # stream-timeout/disconnect cleanup), the process_query loop bails at the
        # next step boundary so the producer thread is torn down, not leaked.
        self._cancel_event: Optional[threading.Event] = None

        # Read base_url from environment if not provided
        if base_url is None:
            base_url = os.getenv("LEMONADE_BASE_URL", "http://localhost:13305/api/v1")

        # Lazy Lemonade initialization for local LLM users
        # This ensures Lemonade server is running before we try to use it
        if not (use_claude or use_chatgpt or skip_lemonade):
            from gaia.llm.lemonade_manager import LemonadeManager

            # Resolve declarative per-agent hardware requirement (if any)
            req = getattr(self.__class__, "REQUIRED_HARDWARE", None)
            required_min_device = req.min_device if req is not None else None

            LemonadeManager.ensure_ready(
                min_context_size=min_context_size,
                quiet=silent_mode,
                base_url=base_url,
                required_min_device=required_min_device,
                device=device,
            )

        # Initialize state management
        self.execution_state = self.STATE_PLANNING
        self.current_plan = None
        self.current_step = 0
        self.total_plan_steps = 0
        self.plan_iterations = 0  # Track number of plan cycles

        # Initialize the console/output handler for display
        # If output_handler is provided, use it; otherwise create based on silent_mode
        if output_handler is not None:
            self.console = output_handler
        else:
            self.console = self._create_console()

        # Initialize LLM client for local model
        # Note: System prompt will be composed after _register_tools()
        # This allows mixins to be initialized first (in subclass __init__)

        # Store response format template BEFORE _register_tools() so that when
        # _register_tools calls load_mcp_servers_from_config → rebuild_system_prompt,
        # the template is already available and gets included in the cached prompt.
        # Subclasses can set self.response_mode before calling super().__init__()
        # to select "conversational" mode (plain text + JSON tool calls).
        if not hasattr(self, "response_mode"):
            self.response_mode = "planning"
        self._response_format_template = self._FORMAT_TEMPLATES.get(
            self.response_mode, self._PLANNING_FORMAT
        )

        # Store model_id BEFORE _register_tools() so that the system-prompt
        # cache populated during MCP tool registration sees the correct
        # ``is_tool_calling_model(self.model_id)`` result. Without this the
        # check at ``_compose_system_prompt`` runs with model_id=None, returns
        # False, and the JSON envelope template gets baked into the prompt
        # for tool-calling models — exactly what the suppression was meant
        # to prevent.
        self.model_id = model_id
        # Claude always speaks native tool_calls, whatever model_id says — the
        # two tool-capability gates below read this alongside model_id.
        self._use_claude = use_claude

        # Initialised here (not lazy via getattr) so subclass tests that drive
        # the parsing helpers outside the standard query lifecycle don't see
        # the silent False fallback.
        self._single_tool_done: bool = False

        # Register tools for this agent (may call rebuild_system_prompt via MCP loading;
        # _response_format_template must be set above before this call).
        self._register_tools()

        # Declarative skills (#2466, #2467 scope D): compose whatever this
        # agent's gaia-agent.yaml declares. After _register_tools so a skill's
        # tools land on top of the agent's own registry (and survive a subclass's
        # ``_snapshot_tools()``), and before the system prompt is first composed
        # so the skill bodies are in it. A no-op for an agent with no manifest,
        # which is every agent today.
        if self.AUTOLOAD_DECLARED_SKILLS:
            self.load_declared_skills()

        # Note: system_prompt is now a lazy @property that composes on first access.
        # Tool descriptions and response format are added in _compose_system_prompt().

        # Initialize AgentSDK with proper configuration
        # Note: We don't set system_prompt in config, we pass it per request
        # Note: Context size is configured when starting Lemonade server, not here
        # Every agent shares DEFAULT_MODEL_NAME so switching agents never evicts
        # and cold-reloads the resident model.
        chat_config = AgentConfig(
            model=model_id or DEFAULT_MODEL_NAME,
            use_claude=use_claude,
            use_chatgpt=use_chatgpt,
            claude_model=claude_model,
            base_url=base_url,
            show_stats=True,  # Always collect stats for token tracking
            max_history_length=20,  # Keep more history for agent conversations
            # Output token cap. With our 32K ctx_size and a ~7.7K-token system
            # prompt + history, leaving 8K for output gives plenty of headroom
            # for both prose answers and long tool-call arg blobs (the eval
            # surfaced 4K cutting off mid-tool-call on Qwen 4B). Going much
            # higher would steal from the input-history budget.
            max_tokens=8192,
        )
        self.chat = AgentSDK(chat_config)
        # ``self.model_id`` was set earlier (before ``_register_tools``) so the
        # system-prompt cache built during MCP registration sees the correct
        # tool-calling-capability gate.

        # Print system prompt if show_prompts is enabled
        # Debug: Check the actual value of show_prompts
        if self.debug:
            logger.debug(
                f"show_prompts={self.show_prompts}, debug={self.debug}, will show prompt: {self.show_prompts}"
            )

        if self.show_prompts:
            self.console.print_prompt(self.system_prompt, "Initial System Prompt")

    def _get_mixin_prompts(self) -> list[str]:
        """
        Auto-collect system prompt fragments from inherited mixins.

        Discovers all methods matching the pattern get_*_system_prompt() on
        the instance and calls each one. This means any mixin that defines
        a method like get_foo_system_prompt() will automatically have its
        prompt fragment included — no manual registration needed.

        Override this method to modify, reorder, or filter mixin prompts.
        Always call super()._get_mixin_prompts() to preserve auto-discovery.

        Returns:
            List of prompt fragments from mixins (empty list if no mixins provide prompts)

        Example:
            def _get_mixin_prompts(self) -> list[str]:
                prompts = super()._get_mixin_prompts()
                # Filter out SD prompt if not needed
                return [p for p in prompts if "Stable Diffusion" not in p]
        """
        prompts = []
        # Records fragment text -> producing method name for this pass, so
        # ``_compose_system_prompt`` can tell volatile fragments from static
        # ones WITHOUT re-running them (memory's fragment hits the DB). A
        # subclass override that filters the returned list still works: the
        # lookup is by text, applied after the filter.
        origins: Dict[str, str] = {}

        # Auto-discover all get_*_system_prompt() methods on this instance.
        # This eliminates the need to hardcode each mixin's prompt method.
        for attr_name in dir(self):
            if (
                attr_name.startswith("get_")
                and attr_name.endswith("_system_prompt")
                and attr_name != "_get_system_prompt"
                and callable(getattr(self, attr_name, None))
            ):
                try:
                    fragment = getattr(self, attr_name)()
                    if fragment:
                        prompts.append(fragment)
                        origins[fragment] = attr_name
                except Exception as e:
                    # A raising fragment is dropped from the composed prompt; surface it
                    # so a silently degraded system prompt is diagnosable.
                    logger.warning(
                        "system-prompt fragment %s() raised, skipping it: %s",
                        attr_name,
                        e,
                    )

        self._mixin_prompt_origins = origins
        return prompts

    def _compose_system_prompt(self) -> str:
        """
        Compose final system prompt from mixin fragments + agent custom + tools + format.

        Override this method for complete control over prompt composition order.

        Returns:
            Composed system prompt string

        Example:
            def _compose_system_prompt(self) -> str:
                # Custom composition order
                parts = [
                    "Base instructions first",
                    *self._get_mixin_prompts(),
                    self._get_system_prompt(),
                ]
                return "\n\n".join(p for p in parts if p)
        """
        fragments = self._get_mixin_prompts()
        origins = getattr(self, "_mixin_prompt_origins", {})
        volatile_mixins = [
            f for f in fragments if origins.get(f) in self.VOLATILE_PROMPT_FRAGMENTS
        ]
        static_mixins = [f for f in fragments if f not in volatile_mixins]

        parts = list(static_mixins)

        # Add agent-specific prompt
        custom = self._get_system_prompt()
        if custom:
            parts.append(custom)

        # Native tool_calls models receive the full JSON schemas via ``tools=``
        # (``_openai_tools``). Rendering the one-line text list as well restates
        # every name, signature and summary the schema already carries — 1,678
        # duplicate tokens on the flagship's 66-tool registry, on every call.
        # Same reasoning that already skips ``_response_format_template`` below.
        tool_filter = self._active_tool_filter
        tools_block = None
        if not self._uses_native_tool_calls() and hasattr(
            self, "_format_tools_for_prompt"
        ):
            tools_description = self._format_tools_for_prompt(filter_to=tool_filter)
            if tools_description:
                tools_block = f"==== AVAILABLE TOOLS ====\n{tools_description}"

        # With no filter the tool block never changes, so it belongs with the
        # static text. Under a filter it is volatile (it grows as new tools are
        # selected) and moves below, after the stable response-format template.
        if tool_filter is None and tools_block is not None:
            parts.append(tools_block)

        # Add embedded-JSON response format only for models that don't support
        # native tool_calls. For tool_calling models we pass tools=[] instead,
        # and the model uses OpenAI function-calling format natively.
        if hasattr(self, "_response_format_template") and not (
            self._uses_native_tool_calls()
        ):
            parts.append(self._response_format_template)

        # Volatile last. llama.cpp reuses the KV cache only up to the first
        # differing token, so anything that changes mid-session — memory after a
        # remember(), the per-turn skill-body selection, a filtered tool list —
        # placed ABOVE the static text invalidates all of it. Tail position
        # keeps the static head cached across turns.
        parts.extend(volatile_mixins)

        if tool_filter is not None and tools_block is not None:
            parts.append(tools_block)

        return "\n\n".join(p for p in parts if p)

    # ── per-turn performance recording (dev mode, opt-in) ──────────────────

    def _begin_turn_record(self, user_input: str) -> Optional[Any]:
        """Start a turn record, or return ``None`` when recording is off.

        Off is the default: it costs one env lookup per turn. When on, the
        recorder is handed to the chat SDK, which is the single choke point
        every backend request passes through.
        """
        from gaia.agents.base.turn_metrics import TurnRecorder, turn_log_path

        if turn_log_path() is None:
            return None
        try:
            recorder = TurnRecorder(
                query=user_input,
                agent_name=type(self).__name__,
                model_id=getattr(self, "model_id", None),
                system_prompt=self.system_prompt,
                tool_schemas=self._openai_tools,
                tool_names=(
                    sorted(self._tools_registry)
                    if self._active_tool_filter is None
                    else list(self._active_tool_filter)
                ),
                skills_active=sorted(getattr(self, "loaded_skills", {}) or {}),
                history_messages=len(getattr(self, "conversation_history", []) or []),
            )
        except Exception as e:  # noqa: BLE001 - diagnostics must not fail a turn
            logger.warning("could not start turn record: %s", e)
            return None
        chat = getattr(self, "chat", None)
        if chat is not None:
            chat.turn_recorder = recorder
            chat.turn_step = 0
        return recorder

    def _finish_turn_record(
        self, answer: str, steps_taken: int
    ) -> Optional[Dict[str, Any]]:
        """Seal and write the turn record; always detaches it from the SDK.

        Idempotent — a second call in the same turn returns ``None``, because
        the first cleared ``_turn_recorder``.
        """
        recorder = getattr(self, "_turn_recorder", None)
        chat = getattr(self, "chat", None)
        if chat is not None:
            chat.turn_recorder = None
        self._turn_recorder = None
        if recorder is None:
            return None
        try:
            record = recorder.finish(answer=answer or "", steps=steps_taken)
        except Exception as e:  # noqa: BLE001 - diagnostics must not fail a turn
            logger.warning("could not finish turn record: %s", e)
            return None
        from gaia.agents.base.turn_metrics import format_summary

        logger.info("[turn] %s", format_summary(record))
        return record

    def _publish_turn_metrics(self, record: Optional[Dict[str, Any]]) -> None:
        """Hand the sealed record to the console, if this console wants one.

        Optional hook: a console that does not define it (a third-party one not
        deriving from ``OutputHandler``) simply does not receive metrics.
        """
        if not record:
            return
        hook = getattr(self.console, "print_turn_metrics", None)
        if hook is None:
            return
        try:
            hook(record)
        except Exception as e:  # noqa: BLE001 - diagnostics must not fail a turn
            logger.warning("console rejected turn metrics: %s", e)

    def _uses_native_tool_calls(self) -> bool:
        """True when this agent's backend takes tools via ``tools=``.

        The single source of truth for "the model gets schemas, not prose" —
        read by both the tool-block gate and the response-format gate in
        :meth:`_compose_system_prompt`.
        """
        from gaia.llm.lemonade_client import is_tool_calling_model

        return bool(getattr(self, "_use_claude", False)) or is_tool_calling_model(
            getattr(self, "model_id", None)
        )

    @property
    def system_prompt(self) -> str:
        """
        Lazy-loaded system prompt composed from mixins + agent custom.

        Computed on first access to allow mixins to initialize in subclass __init__.

        To see the prompt for debugging:
            print(agent.system_prompt)
        """
        if not hasattr(self, "_system_prompt_cache"):
            self._system_prompt_cache = self._compose_system_prompt()
        return self._system_prompt_cache

    @system_prompt.setter
    def system_prompt(self, value: str):
        """Allow setting system prompt (used when appending tool descriptions)."""
        self._system_prompt_cache = value

    def _get_system_prompt(self) -> str:
        """
        Return agent-specific system prompt additions.

        Default implementation returns empty string (use only mixin prompts).
        Override this method to add custom instructions.

        When using mixins that provide prompts (e.g., SDToolsMixin):
        - Return "" to use only mixin prompts (default behavior)
        - Return custom instructions to append to mixin prompts
        - Override _compose_system_prompt() for full control over composition

        Returns:
            Agent-specific system prompt (empty string by default)

        Example:
            # Use only mixin prompts (default)
            def _get_system_prompt(self) -> str:
                return ""

            # Add custom instructions
            def _get_system_prompt(self) -> str:
                return "Always save metadata to logs"
        """
        return ""  # Default: use only mixin prompts

    def _create_console(self):
        """
        Create and return a console output handler.
        Returns SilentConsole if in silent_mode, otherwise AgentConsole.
        Subclasses can override this to provide domain-specific console output.
        """
        if self.silent_mode:
            # Check if we should completely silence everything (including final answer)
            # This would be true for JSON-only output or when output_dir is set
            silence_final_answer = getattr(self, "output_dir", None) is not None
            return SilentConsole(silence_final_answer=silence_final_answer)
        return AgentConsole()

    @abc.abstractmethod
    def _register_tools(self):
        """
        Register all domain-specific tools for the agent.
        Subclasses must implement this method.
        """
        raise NotImplementedError("Subclasses must implement _register_tools")

    @property
    def _tools_registry(self) -> Dict[str, Any]:
        """Return this agent's effective tool registry.

        Uses the per-instance snapshot if ``_snapshot_tools()`` was called,
        otherwise falls back to the global ``_TOOL_REGISTRY`` for backward
        compatibility with agents that predate the snapshot mechanism.
        """
        if self._instance_tools is not None:
            return self._instance_tools
        return _TOOL_REGISTRY

    def _snapshot_tools(self) -> None:
        """Freeze the current ``_TOOL_REGISTRY`` state into this instance.

        After this call, tool lookup, prompt formatting, and execution all
        use the snapshot.  Mutations on this instance's ``_instance_tools``
        will not affect other agents or the global dict.
        """
        self._instance_tools = dict(_TOOL_REGISTRY)

    def _format_tools_for_prompt(self, filter_to: Optional[List[str]] = None) -> str:
        """Format the registered tools into a string for the prompt.

        Args:
            filter_to: When ``None`` (default), render every registered tool in
                registry order — byte-identical to the legacy path. When a list,
                render only those names, in the given (pre-sorted) order,
                skipping any not present in the registry.
        """
        tool_descriptions = []

        if filter_to is None:
            items = list(self._tools_registry.items())
        else:
            registry = self._tools_registry
            items = [(n, registry[n]) for n in filter_to if n in registry]

        for name, tool_info in items:
            params_str = ", ".join(
                [
                    f"{param_name}{'' if param_info['required'] else '?'}: {param_info['type']}"
                    for param_name, param_info in tool_info["parameters"].items()
                ]
            )

            description = next(
                (
                    line.strip()
                    for line in tool_info["description"].splitlines()
                    if line.strip()
                ),
                "",
            )
            tool_descriptions.append(f"- {name}({params_str}): {description}")

        return "\n".join(tool_descriptions)

    @property
    def _openai_tools(self):
        """Return OpenAI function-calling schemas when the active model supports native tool_calls."""
        if self._uses_native_tool_calls():
            return (
                self._build_openai_tool_schemas(filter_to=self._active_tool_filter)
                or None
            )
        return None

    def _select_tools_for_turn(  # pylint: disable=unused-argument
        self, user_input: str
    ) -> Optional[List[str]]:
        """Return the sorted tool-name subset to surface this turn, or ``None``.

        Default: ``None`` — render the full registry (legacy behavior). Agents
        with a dynamic tool loader override this to return a selection.
        """
        return None

    def _on_tool_invoked(self, tool_name: str) -> None:
        """Hook called when a tool is about to execute (after registry lookup).

        Default: no-op. Agents with a dynamic tool loader override this to
        record tool-use recency. Execution itself always uses the full registry,
        so this never gates execution.
        """

    def _refresh_active_tool_filter(self, user_input: str) -> None:
        """Update the active tool filter for this turn, recomputing on change.

        Calls ``_select_tools_for_turn`` and, **only when the selection
        changes**, swaps ``_active_tool_filter`` and recomputes the cached
        system prompt. Both filters are sorted lists (or ``None``), so ``!=`` is
        a correct change test; a stable selection leaves the cached prompt — and
        thus the backend's KV-cache prefix — untouched. ``None`` is the legacy
        full-registry path. ``_openai_tools`` is a property, so all native
        ``tools=`` call sites pick up the new filter automatically.
        """
        # The base hook returns None, but ChatAgent overrides it to return
        # Optional[List[str]] — pylint's None-inference is wrong here.
        # pylint: disable-next=assignment-from-none
        new_filter = self._select_tools_for_turn(user_input)
        if new_filter != self._active_tool_filter:
            self._apply_tool_filter(new_filter)

    def _apply_tool_filter(self, new_filter: Optional[List[str]]) -> None:
        """Swap the active tool filter and recompute the cached system prompt.

        The single place the "filter and prompt move together" invariant lives.
        Called from :meth:`_refresh_active_tool_filter` (per user turn) and from
        the ``load_tools`` escape-hatch handler (mid-loop), so a mid-query
        expansion is visible to the very next model step — both render paths
        (``system_prompt`` and ``_openai_tools``) read these live.
        """
        self._active_tool_filter = new_filter
        self._system_prompt_cache = self._compose_system_prompt()

    def _select_skills_for_turn(  # pylint: disable=unused-argument
        self, user_input: str
    ) -> Optional[List[str]]:
        """Return the loaded-skill-name subset whose body renders this turn.

        Default: ``None`` — render every loaded skill's full body every turn
        (legacy behavior, unchanged for every agent that doesn't opt in).
        GaiaAgent overrides this with a per-turn semantic selector so an
        unrelated skill's body doesn't ride along on every subsequent turn
        for the life of the session (#2848).
        """
        return None

    def _discover_skills_for_turn(self, user_input: str) -> None:
        """Match this turn against installed-but-unloaded skills and act on it.

        No-op unless a subclass built a
        :class:`~gaia.agents.base.skill_discovery.SkillDiscovery` — every other
        agent's composed prompt stays byte-identical.

        Runs BEFORE :meth:`_refresh_active_tool_filter` so tools the loaded skill
        registers are visible on the same turn, and BEFORE
        :meth:`_refresh_active_skill_filter` so the skill is in ``loaded_skills``
        when the body filter is computed. Pinned via :meth:`_pin_skill_body` so
        that filter cannot immediately hide the body of the skill it just decided
        the turn was about.
        """
        discovery = self._skill_discovery
        if discovery is None:
            return

        previous = self._skill_discovery_result
        query = self._build_skill_discovery_query(user_input)
        result = discovery.run(
            query, loaded=self.loaded_skills, load_fn=self.load_skill
        )
        self._skill_discovery_result = result
        if result.loaded:
            self._pin_skill_body(result.loaded)

        # Rebuild whenever the note changed, INCLUDING after a successful load.
        # ``load_skill`` rebuilds too, but it runs before the line above, so the
        # prompt it composed still carries the *previous* turn's note — the
        # "SKILL ACTIVATED" line would be missing on exactly the turns that
        # earned it. The later ``_refresh_active_skill_filter`` only recomposes
        # when the body filter changes, so it cannot be relied on to fix this.
        before = previous.prompt_fragment() if previous is not None else ""
        if result.prompt_fragment() != before:
            self.rebuild_system_prompt()

    def _build_skill_discovery_query(self, user_input: str) -> str:
        """The text discovery matches on — previous + current user message.

        Reuses ChatAgent's tool-selection query when the agent has one, so a
        follow-up ("and the one before that?") still carries the prior turn's
        subject instead of matching on four pronouns.
        """
        builder = getattr(self, "_build_tool_selection_query", None)
        if callable(builder):
            return builder(user_input)
        return user_input

    def get_skill_discovery_system_prompt(self) -> str:
        """Sourcing rule + this turn's discovery note.

        Auto-discovered by :meth:`_get_mixin_prompts`. Returns "" for any agent
        without discovery enabled, so no existing prompt changes.
        """
        if self._skill_discovery is None:
            return ""
        from gaia.agents.base.skill_discovery import GROUNDING_RULE

        result = self._skill_discovery_result
        note = result.prompt_fragment() if result is not None else ""
        return f"{GROUNDING_RULE}\n\n{note}" if note else GROUNDING_RULE

    def _pin_skill_body(self, name: str, turns: Optional[int] = None) -> None:
        """Keep *name*'s body rendered for the next few filter refreshes.

        Unlike :meth:`_note_skill_active` this works before any filter exists —
        proactive discovery runs before the first refresh of a session, and
        without the pin the very next selection could hide the body of the skill
        that was just loaded *because* this turn needed it.
        """
        # getattr throughout: test stubs copy these methods onto a plain class
        # without inheriting the class attributes they read.
        sticky = getattr(self, "_sticky_skill_turns", None)
        if sticky is None:
            sticky = {}
            self._sticky_skill_turns = sticky
        default = getattr(self, "STICKY_SKILL_TURNS", 3)
        sticky[name] = default if turns is None else turns

    def _refresh_active_skill_filter(self, user_input: str) -> None:
        """Update the active skill-body filter for this turn, if it changed.

        Mirrors :meth:`_refresh_active_tool_filter`. Recomputed fresh every
        turn rather than accumulated: a skill body is large enough (measured
        15-19KB, #2848) that letting a stale match stick around reproduces the
        exact permanent-inlining bug this exists to fix.
        """
        # pylint: disable-next=assignment-from-none
        new_filter = self._select_skills_for_turn(user_input)
        new_filter = self._union_sticky_skills(new_filter)
        if new_filter != self._active_skill_filter:
            self._apply_skill_filter(new_filter)

    def _union_sticky_skills(
        self, new_filter: Optional[List[str]]
    ) -> Optional[List[str]]:
        """Fold explicitly re-activated skills into this turn's selection.

        Each pinned name is kept active for ``STICKY_SKILL_TURNS`` refreshes
        after its ``load_skill`` call, then expires back to pure semantic
        selection. ``None`` (selection off / fallback) passes through — every
        body renders anyway.
        """
        sticky = getattr(self, "_sticky_skill_turns", None)
        if not sticky:
            return new_filter
        loaded = getattr(self, "_loaded_skills", None) or {}
        for name in list(sticky):
            if name not in loaded:
                del sticky[name]
        pinned = set(sticky)
        # Decrement AFTER use, so a pin of N covers N refreshes, not N-1.
        for name in list(sticky):
            sticky[name] -= 1
            if sticky[name] <= 0:
                del sticky[name]
        if new_filter is None or not pinned:
            return new_filter
        return sorted(set(new_filter) | pinned)

    def _apply_skill_filter(self, new_filter: Optional[List[str]]) -> None:
        """Swap the active skill-body filter and recompute the cached prompt."""
        self._active_skill_filter = new_filter
        self._system_prompt_cache = self._compose_system_prompt()

    def _note_skill_active(self, name: str) -> None:
        """Force *name*'s body active for the rest of this turn, if filtering is on.

        No-op on the legacy path (``_active_skill_filter is None`` — every
        loaded skill's body already renders unconditionally). This is the
        explicit escape hatch: calling ``load_skill`` on an already-loaded
        skill always brings its instructions back, even when the per-turn
        selector missed it — mirrors the ``load_tools`` escape hatch for tools.
        Callers still need to call :meth:`rebuild_system_prompt` themselves.
        """
        current = self._active_skill_filter
        if current is None:
            return
        # Pin the explicit ask for the next few turns too — a follow-up like
        # "yes, continue" scores nothing against the skill's description, and
        # collapsing the body one turn after the user asked for it strands
        # the model mid-recipe.
        self._pin_skill_body(name)
        if name not in current:
            self._active_skill_filter = sorted(set(current) | {name})

    @property
    def _always_on_skill_names(self) -> FrozenSet[str]:
        """Names from the manifest's plain ``skills:`` (always-on) list.

        Exempt from the per-turn skill-body filter for the same reason
        ToolLoader's CORE tier is exempt from its cap: small and deliberately
        resident every turn (e.g. an honesty-floor skill), not a per-topic
        recipe. Empty for an agent with no manifest or no always-on entries.
        """
        return frozenset(ref.name for ref in self.skill_sets.always)

    def rebuild_system_prompt(self) -> None:
        """Rebuild system prompt with current tools from _TOOL_REGISTRY.

        This method regenerates the system prompt by:
        1. Getting the base prompt from _get_system_prompt()
        2. Appending the current tools from _TOOL_REGISTRY
        3. Appending the JSON response format instructions

        Call this after dynamically adding tools (e.g., via MCP servers or
        after indexing documents) to ensure the LLM knows about them.

        Example:
            >>> agent = MyAgent()
            >>> agent.connect_mcp_server("filesystem", "npx @modelcontextprotocol/server-filesystem /tmp")
            >>> # rebuild_system_prompt() is called automatically
        """
        # Recompose the full system prompt via _compose_system_prompt() so that
        # mixin prompts, tool descriptions, and response format are all included.
        self._system_prompt_cache = self._compose_system_prompt()

    # ------------------------------------------------------------------
    # Skills (issue #888) — SKILL.md capabilities loaded at runtime
    # ------------------------------------------------------------------

    @property
    def skill_manager(self) -> "SkillManager":
        """This agent's :class:`~gaia.skills.manager.SkillManager`.

        Built lazily over the v1 discovery roots, with the agent's own
        ``SKILL_DIRS`` — plus the ``skills/`` folder its package ships, see
        :meth:`_bundled_skill_dirs` — as the highest-precedence roots.
        """
        if getattr(self, "_skill_manager", None) is None:
            from gaia.skills import SkillManager

            self._skill_manager = SkillManager(
                agent_skill_dirs=[*self.SKILL_DIRS, *self._bundled_skill_dirs()]
            )
        return self._skill_manager

    #: Folder name an agent package ships its own skills in.
    _BUNDLED_SKILLS_DIRNAME: ClassVar[str] = "skills"

    def _bundled_skill_dirs(self) -> List[str]:
        """The ``skills/`` folders this agent ships, found the way its manifest is.

        Declaration and discovery have to come from the same place. The manifest
        is auto-detected beside the agent's module, so the skills that manifest
        declares are auto-detected beside it too — otherwise a class that
        inherits its package's ``gaia-agent.yaml`` without repeating the
        package's ``SKILL_DIRS`` (a second entry point in the same package, e.g.
        an MCP wrapper) declares skills GAIA then refuses to find.

        Explicit ``SKILL_DIRS`` still take precedence; this only adds roots.
        Skills that genuinely are not on disk stay missing, and still raise.
        """
        candidates: List[Path] = []
        try:
            module_dir = Path(inspect.getfile(type(self))).resolve().parent
        except (OSError, TypeError):
            # A class defined in a REPL/exec has no source file to search from.
            module_dir = None
        if module_dir is not None:
            candidates.append(module_dir / self._BUNDLED_SKILLS_DIRNAME)
        manifest = self._resolve_skill_manifest()
        if manifest is not None:
            candidates.append(manifest.parent / self._BUNDLED_SKILLS_DIRNAME)

        # Compare resolved paths so a folder already named by SKILL_DIRS is not
        # registered a second time under a different spelling — two roots over
        # one directory would report every skill in it as shadowing itself.
        seen = {Path(d).resolve() for d in self.SKILL_DIRS}
        found: List[str] = []
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen or not resolved.is_dir():
                continue
            seen.add(resolved)
            found.append(str(candidate))
        return found

    @property
    def loaded_skills(self) -> Dict[str, "Skill"]:
        """``{name: Skill}`` for every skill loaded into this agent."""
        if getattr(self, "_loaded_skills", None) is None:
            self._loaded_skills = {}
        return self._loaded_skills

    @property
    def granted_binaries(self) -> "BinaryGrants":
        """CLIs this **instance's** loaded skills may run (``shell:execute:gh``).

        Created lazily and held per instance: a module-level allowlist would let
        one agent's skill widen every other agent's shell.
        """
        if getattr(self, "_granted_binaries", None) is None:
            from gaia.skills.binaries import BinaryGrants

            self._granted_binaries = BinaryGrants()
        return self._granted_binaries

    def load_skill(
        self, name: str, *, manager: Optional["SkillManager"] = None
    ) -> "Skill":
        """Load a skill by name and scope it into this agent.

        Resolves ``name`` across the discovery roots (agent-bundled →
        ``~/.gaia/skills`` → read-only ``.claude/skills``), validates the
        manifest, registers any tools the skill provides under the
        ``<skill-name>/<tool>`` namespace, and injects its Markdown body into
        the system prompt.

        Args:
            name: The skill's ``name`` (== its directory name).
            manager: Optional :class:`~gaia.skills.manager.SkillManager` to
                resolve against; defaults to :attr:`skill_manager`.

        Returns:
            The loaded :class:`~gaia.skills.format.Skill`.

        Raises:
            SkillNotFoundError: no discovery root contains that skill.
            SkillValidationError: the manifest is invalid or contradicts
                ``tools.py``. Nothing is registered.
            SkillPermissionError: the skill declares a local-capability
                permission (``filesystem``/``shell``/``database``/``desktop``/
                ``env``), which this phase refuses rather than loading
                unenforced.

        Example:
            class WebAgent(Agent):
                def _register_tools(self):
                    self.load_skill("web-research")
        """
        from gaia.skills import connector_requirements, refuse_unbridged_permissions
        from gaia.skills.binaries import resolve_binary_policies
        from gaia.skills.loader import register_skill_tools, unregister_skill_tools

        resolver = manager if manager is not None else self.skill_manager

        if name in self.loaded_skills:
            logger.debug("Skill '%s' is already loaded for this agent", name)
            # Explicit reactivation escape hatch: an already-loaded skill whose
            # body the per-turn filter hid still comes back on request — and
            # the sticky pin is set even when the body is ALREADY active this
            # turn, or an explicit ask made while selection happened to match
            # would still collapse on the next low-scoring follow-up.
            if self._active_skill_filter is not None:
                filter_changed = name not in self._active_skill_filter
                self._note_skill_active(name)
                if filter_changed:
                    self.rebuild_system_prompt()
            return self.loaded_skills[name]

        skill = resolver.load(name)

        # Permission gate BEFORE any registration: a refused skill must not
        # leave tools or prompt fragments behind.
        permissions = skill.parsed_permissions()
        refuse_unbridged_permissions(permissions, skill_name=skill.name)
        requirements = connector_requirements(permissions, skill_name=skill.name)
        # A skill whose CLI is missing must not load and then improvise.
        policies = resolve_binary_policies(permissions, skill_name=skill.name)

        registered = register_skill_tools(skill)
        try:
            if registered and self._instance_tools is not None:
                self._instance_tools.update(registered)

            if requirements:
                # Shadow the ClassVar per instance — never mutate it, or one
                # agent's skill would leak requirements into every sibling.
                existing = list(self.REQUIRED_CONNECTORS)
                for requirement in requirements:
                    if requirement not in existing:
                        existing.append(requirement)
                self.REQUIRED_CONNECTORS = existing

            for policy in policies:
                self.granted_binaries.grant(policy.binary, skill_name=skill.name)

            self.loaded_skills[name] = skill
            self._note_skill_active(name)
            self.rebuild_system_prompt()
        except Exception:
            unregister_skill_tools(skill.name)
            self.granted_binaries.revoke_skill(skill.name)
            self.loaded_skills.pop(name, None)
            raise

        # tools_required names registry tools the skill CONSUMES. A name that is
        # valid but not active in this agent is scoping, not a defect — log it so
        # a skill that quietly can't run its recipe is diagnosable.
        inactive = [
            t for t in skill.gaia.tools_required if t not in self._tools_registry
        ]
        if inactive:
            logger.info(
                "Skill '%s' expects tool(s) %s, which this agent does not have "
                "registered — its instructions may not be fully executable here.",
                skill.name,
                ", ".join(inactive),
            )

        logger.info(
            "Loaded skill '%s' (tier=%s, root=%s, %d tool(s), %d connector "
            "requirement(s), binaries=%s)",
            skill.name,
            skill.security_tier,
            skill.root,
            len(registered),
            len(requirements),
            ", ".join(p.binary for p in policies) or "none",
        )
        return skill

    def load_declared_skills(
        self,
        manifest_path: Optional[Union[str, Path]] = None,
        *,
        manager: Optional["SkillManager"] = None,
    ) -> Dict[str, "Skill"]:
        """Load every skill this agent's ``gaia-agent.yaml`` declares (#2467).

        Runs automatically at the end of ``__init__``, so an agent composes hub
        skills by *declaring* them rather than by calling :meth:`load_skill` —
        and a custom harness under ``~/.gaia/agents/<id>/`` gets the identical
        path by dropping a ``gaia-agent.yaml`` beside its ``agent.py``. No
        per-agent code change, bundled or not.

        Args:
            manifest_path: Explicit manifest. Defaults to :attr:`SKILL_MANIFEST`,
                then to the manifest found beside this agent's own module.
            manager: Resolve against this manager instead of :attr:`skill_manager`.

        Returns:
            ``{name: Skill}`` for the skills loaded by this call (empty when the
            agent declares none, which is the common case).

        Raises:
            SkillNotFoundError: a **required** declared skill is not installed.
            SkillValidationError: a required skill is installed at a version the
                pin excludes, or the manifest's skill blocks are malformed.
            SkillSetError: an explicitly requested skill set is not declared.
        """
        # Locate the manifest with plain path checks first. This runs in every
        # Agent.__init__, and importing gaia.skills would pull in the connector
        # base module for the overwhelming majority of agents that declare no
        # skills at all — so the import waits until there is a manifest to read.
        path = self._resolve_skill_manifest(manifest_path)
        if path is None and not (self._requested_skill_set or "").strip():
            return {}

        if manifest_path is not None:
            # An explicit manifest replaces whatever SKILL_MANIFEST cached.
            self._skill_sets = self._parse_skill_declarations(path)
        return self.load_skill_set(manager=manager)

    #: Manifest filename searched for beside an agent's module. Duplicated from
    #: ``gaia.skills.consume.AGENT_MANIFEST_FILENAME`` so the hot path in
    #: ``__init__`` can look for it without importing the skills package; the two
    #: are asserted equal by ``tests/unit/test_skills_consume.py``.
    _SKILL_MANIFEST_FILENAME: ClassVar[str] = "gaia-agent.yaml"

    def _resolve_skill_manifest(
        self, explicit: Optional[Union[str, Path]] = None
    ) -> Optional[Path]:
        """Locate the manifest whose ``skills:`` block applies to this agent.

        Searches the agent module's own directory then its parent — the two
        layouts GAIA ships (a hub package keeps the manifest one level above the
        Python package; a custom agent keeps it beside ``agent.py``). It stops
        there deliberately: walking further up would eventually claim an unrelated
        manifest from a site-packages sibling or the repo root.
        """
        if explicit is not None:
            path = Path(explicit)
            if not path.is_file():
                raise _skill_validation_error(
                    f"Agent {type(self).__name__} points at a skills manifest that "
                    f"does not exist: {path}. Fix SKILL_MANIFEST (or the path passed "
                    "to load_declared_skills), or unset it to auto-detect."
                )
            return path

        if self.SKILL_MANIFEST:
            path = Path(self.SKILL_MANIFEST)
            if not path.is_absolute():
                try:
                    module_file = inspect.getfile(type(self))
                except (OSError, TypeError) as exc:
                    raise _skill_validation_error(
                        f"Agent {type(self).__name__} sets relative "
                        f"SKILL_MANIFEST={self.SKILL_MANIFEST!r}, but its source "
                        "file is unavailable. Use an absolute path or define "
                        "the agent in a source-backed module."
                    ) from exc
                path = (Path(module_file).resolve().parent / path).resolve()
            if not path.is_file():
                raise _skill_validation_error(
                    f"Agent {type(self).__name__} sets SKILL_MANIFEST="
                    f"{self.SKILL_MANIFEST!r}, which resolves to {path} — no such "
                    "file. Point it at the agent's gaia-agent.yaml, or unset it to "
                    "auto-detect."
                )
            return path

        try:
            module_file = inspect.getfile(type(self))
        except (OSError, TypeError):
            # A class defined in a REPL/exec has no source file to search from.
            return None
        module_dir = Path(module_file).resolve().parent
        for candidate in (module_dir, module_dir.parent):
            manifest = candidate / self._SKILL_MANIFEST_FILENAME
            if manifest.is_file():
                return manifest
        return None

    def unload_skill(self, name: str) -> bool:
        """Remove a loaded skill's tools, binary grants, and body.

        Returns True if it was loaded. A binary stays granted while another
        loaded skill still declares it.
        """
        from gaia.skills.loader import unregister_skill_tools

        if name not in self.loaded_skills:
            return False

        removed = unregister_skill_tools(name)
        if self._instance_tools is not None:
            for key in removed:
                self._instance_tools.pop(key, None)
        revoked = self.granted_binaries.revoke_skill(name)
        self.loaded_skills.pop(name, None)
        if self._active_skill_filter is not None:
            self._active_skill_filter = [
                n for n in self._active_skill_filter if n != name
            ]
        self.rebuild_system_prompt()
        logger.info(
            "Unloaded skill '%s'%s",
            name,
            f" (revoked {', '.join(revoked)})" if revoked else "",
        )
        return True

    # ------------------------------------------------------------------
    # Skill sets (issue #2466) — declarative, per-launch skill bundles
    # ------------------------------------------------------------------

    @property
    def skill_sets(self) -> "SkillSets":
        """This agent's parsed ``skills:`` / ``skill_sets:`` declarations.

        Read once from :attr:`SKILL_MANIFEST` — or from the manifest found beside
        the agent's own module — and cached. Falsy (and empty) when the agent has
        no manifest, so nothing changes for an agent that does not use skills.

        Raises:
            SkillValidationError: the manifest is unreadable, or its skill blocks
                are malformed. An agent whose own manifest cannot be read is
                broken, not degraded.
        """
        if getattr(self, "_skill_sets", None) is None:
            self._skill_sets = self._parse_skill_declarations(
                self._resolve_skill_manifest()
            )
        return self._skill_sets

    def _parse_skill_declarations(self, path: Optional[Path]) -> "SkillSets":
        """Parse the ``skills:`` / ``skill_sets:`` blocks of the manifest at *path*.

        Reads the YAML directly rather than going through
        :func:`gaia.hub.manifest.parse` so a custom agent under
        ``~/.gaia/agents/<id>/`` — whose manifest need not carry the hub's
        publishing fields — declares skills the same way a packaged agent does.
        """
        from gaia.skills.sets import SkillSets, parse_skill_sets

        if path is None:
            return SkillSets()

        import yaml

        try:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise _skill_validation_error(
                f"Could not read the agent manifest at {path}: {exc}. Fix the "
                "YAML — an unreadable manifest may be hiding a 'skills:' block, "
                "so GAIA will not assume the agent declares none."
            ) from exc

        if data is None:
            return SkillSets()
        return parse_skill_sets(data, where=f" in {path}")

    @property
    def active_skill_set(self) -> Optional[str]:
        """The skill set this agent resolved at startup, or ``None``."""
        return getattr(self, "_active_skill_set", None)

    def select_skill_set(self) -> Optional[str]:
        """Selector hook: which skill set fits this launch's runtime state?

        The framework calls this when no explicit ``skill_set`` was passed.
        Override it to key the active set off something the agent already knows
        — a connected account's type, a workspace mode, a device profile.
        Returning ``None`` means "no opinion", which defers to the manifest's
        ``default_skill_set``. Returning a name this agent does not declare
        fails loudly rather than falling back.
        """
        return None

    def resolve_skill_set(
        self, requested: Optional[str] = None
    ) -> "SkillSetResolution":
        """Resolve the active set: *requested* → :meth:`select_skill_set` → default.

        Args:
            requested: Explicit override. Defaults to the ``skill_set`` argument
                passed to ``__init__``.

        Raises:
            SkillSetError: the resolved name is not a declared set.
        """
        declarations = self.skill_sets
        explicit = requested if requested is not None else self._requested_skill_set
        # Only consult the hook when nothing explicit was asked for — an
        # explicit request must never be second-guessed by agent state.
        selected = None if (explicit or "").strip() else self.select_skill_set()
        return declarations.resolve(requested=explicit, selected=selected)

    def load_skill_set(
        self,
        requested: Optional[str] = None,
        *,
        manager: Optional["SkillManager"] = None,
    ) -> Dict[str, "Skill"]:
        """Resolve and load this agent's declared skills. Returns what loaded.

        A no-op returning ``{}`` when the agent declares no skills. Otherwise it
        loads the always-on ``skills:`` list plus the resolved set: each entry's
        SemVer range is matched against what is installed, and a skill whose
        tools another declared skill consumes loads first (declaration order
        breaks every remaining tie). A skill declared ``required: false`` that is
        missing or version-incompatible is logged and skipped; every other
        failure propagates, because an agent launched with the wrong
        capabilities is worse than one that refuses to launch.

        Called automatically at the end of ``Agent.__init__``. Call it again with
        a different name to switch sets mid-session; the previous set's skills are
        unloaded once the new set has loaded, so the two never overlap.

        **All-or-nothing.** A failure part-way through leaves the agent exactly as
        it was — the previous set still loaded, ``active_skill_set`` still
        accurate. A half-switched agent reporting one set while carrying another's
        skills is worse than a raised error.

        Note the switch is not sticky: a later bare ``load_skill_set()`` re-runs
        the full resolution and returns to whatever that yields (the explicit
        ``skill_set`` passed to ``__init__``, else the hook, else the default).
        Pass the name again to stay on it.
        """
        from gaia.skills.consume import requirements_from_refs, resolve_requirements

        declarations = self.skill_sets
        explicit = requested if requested is not None else self._requested_skill_set
        if not declarations:
            if (explicit or "").strip():
                # Never drop an explicit request on the floor — resolve() raises
                # with the actionable "this agent declares no skill_sets" message.
                declarations.resolve(requested=explicit)
            return {}

        resolution = self.resolve_skill_set(requested)
        wanted = {ref.name for ref in resolution.skills}
        previously_loaded = list(self._skill_set_loaded or [])
        resolver = manager if manager is not None else self.skill_manager

        # Match the declared version ranges against what is installed and order
        # by intra-set tool dependencies BEFORE loading anything. A missing or
        # incompatible *required* skill raises here, so the agent is untouched.
        resolved = resolve_requirements(
            requirements_from_refs(
                resolution.skills,
                origin=(
                    f"skill set '{resolution.name}'"
                    if resolution.name
                    else self._SKILL_MANIFEST_FILENAME
                ),
            ),
            manager=resolver,
        )
        for name, reason in resolved.skipped.items():
            logger.warning(
                "Agent %s: optional skill '%s' not loaded — %s",
                type(self).__name__,
                name,
                reason,
            )

        # Load the new set BEFORE dropping the old one, and track what this call
        # actually brought in, so a failure can be undone completely.
        loaded: Dict[str, "Skill"] = {}
        newly_loaded: List[str] = []
        try:
            for skill in resolved.order:
                already_present = skill.name in self.loaded_skills
                loaded[skill.name] = self.load_skill(skill.name, manager=resolver)
                if not already_present:
                    newly_loaded.append(skill.name)
        except Exception:
            # Roll back to the pre-call state: drop only what this call added,
            # and leave _active_skill_set / _skill_set_loaded untouched so the
            # agent keeps reporting the set it is actually carrying.
            for name in newly_loaded:
                self.unload_skill(name)
            logger.error(
                "Skill set '%s' failed to load; the agent is unchanged and skill "
                "set '%s' remains active.",
                resolution.name,
                self._active_skill_set or "(none)",
            )
            raise

        # The new set is fully loaded — now retire the previous one's leftovers.
        # Scoped to set-loaded names, so a skill the agent loaded itself via
        # ``load_skill`` is never a set's to unload.
        for stale in [n for n in previously_loaded if n not in wanted]:
            self.unload_skill(stale)

        self._active_skill_set = resolution.name
        self._skill_set_loaded = list(loaded)
        logger.info(
            "Skill set '%s' active (chosen by: %s) — loaded %d of %d declared "
            "skill(s): %s",
            resolution.name or "(none)",
            resolution.source,
            len(loaded),
            len(resolution.skills),
            ", ".join(loaded) or "(none)",
        )
        return loaded

    def get_skills_system_prompt(self) -> str:
        """Render the loaded skills as a system-prompt fragment.

        Auto-discovered by ``_get_mixin_prompts()``, so a skill's instructions
        reach the model as soon as it is loaded. Returns "" when no skill is
        loaded, keeping every existing agent's prompt byte-identical.

        ``_active_skill_filter is None`` (the default, every agent that hasn't
        opted into per-turn selection) renders every loaded skill's full body
        every turn — the original, byte-identical behavior. When a subclass
        has set the filter (GaiaAgent, #2848 follow-up), a loaded skill whose
        name is not in the filter — and is not in the manifest's always-on
        ``skills:`` list — collapses to a one-line menu entry instead of its
        full body, so a loaded-but-irrelevant skill costs a menu line, not a
        multi-KB body, on the turns that don't need it.
        """
        skills = getattr(self, "_loaded_skills", None)
        if not skills:
            return ""

        active_filter = getattr(self, "_active_skill_filter", None)
        if active_filter is None:
            sections = []
            for skill in skills.values():
                if not skill.body:
                    continue
                sections.append(f"--- SKILL: {skill.name} ---\n{skill.body}")
            if not sections:
                return ""
            return "==== LOADED SKILLS ====\n" + "\n\n".join(sections)

        active = set(active_filter) | self._always_on_skill_names
        body_sections = []
        menu_lines = []
        for skill in sorted(skills.values(), key=lambda s: s.name):
            if skill.name in active:
                if skill.body:
                    body_sections.append(f"--- SKILL: {skill.name} ---\n{skill.body}")
            else:
                menu_lines.append(
                    f"- {skill.name}: {_skill_menu_description(skill.description)}"
                )

        parts = []
        if body_sections:
            parts.append(
                "==== LOADED SKILLS (active this turn) ====\n"
                + "\n\n".join(body_sections)
            )
        if menu_lines:
            parts.append(
                "==== LOADED SKILLS (instructions hidden this turn to save "
                "space — call load_skill('<name>') again to bring one back) "
                "====\n" + "\n".join(menu_lines)
            )
        return "\n\n".join(parts)

    def list_tools(self, verbose: bool = True) -> None:
        """
        Display all tools registered for this agent with their parameters and descriptions.

        Args:
            verbose: If True, displays full descriptions and parameter details. If False, shows a compact list.
        """
        self.console.print_header(f"🛠️ Registered Tools for {self.__class__.__name__}")
        self.console.print_separator()

        for name, tool_info in self.get_tools_info().items():
            # Format parameters
            params = []
            for param_name, param_info in tool_info["parameters"].items():
                required = param_info.get("required", False)
                param_type = param_info.get("type", "Any")
                default = param_info.get("default", None)

                if required:
                    params.append(f"{param_name}: {param_type}")
                else:
                    default_str = f"={default}" if default is not None else "=None"
                    params.append(f"{param_name}: {param_type}{default_str}")

            params_str = ", ".join(params)

            # Get description
            if verbose:
                description = tool_info["description"]
            else:
                description = (
                    tool_info["description"].split("\n")[0]
                    if tool_info["description"]
                    else "No description"
                )

            # Print tool information
            self.console.print_tool_info(name, params_str, description)

        self.console.print_separator()

        return None

    def get_tools_info(self) -> Dict[str, Any]:
        """Get information about all registered tools."""
        return self._tools_registry

    def get_tools(self) -> List[Dict[str, Any]]:
        """Get a list of registered tools for the agent."""
        return list(self._tools_registry.values())

    def _extract_embedded_tool_call(self, response: str) -> Optional[Dict[str, Any]]:
        """
        Detect and extract a tool call JSON embedded in a text response.

        LLMs sometimes output narrative text followed by a JSON tool call, e.g.:
        "Let me search for that.\n{"thought": "...", "tool": "query_documents",
         "tool_args": {"query": "..."}}"

        Decision logic over all {…} candidates that contain a "tool" key,
        each tagged fenced/unfenced:
          1. ≥1 unfenced candidate → return the first (unchanged — zero regression).
          2. else exactly one fenced candidate → return it (the fix for #1428).
          3. else >1 fenced, 0 unfenced → ambiguous (looks like docs) → None + warning.
          4. else → fall back to Python-call syntax detection (#2521), e.g.
             ``remember(fact="...", category="preference")``.

        This method finds the JSON block using brace-depth matching and returns
        the parsed tool call if it contains a "tool" key.  Returns None if no
        embedded tool call is found, allowing the caller to treat the response
        as plain text.

        Raises:
            ValueError: propagated from the Python-call-syntax fallback (#2521)
                when a *registered* tool's name is followed by an argument list
                that can't be parsed — a loud failure rather than echoing the
                raw syntax to the user as an answer.
        """
        # Quick check: must contain "tool" to be worth scanning for the JSON
        # shape. Responses without it may still carry the #2521 Python-call
        # shape below (e.g. no literal "tool" substring at all).
        if '"tool"' not in response:
            return self._extract_function_call_tool_syntax(response)

        # Build a set of character ranges inside code fences (```...```)
        _code_ranges: list[tuple[int, int]] = []
        _search_from = 0
        while True:
            _open = response.find("```", _search_from)
            if _open == -1:
                break
            _close = response.find("```", _open + 3)
            if _close == -1:
                # Unclosed fence — treat rest as code
                _code_ranges.append((_open, len(response)))
                break
            _code_ranges.append((_open, _close + 3))
            _search_from = _close + 3

        def _inside_code_fence(pos: int) -> bool:
            return any(start <= pos < end for start, end in _code_ranges)

        def _parse_candidate(raw: str) -> Optional[Dict[str, Any]]:
            """Return the parsed dict if raw is valid JSON with a 'tool' key, else None."""
            try:
                fixed = re.sub(r",\s*}", "}", raw)
                fixed = re.sub(r",\s*]", "]", fixed)
                parsed = json.loads(fixed)
                if isinstance(parsed, dict) and "tool" in parsed:
                    if "tool_args" not in parsed:
                        parsed["tool_args"] = {}
                    return parsed
            except json.JSONDecodeError:
                pass
            return None

        # Collect all tool-call candidates, tagged by whether they are inside a fence
        unfenced: list[Dict[str, Any]] = []
        fenced: list[Dict[str, Any]] = []

        idx = 0
        while idx < len(response):
            brace_pos = response.find("{", idx)
            if brace_pos == -1:
                break

            is_fenced = _inside_code_fence(brace_pos)

            # Look ahead for "tool" near this brace (within 200 chars)
            look_ahead = response[brace_pos : brace_pos + 200]
            if '"tool"' not in look_ahead and '"thought"' not in look_ahead:
                idx = brace_pos + 1
                continue

            # Use brace-depth matching to find the complete JSON object
            depth = 0
            in_str = False
            escape = False
            end_pos = brace_pos
            for j in range(brace_pos, len(response)):
                ch = response[j]
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"' and not escape:
                    in_str = not in_str
                if not in_str:
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end_pos = j
                            break

            if depth != 0:
                idx = brace_pos + 1
                continue

            raw = response[brace_pos : end_pos + 1]
            parsed = _parse_candidate(raw)
            if parsed is not None:
                if is_fenced:
                    fenced.append(parsed)
                else:
                    unfenced.append(parsed)

            idx = brace_pos + 1

        # Decision logic
        if unfenced:
            # Rule 1: prefer unfenced (unchanged behaviour — zero regression)
            logger.debug(
                "[PARSE] Extracted embedded tool call: %s", unfenced[0].get("tool")
            )
            return unfenced[0]

        if len(fenced) == 1:
            # Rule 2: exactly one fenced call — trust it (fix for #1428)
            logger.debug(
                "[PARSE] Extracted fenced tool call: %s", fenced[0].get("tool")
            )
            return fenced[0]

        if len(fenced) > 1:
            # Rule 3: multiple fenced calls — ambiguous, likely documentation examples
            logger.warning(
                "[PARSE] ambiguous: %d fenced tool-call candidates found and no "
                "unfenced call; cannot determine which is real — returning None",
                len(fenced),
            )
            return None

        # Rule 4: the "tool" marker was present but matched no JSON-shaped
        # candidate (e.g. it appeared in unrelated text) — fall back to the
        # Python-call syntax detector (#2521) before giving up.
        return self._extract_function_call_tool_syntax(response)

    _FUNC_CALL_NAME_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")

    def _extract_function_call_tool_syntax(
        self, response: str
    ) -> Optional[Dict[str, Any]]:
        """Detect a Python-call-style tool invocation embedded in text (#2521).

        On the non-tool-calling (embedded-JSON) path the prompt only ever
        teaches the JSON shape ``{"tool": "name", "tool_args": {...}}``, but
        some models — observed on the FastFlowLM/NPU backend — instead emit
        a bare Python-style call, e.g.::

            remember(fact="TechCrunch emails are low priority", category="preference")

        Without this, that text falls through to the plain-text answer path:
        the raw call syntax is shown to the user and the tool never runs.

        Only names that match a tool actually **registered** on this agent
        are treated as calls, so ordinary prose that happens to contain
        "word(...)" (code snippets, examples) is left as plain text. A name
        match with an argument list that can't be parsed is a loud failure
        (raises ``ValueError``) rather than being echoed to the user.

        Returns:
            ``{"tool": name, "tool_args": {...}}`` on a successful match, or
            ``None`` if no registered-tool call syntax is present.

        Raises:
            ValueError: a registered tool's name is followed by an argument
                list that could not be parsed as Python literals.
        """
        registry = self._tools_registry
        if not registry:
            return None

        for match in self._FUNC_CALL_NAME_RE.finditer(response):
            name = match.group(1)
            if name not in registry:
                continue

            open_paren = match.end() - 1
            close_paren = _find_matching_close_paren(response, open_paren)
            if close_paren is None:
                raise ValueError(
                    f"Detected an unterminated call to tool '{name}' — cannot "
                    "execute it. Raw text: "
                    f"{response[match.start():match.start() + 200]!r}"
                )

            call_src = response[match.start() : close_paren + 1]
            try:
                node = ast.parse(call_src, mode="eval").body
                if not isinstance(node, ast.Call) or node.args:
                    raise ValueError("expected a call with only keyword arguments")
                tool_args: Dict[str, Any] = {}
                for kw in node.keywords:
                    if kw.arg is None:
                        raise ValueError("**kwargs expansion is not supported")
                    tool_args[kw.arg] = ast.literal_eval(kw.value)
            except (SyntaxError, ValueError) as exc:
                raise ValueError(
                    f"Detected a call to tool '{name}' but could not parse "
                    f"its arguments: {exc}. Raw call: {call_src[:200]!r}"
                ) from exc

            logger.debug("[PARSE] Extracted function-call-syntax tool call: %s", name)
            return {"tool": name, "tool_args": tool_args}

        return None

    def _extract_json_from_response(self, response: str) -> Optional[Dict[str, Any]]:
        """
        Apply multiple extraction strategies to find valid JSON in the response.

        Args:
            response: The raw response from the LLM

        Returns:
            Extracted JSON dictionary or None if extraction failed
        """
        # Strategy 1: Extract JSON from code blocks with various patterns
        json_patterns = [
            r"```(?:json)?\s*(.*?)\s*```",  # Standard code block
            r"`json\s*(.*?)\s*`",  # Single backtick with json tag
            r"<json>\s*(.*?)\s*</json>",  # XML-style tags
        ]

        for pattern in json_patterns:
            matches = re.findall(pattern, response, re.DOTALL)
            for match in matches:
                try:
                    result = json.loads(match)
                    # Ensure tool_args exists if tool is present
                    if "tool" in result and "tool_args" not in result:
                        result["tool_args"] = {}
                    logger.debug(f"Successfully extracted JSON with pattern {pattern}")
                    return result
                except json.JSONDecodeError:
                    continue

        start_idx = response.find("{")
        if start_idx >= 0:
            bracket_count = 0
            in_string = False
            escape_next = False

            for i, char in enumerate(response[start_idx:], start_idx):
                if escape_next:
                    escape_next = False
                    continue
                if char == "\\":
                    escape_next = True
                    continue
                if char == '"' and not escape_next:
                    in_string = not in_string
                if not in_string:
                    if char == "{":
                        bracket_count += 1
                    elif char == "}":
                        bracket_count -= 1
                        if bracket_count == 0:
                            # Found complete JSON object
                            try:
                                extracted = response[start_idx : i + 1]
                                # Fix common issues before parsing
                                fixed = re.sub(r",\s*}", "}", extracted)
                                fixed = re.sub(r",\s*]", "]", fixed)
                                result = json.loads(fixed)
                                # Ensure tool_args exists if tool is present
                                if "tool" in result and "tool_args" not in result:
                                    result["tool_args"] = {}
                                logger.debug(
                                    "Successfully extracted JSON using bracket-matching"
                                )
                                return result
                            except json.JSONDecodeError as e:
                                logger.debug(f"Bracket-matched JSON parse failed: {e}")
                                break

        return None

    def validate_json_response(self, response_text: str) -> Dict[str, Any]:
        """
        Validates and attempts to fix JSON responses from the LLM.

        Attempts the following fixes in order:
        1. Parse as-is if valid JSON
        2. Extract JSON from code blocks
        3. Truncate after first complete JSON object
        4. Fix common JSON syntax errors
        5. Extract JSON-like content using regex

        Args:
            response_text: The response string from the LLM

        Returns:
            A dictionary containing the parsed JSON if valid

        Raises:
            ValueError: If the response cannot be parsed as JSON or is missing required fields
        """
        original_response = response_text
        json_was_modified = False

        # Step 0: Sanitize control characters to ensure proper JSON format
        def sanitize_json_string(text: str) -> str:
            """
            Ensure JSON strings have properly escaped control characters.

            Args:
                text: JSON text that may contain unescaped control characters

            Returns:
                Sanitized JSON text with properly escaped control characters
            """

            def escape_string_content(match):
                """Ensure control characters are properly escaped in JSON string values."""
                quote = match.group(1)
                content = match.group(2)
                closing_quote = match.group(3)

                # Ensure proper escaping of control characters
                content = content.replace("\n", "\\n")
                content = content.replace("\r", "\\r")
                content = content.replace("\t", "\\t")
                content = content.replace("\b", "\\b")
                content = content.replace("\f", "\\f")

                return f"{quote}{content}{closing_quote}"

            # Match JSON strings: "..." handling escaped quotes
            pattern = r'(")([^"\\]*(?:\\.[^"\\]*)*)(")'

            try:
                return re.sub(pattern, escape_string_content, text)
            except Exception as e:
                logger.debug(
                    f"[JSON] String sanitization encountered issue: {e}, using original"
                )
                return text

        response_text = sanitize_json_string(response_text)

        # Step 1: Try to parse as-is
        try:
            json_response = json.loads(response_text)
            logger.debug("[JSON] Successfully parsed response without modifications")
        except json.JSONDecodeError as initial_error:
            # Step 2: Try to extract from code blocks
            json_match = re.search(
                r"```(?:json)?\s*({.*?})\s*```", response_text, re.DOTALL
            )
            if json_match:
                try:
                    response_text = json_match.group(1)
                    json_response = json.loads(response_text)
                    json_was_modified = True
                    logger.warning("[JSON] Extracted JSON from code block")
                except json.JSONDecodeError as e:
                    logger.debug(f"[JSON] Code block extraction failed: {e}")

            # Step 3: Try to find and extract first complete JSON object
            if not json_was_modified:
                # Find the first '{' and try to match brackets
                start_idx = response_text.find("{")
                if start_idx >= 0:
                    bracket_count = 0
                    in_string = False
                    escape_next = False

                    for i, char in enumerate(response_text[start_idx:], start_idx):
                        if escape_next:
                            escape_next = False
                            continue
                        if char == "\\":
                            escape_next = True
                            continue
                        if char == '"' and not escape_next:
                            in_string = not in_string
                        if not in_string:
                            if char == "{":
                                bracket_count += 1
                            elif char == "}":
                                bracket_count -= 1
                                if bracket_count == 0:
                                    # Found complete JSON object
                                    try:
                                        truncated = response_text[start_idx : i + 1]
                                        json_response = json.loads(truncated)
                                        json_was_modified = True
                                        logger.warning(
                                            f"[JSON] Truncated response after first complete JSON object (removed {len(response_text) - i - 1} chars)"
                                        )
                                        response_text = truncated
                                        break
                                    except json.JSONDecodeError:
                                        logger.debug(
                                            "[JSON] Truncated text is not valid JSON, trying next bracket pair"
                                        )
                                        continue

            # Step 4: Try to fix common JSON errors
            if not json_was_modified:
                fixed_text = response_text

                # Remove trailing commas
                fixed_text = re.sub(r",\s*}", "}", fixed_text)
                fixed_text = re.sub(r",\s*]", "]", fixed_text)

                # Fix single quotes to double quotes (carefully)
                if "'" in fixed_text and '"' not in fixed_text:
                    fixed_text = fixed_text.replace("'", '"')

                # Remove any text before first '{' or '['
                json_start = min(
                    fixed_text.find("{") if "{" in fixed_text else len(fixed_text),
                    fixed_text.find("[") if "[" in fixed_text else len(fixed_text),
                )
                if json_start > 0 and json_start < len(fixed_text):
                    fixed_text = fixed_text[json_start:]

                # Try to parse the fixed text
                if fixed_text != response_text:
                    try:
                        json_response = json.loads(fixed_text)
                        json_was_modified = True
                        logger.warning("[JSON] Applied automatic JSON fixes")
                        response_text = fixed_text
                    except json.JSONDecodeError as e:
                        logger.debug(f"[JSON] Auto-fix failed: {e}")

            # If still no valid JSON, raise the original error
            if not json_was_modified:
                raise ValueError(
                    f"Failed to parse response as JSON: {str(initial_error)}"
                )

        # Log warning if JSON was modified
        if json_was_modified:
            logger.warning(
                f"[JSON] Response was modified to extract valid JSON. Original length: {len(original_response)}, Fixed length: {len(response_text)}"
            )

        # Validate required fields
        # Note: 'goal' is optional for simple answer responses
        if "answer" in json_response:
            required_fields = ["thought", "answer"]  # goal is optional
        elif "tool" in json_response:
            required_fields = ["thought", "tool", "tool_args"]  # goal is optional
        else:
            required_fields = ["thought", "plan"]  # goal is optional

        missing_fields = [
            field for field in required_fields if field not in json_response
        ]
        if missing_fields:
            raise ValueError(
                f"Response is missing required fields: {', '.join(missing_fields)}"
            )

        return json_response

    def _build_openai_tool_schemas(self, filter_to: Optional[List[str]] = None) -> list:
        """Build OpenAI-format function-calling schemas from the tool registry.

        Args:
            filter_to: When ``None`` (default), build a schema for every
                registered tool in registry order — byte-identical to the legacy
                path. When a list, build only those names, in the given
                (pre-sorted) order, skipping any not present in the registry.
        """

        def _python_to_json_type(py_type: str) -> str:
            return {
                "str": "string",
                "int": "integer",
                "float": "number",
                "bool": "boolean",
                "list": "array",
                "dict": "object",
            }.get(py_type.lower().strip(), "string")

        if filter_to is None:
            items = list(self._tools_registry.items())
        else:
            registry = self._tools_registry
            items = [(n, registry[n]) for n in filter_to if n in registry]

        schemas = []
        for name, tool_info in items:
            properties = {}
            required = []
            for param_name, param_info in tool_info["parameters"].items():
                prop: Dict[str, Any] = {
                    "type": _python_to_json_type(param_info.get("type", "str"))
                }
                desc = param_info.get("description", "")
                if desc:
                    prop["description"] = desc
                properties[param_name] = prop
                if param_info.get("required", True):
                    required.append(param_name)
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool_info.get("description", ""),
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }
            )
        return schemas

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """
        Parse the LLM response to extract tool calls or conversational answers.

        ARCHITECTURE: Supports three response paths
        - Sentinel JSON string `{"__tool_calls__": ...}` — native OpenAI tool_calls
        - Plain JSON string `{"thought": ..., "tool": ..., "tool_args": ...}` — embedded format
        - Plain text — conversational answer

        Native tool_calls return shape (issue #944):
            {
                "thought": "", "goal": "",
                "tool_calls": [
                    {"id": str, "name": str, "tool_args": dict},
                    ...  # 1 or more — N>1 is the "parallel tool calls" case
                ],
                "content": str | None,  # assistant text emitted alongside calls
                # Backwards-compat: when N==1 the legacy single-call fields
                # ("tool", "tool_args") are also populated so older consumers
                # keep working unchanged. Newer code paths SHOULD prefer
                # ``tool_calls`` since it's the only field set when N>1.
                "tool": <name when N==1>,
                "tool_args": <args when N==1>,
            }

        Args:
            response: Raw string from LLM (or sentinel-encoded tool_calls)

        Returns:
            Parsed response as a dictionary
        """
        # In single_tool_per_turn mode, once a tool has run the next LLM turn
        # should be the final user-facing answer — never another tool call.
        if getattr(self, "_single_tool_done", False):
            return {"thought": "", "answer": (response or "").strip() or "Done."}

        # --- Native tool_calls branch ---
        # The Lemonade provider encodes native tool_calls as a sentinel JSON string
        # when the model uses OpenAI function calling. Detect it here so the rest
        # of the agent loop (tool execution, history, streaming) sees only str.
        if isinstance(response, str) and response.startswith('{"__tool_calls__":'):
            try:
                envelope = json.loads(response)
            except json.JSONDecodeError as exc:
                # Issue #1023: smaller LLMs occasionally emit envelopes with
                # (a) single-backslash Windows paths -> ``Invalid \escape``,
                # or (b) trailing commentary after the closing brace ->
                # ``Extra data``.  Mirror the inner-arguments recovery
                # (``json.loads`` on ``arguments`` below): retry once via
                # ``_repair_invalid_json_escapes``, then fall back to
                # ``raw_decode`` for the trailing-garbage case.  Only
                # surface the original parse error if both attempts fail.
                envelope = None
                repaired = _repair_invalid_json_escapes(response)
                if repaired != response:
                    try:
                        envelope = json.loads(repaired)
                        logger.debug(
                            "[PARSE] repaired invalid backslash escape(s) "
                            "in native tool_calls envelope"
                        )
                    except json.JSONDecodeError:
                        envelope = None
                if envelope is None and exc.msg.startswith("Extra data"):
                    # ``raw_decode`` parses one JSON value and returns
                    # (obj, end_idx); the suffix is whatever the model
                    # appended after the structured payload (commentary,
                    # whitespace, a stray brace).  Logged at info so a
                    # steady stream surfaces in production telemetry --
                    # if it's persistent, the prompt needs tightening,
                    # not the parser.
                    try:
                        decoder = json.JSONDecoder()
                        envelope, end_idx = decoder.raw_decode(response)
                        logger.info(
                            "[PARSE] tolerated trailing data after native "
                            "tool_calls envelope (%d chars discarded)",
                            len(response) - end_idx,
                        )
                    except json.JSONDecodeError:
                        envelope = None
                if envelope is None:
                    raise ValueError(
                        f"Malformed native tool_calls envelope: {exc}"
                    ) from exc
            # Issue #1023: after the recovery path (repair or ``raw_decode``)
            # an envelope can be syntactically valid JSON without the
            # ``__tool_calls__`` key -- e.g. ``raw_decode`` of
            # ``{"foo":1}<trailing>`` returns ``{"foo":1}``.  Convert the
            # bare ``envelope["__tool_calls__"]`` lookup into a checked one
            # so the recovery branch at L2820 (which only catches
            # ``ValueError``/``NotImplementedError``) handles it -- a bare
            # ``KeyError`` would escape and crash the session.
            raw_tool_calls = envelope.get("__tool_calls__")
            if raw_tool_calls is None:
                raise ValueError(
                    "Malformed native tool_calls envelope: parsed prefix "
                    "lacks __tool_calls__ key."
                )
            finish_reason = envelope.get("finish_reason", "")
            if finish_reason == "length":
                # ``finish_reason="length"`` from the OpenAI completions API
                # signals the model hit the **max_tokens output cap**, NOT the
                # context window — those are separate limits and conflating
                # them led to misleading error messages telling users to
                # raise ``--ctx-size`` when their ctx was already 32K. The
                # actual fix is bumping the output budget in
                # ``AgentConfig.max_tokens`` (or, for one-off long tool calls,
                # asking the model to pick a single value rather than
                # concatenating).
                raise ValueError(
                    f"Tool call truncated mid-arguments (finish_reason=length). "
                    f"Model {self.model_id} ran out of output tokens before "
                    f"finishing the call — increase AgentConfig.max_tokens."
                )
            if not raw_tool_calls:
                raise ValueError(
                    "Native tool_calls envelope contained an empty tool_calls list."
                )
            # Normalise every entry. Tool-calling-trained models routinely
            # emit multiple tool_calls per response when a user utterance
            # contains multiple distinct intents (issue #944). Each call gets
            # parsed independently so a single bad-arguments entry only
            # poisons that one call's parse, not the others.
            normalised: list[Dict[str, Any]] = []
            for idx, tc in enumerate(raw_tool_calls):
                name = tc["function"]["name"]
                arguments_raw = tc["function"].get("arguments")
                # ``arguments`` is canonically a JSON string per OpenAI spec,
                # but llama.cpp 4B-class models occasionally emit it
                # pre-parsed as a dict. Accept both shapes — only call
                # ``json.loads`` when it's actually a string.
                if arguments_raw is None or arguments_raw == "":
                    tool_args: Dict[str, Any] = {}
                elif isinstance(arguments_raw, dict):
                    tool_args = arguments_raw
                elif isinstance(arguments_raw, (str, bytes, bytearray)):
                    args_str = (
                        arguments_raw.decode("utf-8")
                        if isinstance(arguments_raw, (bytes, bytearray))
                        else arguments_raw
                    )
                    try:
                        tool_args = json.loads(args_str)
                    except json.JSONDecodeError as exc:
                        # Issue #1023: Windows paths emitted with single
                        # backslashes (``C:\Users\Klaus``) -> ``\U`` is
                        # invalid JSON.  Repair invalid escapes and retry
                        # once before surfacing the error to the recovery
                        # layer.
                        repaired = _repair_invalid_json_escapes(args_str)
                        if repaired == args_str:
                            raise ValueError(
                                f"Malformed tool_call arguments for '{name}': {exc}. "
                                f"Raw arguments: {args_str[:200]}"
                            ) from exc
                        try:
                            tool_args = json.loads(repaired)
                        except json.JSONDecodeError as exc2:
                            raise ValueError(
                                f"Malformed tool_call arguments for '{name}': {exc2}. "
                                f"Raw arguments: {args_str[:200]}"
                            ) from exc2
                        logger.debug(
                            "[PARSE] repaired invalid backslash escape(s) in "
                            "tool_call args for '%s'",
                            name,
                        )
                else:
                    # Unexpected shape (list / int / None-ish) — treat as
                    # malformed so the recovery layer in process_query nudges
                    # the model to retry with valid arguments.
                    raise ValueError(
                        f"Malformed tool_call arguments for '{name}': expected "
                        f"str or dict, got {type(arguments_raw).__name__}"
                    )
                # Use the model-supplied id when present so tool result
                # messages can be correlated back to their originating call;
                # synthesise one when absent (some llama.cpp builds omit it).
                tc_id = tc.get("id") or f"call_{idx}_{uuid.uuid4().hex[:8]}"
                normalised.append({"id": tc_id, "name": name, "tool_args": tool_args})
            content = envelope.get("content")
            logger.debug(
                "[PARSE] tool_call_path=native model_id=%s n_calls=%d tools=%s",
                self.model_id,
                len(normalised),
                [tc["name"] for tc in normalised],
            )
            parsed: Dict[str, Any] = {
                "thought": "",
                "goal": "",
                "tool_calls": normalised,
                "content": content,
            }
            # Backwards-compat: populate the legacy single-call fields when
            # there's exactly one call so existing consumers (and the
            # embedded-JSON code path in process_query) keep working without
            # change. The legacy fields are intentionally absent for N>1 to
            # force callers into the fan-out path.
            if len(normalised) == 1:
                parsed["tool"] = normalised[0]["name"]
                parsed["tool_args"] = normalised[0]["tool_args"]
            return parsed

        # Check for empty responses
        if not response or not response.strip():
            logger.warning("Empty LLM response received")
            self.error_history.append("Empty LLM response")

            # Provide more helpful error message based on context
            if hasattr(self, "api_mode") and self.api_mode:  # pylint: disable=no-member
                answer = "I encountered an issue processing your request. This might be due to a connection problem with the language model. Please try again."
            else:
                answer = "I apologize, but I received an empty response from the language model. Please try again."

            return {
                "thought": "LLM returned empty response",
                "goal": "Handle empty response error",
                "answer": answer,
            }

        response = response.strip()

        # Log what we received for debugging (show more to see full JSON)
        if len(response) > 500:
            logger.debug(
                f"📥 LLM Response ({len(response)} chars): {response[:500]}..."
            )
        else:
            logger.debug(f"📥 LLM Response: {response}")

        # STEP 1: Fast path - detect plain text conversational responses
        # If response doesn't start with '{', it's likely plain text.
        # However, LLMs sometimes prefix a tool call JSON with narrative text
        # like "Let me search for that.\n{"tool": "query_documents", ...}".
        # Detect and extract embedded tool calls before treating as plain text.
        if not response.startswith("{"):
            # Check for embedded tool call JSON: look for {"tool" or {"thought"
            # patterns that indicate a structured response is buried in the text
            embedded_json = self._extract_embedded_tool_call(response)
            if embedded_json:
                logger.debug("[PARSE] Found embedded tool call in text response")
                return embedded_json
            logger.debug(
                f"[PARSE] Plain text conversational response (length: {len(response)})"
            )
            return {"thought": "", "goal": "", "answer": response}

        # STEP 2: Response starts with '{' - looks like JSON
        # Try direct JSON parsing first (fastest path)
        try:
            result = json.loads(response)
            # Ensure tool_args exists if tool is present
            if "tool" in result and "tool_args" not in result:
                result["tool_args"] = {}
            logger.debug("[PARSE] Valid JSON response")
            return result
        except json.JSONDecodeError:
            # JSON parsing failed - continue to extraction methods
            logger.debug("[PARSE] Malformed JSON, trying extraction")

        # STEP 3: Try JSON extraction methods (handles code blocks, mixed text, etc.)
        extracted_json = self._extract_json_from_response(response)
        if extracted_json:
            logger.debug("[PARSE] Extracted JSON successfully")
            return extracted_json

        # STEP 4: JSON was expected (starts with '{') but all parsing failed
        # Log error ONLY for JSON that couldn't be parsed
        logger.debug("Attempting to extract fields using regex")
        thought_match = re.search(r'"thought":\s*"([^"]*)"', response)
        tool_match = re.search(r'"tool":\s*"([^"]*)"', response)
        answer_match = re.search(r'"answer":\s*"([^"]*)"', response)
        plan_match = re.search(r'"plan":\s*(\[.*?\])', response, re.DOTALL)

        # Check for tool calls FIRST — if a response has both "tool" and
        # "answer", the tool should be executed because the "answer" is
        # often just the LLM narrating what it plans to do, not the final
        # response.  The real answer will come after the tool executes.
        if tool_match:
            tool_args = {}

            tool_args_start = response.find('"tool_args"')

            if tool_args_start >= 0:
                # Find the opening brace after "tool_args":
                brace_start = response.find("{", tool_args_start)
                if brace_start >= 0:
                    # Use bracket-matching to find the complete object
                    bracket_count = 0
                    in_string = False
                    escape_next = False
                    for i, char in enumerate(response[brace_start:], brace_start):
                        if escape_next:
                            escape_next = False
                            continue
                        if char == "\\":
                            escape_next = True
                            continue
                        if char == '"' and not escape_next:
                            in_string = not in_string
                        if not in_string:
                            if char == "{":
                                bracket_count += 1
                            elif char == "}":
                                bracket_count -= 1
                                if bracket_count == 0:
                                    # Found complete tool_args object
                                    tool_args_str = response[brace_start : i + 1]
                                    try:
                                        tool_args = json.loads(tool_args_str)
                                    except json.JSONDecodeError as e:
                                        error_msg = f"Failed to parse tool_args JSON: {str(e)}, content: {tool_args_str[:100]}..."
                                        logger.error(error_msg)
                                        self.error_history.append(error_msg)
                                    break

            result = {
                "thought": thought_match.group(1) if thought_match else "",
                "goal": "clear statement of what you're trying to achieve",
                "tool": tool_match.group(1),
                "tool_args": tool_args,
            }

            # Add plan if found
            if plan_match:
                try:
                    result["plan"] = json.loads(plan_match.group(1))
                    logger.debug(f"Extracted plan using regex: {result['plan']}")
                except json.JSONDecodeError as e:
                    error_msg = f"Failed to parse plan JSON: {str(e)}, content: {plan_match.group(1)[:100]}..."
                    logger.error(error_msg)
                    self.error_history.append(error_msg)

            logger.debug(f"Extracted tool call using regex: {result}")
            return result

        # Fall back to answer extraction (only reached if no tool was found)
        if answer_match:
            result = {
                "thought": thought_match.group(1) if thought_match else "",
                "goal": "what was achieved",
                "answer": answer_match.group(1),
            }
            logger.debug(f"Extracted answer using regex: {result}")
            return result

        # Try to match simple key-value patterns for object names (like ': "my_cube"')
        obj_name_match = re.search(
            r'["\':]?\s*["\'"]?([a-zA-Z0-9_\.]+)["\'"]?', response
        )
        if obj_name_match:
            object_name = obj_name_match.group(1)
            # If it looks like an object name and not just a random word
            if "." in object_name or "_" in object_name:
                logger.debug(f"Found potential object name: {object_name}")
                return {
                    "thought": "Extracted object name",
                    "goal": "Use the object name",
                    "answer": object_name,
                }

        # CONVERSATIONAL MODE: No JSON found - treat as plain conversational response
        # This is normal and expected for chat agents responding to greetings, explanations, etc.
        logger.debug(
            f"[PARSE] No JSON structure found, treating as conversational response. Length: {len(response)}, preview: {response[:100]}..."
        )

        # If response is empty, provide a meaningful fallback
        if not response.strip():
            logger.warning("[PARSE] Empty response received from LLM")
            return {
                "thought": "",
                "goal": "",
                "answer": "I apologize, but I received an empty response. Please try again.",
            }

        # Valid conversational response - wrap it in expected format
        return {"thought": "", "goal": "", "answer": response.strip()}

    def _resolve_plan_parameters(
        self, tool_args: Any, step_results: List[Dict[str, Any]], _depth: int = 0
    ) -> Any:
        """
        Recursively resolve placeholder references in tool arguments from previous step results.

        Supports dynamic parameter substitution in multi-step plans:
        - $PREV.field - Get field from previous step result
        - $STEP_0.field - Get field from specific step result (0-indexed)

        Args:
            tool_args: Tool arguments that may contain placeholders
            step_results: List of results from previously executed steps
            _depth: Internal recursion depth counter (max 50 levels)

        Returns:
            Tool arguments with placeholders resolved to actual values

        Examples:
            >>> step_results = [{"image_path": "/path/to/img.png", "status": "success"}]
            >>> tool_args = {"image_path": "$PREV.image_path", "style": "dramatic"}
            >>> resolved = agent._resolve_plan_parameters(tool_args, step_results)
            >>> resolved
            {"image_path": "/path/to/img.png", "style": "dramatic"}

        Backward Compatibility:
            - If no placeholders exist, returns original tool_args unchanged
            - If placeholder references invalid step/field, returns placeholder string unchanged

        Limitations:
            - Field names cannot contain dots (e.g., $PREV.user.name not supported - use $PREV.user_name)
            - Maximum nesting depth of 50 levels to prevent stack overflow
            - No type checking - resolved values are used as-is (tools should validate inputs)
        """
        # Prevent stack overflow from deeply nested structures
        MAX_DEPTH = 50
        if _depth > MAX_DEPTH:
            logger.warning(
                f"Maximum recursion depth ({MAX_DEPTH}) exceeded in parameter resolution, returning unchanged"
            )
            return tool_args

        # Handle dict: recursively resolve each value
        if isinstance(tool_args, dict):
            return {
                k: self._resolve_plan_parameters(v, step_results, _depth + 1)
                for k, v in tool_args.items()
            }

        # Handle list: recursively resolve each item
        elif isinstance(tool_args, list):
            return [
                self._resolve_plan_parameters(item, step_results, _depth + 1)
                for item in tool_args
            ]

        # Handle string: check for placeholder patterns
        elif isinstance(tool_args, str):
            # Handle $PREV.field - get field from previous step
            if tool_args.startswith("$PREV.") and step_results:
                field = tool_args[6:]  # Strip "$PREV."
                prev_result = step_results[-1]
                if isinstance(prev_result, dict) and field in prev_result:
                    resolved = prev_result[field]
                    logger.debug(
                        f"Resolved {tool_args} -> {resolved} from previous step result"
                    )
                    return resolved
                else:
                    logger.warning(
                        f"Could not resolve {tool_args}: field '{field}' not found in previous result"
                    )
                    return tool_args  # Return unchanged if field not found

            # Handle $STEP_N.field - get field from specific step
            match = re.match(r"\$STEP_(\d+)\.(.+)", tool_args)
            if match and step_results:
                step_idx = int(match.group(1))
                field = match.group(2)
                if 0 <= step_idx < len(step_results):
                    step_result = step_results[step_idx]
                    if isinstance(step_result, dict) and field in step_result:
                        resolved = step_result[field]
                        logger.debug(
                            f"Resolved {tool_args} -> {resolved} from step {step_idx} result"
                        )
                        return resolved
                    else:
                        logger.warning(
                            f"Could not resolve {tool_args}: field '{field}' not found in step {step_idx} result"
                        )
                else:
                    logger.warning(
                        f"Could not resolve {tool_args}: step {step_idx} out of range (0-{len(step_results)-1})"
                    )
                return tool_args  # Return unchanged if reference invalid

        # For all other types (int, float, bool, None), return unchanged
        return tool_args

    def _resolve_tool_name(self, tool_name: str) -> Optional[str]:
        """Resolve an unrecognised tool name to a registered one.

        Handles common LLM mistakes:
        - Unprefixed MCP names  ("get_current_time" -> "mcp_time_get_current_time")
        - Case-insensitive match ("Get_Current_Time" -> "mcp_time_get_current_time")

        Returns the resolved name, or None if no unique match is found.
        """
        lower = tool_name.lower()
        suffix = f"_{lower}"
        registry = self._tools_registry
        matches = [n for n in registry if n.lower().endswith(suffix)]
        if len(matches) == 1:
            return matches[0]
        # Also try exact case-insensitive match
        matches = [n for n in registry if n.lower() == lower]
        if len(matches) == 1:
            return matches[0]
        return None

    def _resolve_tool_timeout(self, tool_name: str) -> float:
        """Resolve the execution timeout (seconds) for a tool.

        A per-tool ``@tool(timeout=...)`` override wins; otherwise the global
        ``GAIA_AGENT_TOOL_TIMEOUT`` default applies.
        """
        entry = self._tools_registry.get(tool_name) or {}
        override = entry.get("timeout")
        if override is not None:
            return float(override)
        return tool_execution_timeout()

    def _call_tool_bounded(
        self, tool: Callable, tool_args: Dict[str, Any], tool_name: str
    ) -> Any:
        """Run a tool body under a bounded execution window.

        The tool runs in a daemon worker thread joined with the resolved
        timeout. On success the worker's return value is returned and any
        exception it raised is re-raised in the caller (so the existing
        ``_execute_tool`` error handling applies unchanged). On timeout a
        ``ToolExecutionTimeout`` is raised — the worker keeps running (Python
        cannot kill a thread) but it is a daemon, so it cannot block process
        exit and the agent loop is freed immediately.
        """
        timeout = self._resolve_tool_timeout(tool_name)
        holder: Dict[str, Any] = {}

        def _target():
            try:
                holder["result"] = tool(**tool_args)
            except BaseException as exc:  # noqa: BLE001 — re-raised in caller
                holder["exc"] = exc

        worker = threading.Thread(target=_target, name=f"tool:{tool_name}", daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            raise ToolExecutionTimeout(tool_name, timeout)
        if "exc" in holder:
            raise holder["exc"]
        return holder.get("result")

    @classmethod
    def confirmation_required_tools(cls) -> frozenset:
        """The full set of tool names gated behind explicit user confirmation
        for this agent (#1440): the generic dangerous base set
        (``TOOLS_REQUIRING_CONFIRMATION``) unioned with the agent's own
        ``CONFIRMATION_REQUIRED_TOOLS``. Subclasses declare only their
        agent-specific tools without re-listing the shared shell/file-mutation
        ones.

        This is the *static* half of the gate; ``_tool_requires_confirmation``
        combines it with the per-entry flag that covers runtime-registered
        (MCP) tools, and is what ``_execute_tool`` actually calls.
        """
        return frozenset(TOOLS_REQUIRING_CONFIRMATION) | frozenset(
            cls.CONFIRMATION_REQUIRED_TOOLS
        )

    def _confirmation_denied_error(self, tool_name: str) -> str:
        """Explain a confirmation denial to the model in the tool result.

        Consoles may expose ``confirmation_denied_reason`` to distinguish "a human
        said no" from "nothing could ask a human" (#2210); duck-typed so custom
        handlers without the hook still get the generic wording.
        """
        reason_hook = getattr(self.console, "confirmation_denied_reason", None)
        if callable(reason_hook):
            reason = reason_hook(tool_name)
            if reason:
                return str(reason)
        return f"Tool '{tool_name}' was denied by the user."

    def _policy_refusal(
        self, tool_name: str, tool_args: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """The refusal this call has already earned, or None.

        Checked *before* the confirmation prompt. Asking someone to approve an
        action that is guaranteed to be refused a moment later teaches them to
        approve prompts, and presents a blocked action — ``gh auth token`` — as
        merely destructive rather than not permitted. Validate first, confirm
        second; only a call that could actually run should ever ask.

        Duck-typed like :meth:`_call_is_pre_authorized`: a host that can refuse
        a call up front implements ``policy_refusal_for_call``.
        """
        if not tool_args:
            return None
        refuses = getattr(self, "policy_refusal_for_call", None)
        if not callable(refuses):
            return None
        return refuses(tool_name, tool_args)  # pylint: disable=not-callable

    def _call_is_pre_authorized(
        self, tool_name: str, tool_args: Optional[Dict[str, Any]]
    ) -> bool:
        """True when an explicit, scoped grant already covers this exact call.

        A skill declaring ``shell:execute:gh`` *is* the user's consent for
        read-only ``gh``: narrower than the tool, declared in the skill's front
        matter, and auditable. Asking again per call would make any real use of
        it — a triage is five to ten reads — a wall of modals attended, and a
        guaranteed failure unattended.

        Reads only. The same grant also declares which ``gh`` *writes* may be
        offered to the user, and those answer False here — the grant says a
        write may be asked about, never that it is already approved.

        Duck-typed on purpose. The host that owns a grant implements
        ``skill_grant_covers_call``; an agent with no such mixin has no way to
        answer yes, so its gate stays byte-identical.
        """
        if not tool_args:
            return False
        covers = getattr(self, "skill_grant_covers_call", None)
        if not callable(covers):
            return False
        return bool(covers(tool_name, tool_args))  # pylint: disable=not-callable

    def _tool_requires_confirmation(
        self, tool_name: str, tool_args: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Whether this call must be user-confirmed before it executes.

        Unions two independent sources so tools registered at runtime are
        covered as well as ones named at import time:

        * ``confirmation_required_tools()`` — the static name set, for native
          tools whose names GAIA controls. An individual call inside an active
          skill grant is exempt (:meth:`_call_is_pre_authorized`); the tool is
          not.
        * ``requires_confirmation`` on the registry entry — for dynamically
          registered tools (MCP) whose names are chosen by a third party and so
          can never be enumerated in a static set.

        ``mcp_``-prefixed tools fail closed: an entry that carries no explicit
        flag is treated as requiring confirmation, so a registration path that
        forgets to classify its tools cannot silently leave the gate open.

        Args:
            tool_name: The tool about to run.
            tool_args: Its arguments. Omitting them decides on the name alone —
                what every caller did before grants existed, and still gets.
        """
        if tool_name in self.confirmation_required_tools():
            return not self._call_is_pre_authorized(tool_name, tool_args)
        entry = self._tools_registry.get(tool_name) or {}
        flag = entry.get("requires_confirmation")
        if flag is not None:
            return bool(flag)
        return tool_name.startswith("mcp_")

    def _fold_tool_usage(self, tool_name: str, tool_result: Any) -> None:
        """Record a tool's self-reported LLM usage (see ``_extract_tool_usage``)
        against this turn's running total. Called from the single success path
        inside ``_execute_tool`` so every caller is covered uniformly. Never
        raises — extraction failures are already swallowed by
        ``_extract_tool_usage``; this method only appends.

        A tool whose internal LLM calls already route through ``self.chat``
        would double-count here — a constraint on future tools, not a live one.
        """
        usage = _extract_tool_usage(tool_result)
        if usage is None:
            return
        logger.debug("Tool '%s' reported its own LLM usage: %s", tool_name, usage)
        self._tool_reported_usage.append(usage)

    def _execute_tool_timed(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        """Run :meth:`_execute_tool`, timing it for the turn record.

        Deliberately a separate method the agent loop calls, rather than timing
        inside ``_execute_tool``: that method is copied onto stand-ins by
        attribute assignment (``_execute_tool = Agent._execute_tool``) and is
        read back with ``inspect.getsource`` by the coercion contract test, so
        splitting it breaks both. Timing here also still covers every exit
        ``_execute_tool`` has — refusals, unknown names, declined confirmations
        — so a refused call's latency is never misfiled as agent overhead.
        """
        recorder = getattr(self, "_turn_recorder", None)
        # Only the outermost call is timed. A tool body may call another tool
        # (CodeAgent's orchestration does); timing both would count the inner
        # one's seconds twice and push tool_s past the turn's own total.
        if recorder is None or getattr(self, "_tool_timing_depth", 0):
            return self._execute_tool(tool_name, tool_args)

        started = time.perf_counter()
        # Default False, set only on a clean return: a tool that RAISES must not
        # be recorded ok, and ``_is_error_result(None)`` would say it was.
        ok = False
        self._tool_timing_depth = 1
        self._confirmation_wait_s = 0.0
        try:
            result = self._execute_tool(tool_name, tool_args)
            ok = not self._is_error_result(result)
            return result
        finally:
            self._tool_timing_depth = 0
            waited = getattr(self, "_confirmation_wait_s", 0.0) or 0.0
            try:
                recorder.record_tool(
                    step=getattr(getattr(self, "chat", None), "turn_step", 0),
                    name=tool_name or "<unnamed>",
                    # Human approval is excluded — neither tool nor model cost.
                    # Folding it in made a 1.3s command report as 322.6s.
                    wall_s=max(0.0, time.perf_counter() - started - waited),
                    ok=ok,
                    waited_s=waited,
                )
            except Exception as e:  # noqa: BLE001 - never displace a tool error
                logger.warning("could not record tool timing: %s", e)

    def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Any:
        """
        Execute a tool by name with the provided arguments.

        Args:
            tool_name: Name of the tool to execute
            tool_args: Arguments to pass to the tool

        Returns:
            Result of the tool execution
        """
        if not tool_name:
            logger.error("Tool name is None or empty")
            return {
                "status": "error",
                "error": "Tool name is missing from LLM response",
                "error_displayed": True,
            }

        # Exact name first — skill tools register with a literal hyphen
        # (``rss-digest/fetch_rss``); the normalization below is only a typo rescue.
        tool_name = tool_name.removesuffix("()")
        if tool_name not in self._tools_registry:
            tool_name = tool_name.replace("-", "_")

        logger.debug(f"Executing tool {tool_name} with args: {tool_args}")

        if not tool_name:
            return {"status": "error", "error": "No tool name provided"}

        if tool_name not in self._tools_registry:
            # Try to resolve unprefixed MCP tool names (e.g. "get_current_time"
            # when registry has "mcp_time_get_current_time"). Local LLMs often
            # strip the mcp_<server>_ prefix.
            resolved = self._resolve_tool_name(tool_name)
            if resolved:
                logger.debug(f"Resolved tool '{tool_name}' -> '{resolved}'")
                tool_name = resolved
            else:
                # When the name is a strict prefix of one or more registered
                # tools, hand the model the candidate list so it can retry
                # with the full name. Covers truncated-name emission like
                # the bare server prefix some local models produce.
                lower = tool_name.lower()
                candidates = sorted(
                    n for n in self._tools_registry if n.lower().startswith(lower + "_")
                )[:10]
                # Bare-prefix detection: EVERY candidate begins with the
                # requested name + "_". Tells us the model emitted a strict
                # prefix, not a typo. Don't re-quote the bad name in the
                # error — re-emitting it puts the bad token back in the
                # model's context and reinforces the failure loop.
                is_bare_prefix = len(candidates) >= 2 and all(
                    c.startswith(tool_name + "_") for c in candidates
                )
                if is_bare_prefix:
                    err = (
                        "Incomplete tool name. Choose ONE of these complete "
                        f"names and copy it exactly: {', '.join(candidates)}."
                    )
                elif candidates:
                    err = "Unknown tool name. Use one of: " f"{', '.join(candidates)}."
                else:
                    # Native tool-calling models get schemas via ``tools=`` and
                    # no longer receive an AVAILABLE TOOLS section, so naming it
                    # here would point them at something that isn't there.
                    err = "Unknown tool name. Use only the tools you were given."
                logger.error(err)
                return {"status": "error", "error": err}

        # Validate first, confirm second: a call the guardrails already refuse
        # must never reach a prompt.
        refusal = self._policy_refusal(tool_name, tool_args)
        if refusal is not None:
            return refusal

        # Guardrail: require explicit user confirmation for high-risk tools.
        # Consoles that cannot reach a human deny rather than answer for them
        # (#2210): AgentConsole prompts on a TTY, SSEOutputHandler blocks on the
        # frontend modal, everything else denies with an actionable message.
        if self._tool_requires_confirmation(tool_name, tool_args):
            # Blocking on a human is not tool cost. Timed separately so a turn
            # where approval took five minutes does not report the tool as
            # having taken five minutes.
            _confirm_started = time.perf_counter()
            try:
                approved = self.console.confirm_tool_execution(tool_name, tool_args)
            finally:
                # Accumulates: a tool body that invokes another confirmed tool
                # asks twice, and the outer timer resets this per turn anyway.
                self._confirmation_wait_s = (
                    getattr(self, "_confirmation_wait_s", 0.0) or 0.0
                ) + (time.perf_counter() - _confirm_started)
            if not approved:
                return {
                    "status": "denied",
                    "error": self._confirmation_denied_error(tool_name),
                }

        # Dynamic tool loader (#1449): record use for LRU recency. The name is
        # fully resolved and confirmed in the registry here. Execution stays on
        # the full registry — recording never gates it — so a model that names
        # an unlisted tool still runs it (free non-tool-calling recovery), and
        # the loader logs that as an escape-hatch signal.
        self._on_tool_invoked(tool_name)

        tool = self._tools_registry[tool_name]["function"]
        sig = inspect.signature(tool)

        # Get required parameters (those without defaults)
        # Skip VAR_KEYWORD (**kwargs) and VAR_POSITIONAL (*args) parameters
        required_args = {
            name: param
            for name, param in sig.parameters.items()
            if param.default == inspect.Parameter.empty
            and name != "return"
            and param.kind
            not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
        }

        # Check for missing required arguments
        missing_args = [arg for arg in required_args if arg not in tool_args]
        if missing_args:
            error_msg = (
                f"Missing required arguments for {tool_name}: {', '.join(missing_args)}"
            )
            logger.error(error_msg)
            return {"status": "error", "error": error_msg}

        # Reject arguments the tool does not accept before dispatch. A model that
        # hallucinates a kwarg (e.g. mailbox= on archive_message_batch) would
        # otherwise raise a bare TypeError inside tool(**tool_args); surface a
        # structured, recoverable error naming the accepted parameters instead.
        # Tools declaring **kwargs accept anything, so they opt out.
        accepts_var_keyword = any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in sig.parameters.values()
        )
        if not accepts_var_keyword:
            accepted_args = {
                name
                for name, param in sig.parameters.items()
                if name != "return"
                and param.kind
                not in (
                    inspect.Parameter.VAR_KEYWORD,
                    inspect.Parameter.VAR_POSITIONAL,
                )
            }
            unexpected_args = [arg for arg in tool_args if arg not in accepted_args]
            if unexpected_args:
                error_msg = (
                    f"Unexpected argument(s) for {tool_name}: "
                    f"{', '.join(sorted(unexpected_args))}. "
                    f"Accepted argument(s): {', '.join(sorted(accepted_args)) or 'none'}."
                )
                logger.error(error_msg)
                return {"status": "error", "error": error_msg}

        # Models routinely send numbers as JSON strings ("120" for timeout: int).
        # Every tool body would otherwise have to defend itself, and the ones
        # that don't fail deep inside a library with an unrecognisable message.
        tool_args, coercion_error = self._coerce_tool_args(tool_name, sig, tool_args)
        if coercion_error is not None:
            logger.error(coercion_error)
            return {"status": "error", "error": coercion_error}

        try:
            result = self._call_tool_bounded(tool, tool_args, tool_name)
            logger.debug(f"Tool execution result: {result}")
            self._fold_tool_usage(tool_name, result)
            return result
        except ToolExecutionTimeout as e:
            # Bounded-execution guard fired: the tool body blocked past its
            # limit. Fail loud with an actionable message — name the tool, the
            # window, and the override knob — instead of hanging the agent loop.
            error_msg = (
                f"Tool '{tool_name}' did not return within {e.timeout:g}s and "
                f"was abandoned. The call may be hung (e.g. an unreachable "
                f"service or stuck network request). Check the tool/connector, "
                f"or raise GAIA_AGENT_TOOL_TIMEOUT if it legitimately needs "
                f"longer."
            )
            logger.error(error_msg)
            self.error_history.append(error_msg)
            # The main loop's is_error/STATE_ERROR_RECOVERY handling retries
            # this — not a fatal top-level failure (#2515).
            self.console.print_error(error_msg, recoverable=True)
            return {
                "status": "error",
                "error": error_msg,
                "timeout": True,
                "tool_name": tool_name,
            }
        except subprocess.TimeoutExpired as e:
            # Handle subprocess timeout specifically
            error_msg = f"Tool {tool_name} timed out: {str(e)}"
            logger.error(error_msg)
            self.error_history.append(error_msg)
            return {"status": "error", "error": error_msg, "timeout": True}
        except Exception as e:
            # Format error with full execution trace for debugging
            formatted_error = format_execution_trace(
                exception=e,
                query=getattr(self, "_current_query", None),
                plan_step=self.current_step + 1 if self.current_plan else None,
                total_steps=self.total_plan_steps if self.current_plan else None,
                tool_name=tool_name,
                tool_args=tool_args,
            )
            logger.error(f"Error executing tool {tool_name}: {e}")
            self.error_history.append(str(e))  # Store brief error, not formatted

            # Print to console immediately so user sees it. The caller's
            # is_error/STATE_ERROR_RECOVERY handling retries this — not a
            # fatal top-level failure (#2515).
            self.console.print_error(formatted_error, recoverable=True)

            return {
                "status": "error",
                "error_brief": str(e),  # Brief error message for quick reference
                "error_displayed": True,  # Flag to prevent duplicate display
                "tool_name": tool_name,
                "tool_args": tool_args,
                "plan_step": self.current_step + 1 if self.current_plan else None,
            }

    def _generate_max_steps_message(
        self, conversation: List[Dict], steps_taken: int, steps_limit: int
    ) -> str:
        """Generate informative message when max steps is reached.

        Args:
            conversation: The conversation history
            steps_taken: Number of steps actually taken
            steps_limit: Maximum steps allowed

        Returns:
            Informative message about what was accomplished
        """
        # Analyze what was done
        tool_calls = [
            msg
            for msg in conversation
            if msg.get("role") == "assistant" and "tool_calls" in msg
        ]

        tools_used = []
        for msg in tool_calls:
            for tool_call in msg.get("tool_calls", []):
                if "function" in tool_call:
                    tools_used.append(tool_call["function"]["name"])

        message = f"⚠️ Reached maximum steps limit ({steps_limit} steps)\n\n"
        message += f"Completed {steps_taken} steps using these tools:\n"

        # Count tool usage
        from collections import Counter

        tool_counts = Counter(tools_used)
        for tool, count in tool_counts.most_common(10):
            message += f"  - {tool}: {count}x\n"

        message += "\nTo continue or complete this task:\n"
        message += "1. Review the generated files and progress so far\n"
        message += f"2. Run with --max-steps {steps_limit + 50} to allow more steps\n"
        message += "3. Or complete remaining tasks manually\n"

        return message

    def _write_json_to_file(self, data: Dict[str, Any], filename: str = None) -> str:
        """
        Write JSON data to a file and return the absolute path.

        Args:
            data: Dictionary data to write as JSON
            filename: Optional filename, if None a timestamped name will be generated

        Returns:
            Absolute path to the saved file
        """
        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)

        # Generate filename if not provided
        if not filename:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"agent_output_{timestamp}.json"

        # Ensure filename has .json extension
        if not filename.endswith(".json"):
            filename += ".json"

        # Create absolute path
        file_path = os.path.join(self.output_dir, filename)

        # Write JSON data to file
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        return os.path.abspath(file_path)

    def _truncation_budget(self) -> tuple:
        """(threshold, target) chars a tool result may occupy, for THIS model.

        The local budget is derived from the device profile (NPU 32K / GPU 64K),
        which is right for Lemonade and wrong for anything else. Running on
        Claude the agent was still capping tool results at the NPU's 20,000
        chars — with a 200,000-token window sitting unused — and silently
        dropping list entries it had ample room for. Observed: a 36-skill
        listing came back one short, and the agent said so.
        """
        if getattr(self, "_use_claude", False):
            # Imported here, not at module scope: providers/claude.py guards an
            # optional dependency, and the Lemonade-only path must not pay for it.
            from gaia.llm.providers.claude import (  # pylint: disable=import-outside-toplevel
                CLAUDE_CTX_SIZE,
            )

            return budget_for_ctx(CLAUDE_CTX_SIZE)
        return truncation_budget(self.device)

    #: Scalar annotations worth coercing, by name as well as by type: a module
    #: using postponed annotations hands us the string "int", not ``int``, and
    #: silently skipping those would turn this into a no-op nobody notices.
    #: Anything richer (a dict, a dataclass, an Optional[...]) is left exactly
    #: as the model sent it.
    _COERCIBLE = {"int": int, "float": float, "bool": bool, "str": str}

    @staticmethod
    def _coerce_scalar(value: Any, target: type) -> Any:
        """Return *value* as *target*, or raise ValueError naming the problem."""
        if target is bool:
            if isinstance(value, bool):
                return value
            text = str(value).strip().lower()
            if text in ("true", "yes", "1"):
                return True
            if text in ("false", "no", "0"):
                return False
            raise ValueError(f"{value!r} is not a boolean")
        if target is str:
            return value if isinstance(value, str) else str(value)
        # bool is a subclass of int — coercing True to 1 silently would hide a
        # model that confused a flag for a count.
        if isinstance(value, bool):
            raise ValueError(f"{value!r} is a boolean, not a number")
        if target is int:
            if isinstance(value, int):
                return value
            number = float(str(value).strip())
            if number != int(number):
                raise ValueError(f"{value!r} is not a whole number")
            return int(number)
        if isinstance(value, (int, float)):
            return float(value)
        return float(str(value).strip())

    def _coerce_tool_args(
        self,
        tool_name: str,
        sig: "inspect.Signature",
        tool_args: Dict[str, Any],
    ) -> tuple:
        """Fit model-supplied arguments to each parameter's annotated type.

        Returns ``(args, error)``; ``error`` is None when everything converted.

        A model that sends ``timeout: "120"`` for ``timeout: int = 60`` used to
        reach ``subprocess.run(timeout="120")``, which fails inside the stdlib
        with ``unsupported operand type(s) for +: 'float' and 'str'``. The
        agent read that as a bug in the *script* it was running and reported it
        as such — so the real fault was invisible from the transcript.
        """
        converted = dict(tool_args)
        problems = []
        for name, value in tool_args.items():
            param = sig.parameters.get(name)
            if param is None or param.annotation is inspect.Parameter.empty:
                continue
            annotation = param.annotation
            target = self._COERCIBLE.get(
                annotation.strip() if isinstance(annotation, str) else None
            ) or (annotation if annotation in self._COERCIBLE.values() else None)
            if target is None or value is None:
                continue
            # bool passes isinstance(x, int), so an int parameter handed True
            # would look correct and skip the check that rejects it.
            already_right = isinstance(value, target) and not (
                target is int and isinstance(value, bool)
            )
            if already_right:
                continue
            try:
                converted[name] = self._coerce_scalar(value, target)
            except (TypeError, ValueError) as exc:
                problems.append(f"{name} expects {target.__name__} — {exc}")
        if problems:
            return tool_args, (
                f"Invalid argument(s) for {tool_name}: {'; '.join(problems)}. "
                f"Send each value as its declared type."
            )
        if converted != tool_args:
            logger.debug(
                "[coerce] %s: %s",
                tool_name,
                {
                    k: f"{tool_args[k]!r}->{v!r}"
                    for k, v in converted.items()
                    if tool_args.get(k) != v or type(tool_args.get(k)) is not type(v)
                },
            )
        return converted, None

    def _handle_large_tool_result(
        self,
        tool_name: str,
        tool_result: Any,
        conversation: List[Dict[str, Any]],
        tool_args: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Handle large tool results by truncating them if necessary.

        Args:
            tool_name: Name of the executed tool
            tool_result: The result from tool execution
            conversation: The conversation list to append to
            tool_args: Arguments passed to the tool (optional)

        Returns:
            The truncated result or original if within limits
        """
        truncated_result = tool_result
        if isinstance(tool_result, (dict, list)):
            # Use custom encoder to handle bytes and other non-serializable types.
            # ensure_ascii=False: this text reaches the model as prose, not a
            # wire format re-parsed on the other end -- escaping would hand it
            # literal \uXXXX sequences instead of the actual characters.
            result_str = json.dumps(
                tool_result, default=self._json_serialize_fallback, ensure_ascii=False
            )
            threshold, target = self._truncation_budget()
            if len(result_str) > threshold:
                # Truncate large results to prevent overwhelming the LLM. The
                # result is re-parsed just below, so this path must always
                # come back as valid JSON (#2620).
                truncated_str = self._truncate_large_content(
                    tool_result, max_chars=target, as_json=True
                )
                try:
                    truncated_result = json.loads(truncated_str)
                except json.JSONDecodeError:
                    # If truncated string isn't valid JSON, use it as-is
                    truncated_result = truncated_str
                # Notify user about truncation
                self.console.print_info(
                    f"Note: Large result ({len(result_str)} chars) truncated for LLM context"
                )
                # SilentConsole.print_info is a no-op, so every packaged/sidecar
                # agent needs the real logger to make truncation diagnosable.
                logger.warning(
                    "Tool result for %s truncated: %d chars exceeded the %d-char "
                    "cap for this device profile (target %d)",
                    tool_name,
                    len(result_str),
                    threshold,
                    target,
                )
                if self.debug:
                    print(f"[DEBUG] Tool result truncated from {len(result_str)} chars")

        # Add to conversation
        tool_entry: Dict[str, Any] = {
            "role": "tool",
            "name": tool_name,
            "content": truncated_result,
        }
        if tool_args is not None:
            tool_entry["tool_args"] = tool_args
        conversation.append(tool_entry)
        return truncated_result

    def _progress_label(self) -> str:
        """Name the phase the loop is in, in the user's terms (#2804).

        An unmapped state names the wait rather than inventing a phase — the
        caller must never be handed an empty progress label.
        """
        return self._STATE_PROGRESS_LABELS.get(
            self.execution_state, "Working on your request"
        )

    def _is_loaded_ctx_too_small(self) -> bool:
        """Probe Lemonade's health endpoint to see whether the active LLM is
        loaded with a context size smaller than the active device profile's
        expected window.

        Used when a context-overflow error fires but ``str(exception)`` no
        longer carries the raw ``n_ctx`` value (typical when AgentSDK
        re-raises with the typed exception's friendly user_message).
        Returns False on any probe failure so the caller falls through to
        the safe in-loop trim path rather than crashing.
        """
        try:
            import httpx

            from gaia.llm.lemonade_client import (
                lemonade_auth_headers,
                resolve_lemonade_api_key,
            )
            from gaia.llm.lemonade_manager import LemonadeManager

            base_url = LemonadeManager.get_base_url() or "http://localhost:13305/api/v1"
            # ``api/v0/health`` exposes ``all_models_loaded`` with ctx_size.
            # The base_url already ends in /api/v1; strip the v1 suffix to
            # reach the v0 health endpoint.
            health_url = base_url.replace("/api/v1", "/api/v0/health")
            resp = httpx.get(
                health_url,
                timeout=3.0,
                headers=lemonade_auth_headers(resolve_lemonade_api_key()),
            )
            if resp.status_code != 200:
                return False
            data = resp.json()
            device = self.device
            if device is None:
                # Match the device used by model loading when an agent was
                # constructed without an explicit device (e.g. email).
                from gaia.config import GaiaConfig

                device = GaiaConfig.load().default_device
            expected_ctx = profile_ctx_size(device)
            for m in data.get("all_models_loaded", []):
                if m.get("type") in ("llm", "vlm"):
                    ctx = m.get("recipe_options", {}).get("ctx_size") or 0
                    # Compare against the active profile: a correctly loaded
                    # NPU model is 32K, while GPU/CPU profiles are 64K.
                    if 0 < ctx < expected_ctx:
                        return True
            return False
        except Exception:  # pylint: disable=broad-except
            return False

    def _extract_lemonade_user_message(self, exc: BaseException) -> Optional[str]:
        """Return a typed Lemonade error's ``user_message`` if present in *exc*.

        AgentSDK wraps backend exceptions in generic ``RuntimeError`` /
        ``Exception`` with ``str(original)`` as the message; the typed-class
        info is preserved on ``__cause__`` / ``__context__``. We walk both
        chains and also fall back to substring-matching the stringified
        exception, so callers get a typed actionable message regardless
        of which layer raised.

        Specifically prevents the generic "Sorry, I ran into an unexpected
        problem. This might be a temporary issue — try again in a moment."
        wrapper from clobbering the precise remediation messages on typed
        errors like :class:`LemonadeUpstreamTimeoutError` (#1030) — that
        wrapper actively misleads users on non-retryable failures.

        Returns ``None`` for unrelated exceptions so the caller falls
        through to its normal generic copy.
        """
        try:
            from gaia.llm.providers.lemonade import LemonadeError
            from gaia.ui._chat_helpers import _classify_chat_exception
        except Exception:  # pylint: disable=broad-except
            return None

        # 1. Direct match anywhere in the cause chain.
        cur: Optional[BaseException] = exc
        seen: set = set()
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if isinstance(cur, LemonadeError):
                msg = getattr(cur, "user_message", None)
                if msg:
                    return str(msg)
            cur = cur.__cause__ or cur.__context__

        # 2. String-based reclassification — covers the case where the typed
        # exception was stringified into a generic ``Exception`` by AgentSDK.
        # ``_classify_chat_exception`` already does the timeout-vs-network
        # split we need for #1030.
        classified = _classify_chat_exception(exc)
        if classified is not None:
            msg = getattr(classified, "user_message", None)
            if msg:
                return str(msg)
        return None

    def _shrink_messages_for_overflow(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Aggressively shrink the messages array after a context-overflow.

        Strategy: keep the original user query, keep recent assistant tool_calls
        and the LATEST tool result intact, but replace older tool-result
        contents with a short stub. This preserves the structural shape of
        the conversation (so the model can still reason about what tools have
        been called) while dropping the bulk of the bytes.

        Used by ``process_query``'s LLM-call retry loop when the model
        reports ``exceed_context_size``. Returns a new list — the caller
        must rebind ``messages``.
        """
        if not messages:
            return messages
        first = messages[0]  # user query
        rest = messages[1:]
        # Find indices of tool-result entries
        tool_indices = [i for i, m in enumerate(rest) if m.get("role") == "tool"]
        keep_intact = set(tool_indices[-1:]) if tool_indices else set()
        shrunk_rest: List[Dict[str, Any]] = []
        for i, m in enumerate(rest):
            if m.get("role") == "tool" and i not in keep_intact:
                # Replace bulky tool result with a stub — the model only
                # needs to know SOMETHING was returned at this point.
                shrunk_rest.append(
                    {
                        "role": "tool",
                        "name": m.get("name", "unknown"),
                        "tool_call_id": m.get("tool_call_id", "stub"),
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "[tool result omitted — context "
                                    "overflow recovery; see latest result]"
                                ),
                            }
                        ],
                    }
                )
            elif (
                m.get("role") == "assistant"
                and isinstance(m.get("content"), str)
                and len(m.get("content", "")) > 800
            ):
                # Truncate verbose assistant chain-of-thought too.
                shrunk_rest.append(
                    {
                        "role": "assistant",
                        "content": m["content"][:800] + "... (truncated)",
                    }
                )
            else:
                shrunk_rest.append(m)
        return [first] + shrunk_rest

    def _create_tool_message(
        self,
        tool_name: str,
        tool_output: Any,
        tool_call_id: str | None = None,
    ) -> Dict[str, Any]:
        """
        Build a message structure representing a tool output for downstream LLM calls.

        Args:
            tool_name: The name of the tool that produced the output.
            tool_output: The raw tool output (str / dict / list / etc).
            tool_call_id: Optional id from the originating ``tool_calls`` array.
                When provided, the tool message references the model's
                actual call id so OpenAI-spec consumers can correlate
                results to calls — this matters for parallel tool_calls
                (issue #944) where multiple results need to be matched to
                multiple calls in the prior assistant turn. When omitted,
                a fresh uuid is synthesised for backward compatibility
                with embedded-JSON paths that don't carry an id.
        """
        if isinstance(tool_output, str):
            text_content = tool_output
        else:
            # Every call site hands this a result ``_handle_large_tool_result``
            # already fitted to the device budget, so this is a backstop, not
            # the real gate -- it must not be tighter than the gate it backs.
            _, target = self._truncation_budget()
            # Prose call site: text_content is spliced into a message's text
            # field, never json.loads'd -- stays on the default prose path,
            # not the JSON-safe envelope (#2620, reflection C2).
            text_content = self._truncate_large_content(tool_output, max_chars=target)

        if not isinstance(text_content, str):
            text_content = json.dumps(
                tool_output, default=self._json_serialize_fallback, ensure_ascii=False
            )

        msg = {
            "role": "tool",
            "name": tool_name,
            "tool_call_id": tool_call_id or uuid.uuid4().hex,
            "content": [{"type": "text", "text": text_content}],
        }
        if getattr(self, "_single_tool_done", False):
            for block in msg["content"]:
                if block.get("type") == "text":
                    block["text"] += _SINGLE_TOOL_DONE_SUFFIX
                    break
        return msg

    def _build_assistant_message(
        self, raw_response: str, parsed: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Construct the assistant message to append to the LLM context.

        For native ``tool_calls`` responses (issue #944) we MUST emit a
        proper OpenAI-shape assistant turn — ``content`` (string or
        null) plus ``tool_calls`` carrying the original ids — so that
        the subsequent ``role=tool`` messages can correlate by
        ``tool_call_id``. Stuffing the raw ``{"__tool_calls__": ...}``
        sentinel envelope into ``content`` (the pre-fix behaviour) is
        rejected by spec-strict providers and breaks parallel-call
        result-to-call matching.

        For embedded-JSON / plain-text responses we keep passing the raw
        response text through unchanged.
        """
        tc_list = parsed.get("tool_calls")
        if not tc_list:
            return {"role": "assistant", "content": raw_response}
        return {
            "role": "assistant",
            "content": parsed.get("content"),
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(
                            tc["tool_args"],
                            default=self._json_serialize_fallback,
                            ensure_ascii=False,
                        ),
                    },
                }
                for tc in tc_list
            ],
        }

    def _json_serialize_fallback(self, obj: Any) -> Any:
        """
        Fallback serializer for JSON encoding non-standard types.

        Handles bytes, datetime, and other common non-serializable types.
        """
        try:
            import numpy as np  # Local import to avoid hard dependency at module import time

            if isinstance(obj, np.generic):
                return obj.item()
            if isinstance(obj, np.ndarray):
                return obj.tolist()
        except Exception:
            pass

        if isinstance(obj, bytes):
            # For binary data, return a placeholder (don't expose raw bytes to LLM)
            return f"<binary data: {len(obj)} bytes>"
        if hasattr(obj, "isoformat"):
            # Handle datetime objects
            return obj.isoformat()
        if hasattr(obj, "__dict__"):
            # Handle objects with __dict__
            return obj.__dict__

        for caster in (float, int, str):
            try:
                return caster(obj)
            except Exception:
                continue

        return "<non-serializable>"

    def _truncate_large_content(
        self, content: Any, max_chars: int = 20000, as_json: bool = False
    ) -> str:
        """
        Truncate large content to prevent overwhelming the LLM.

        Defaults to 20000 chars, appropriate for the NPU's 32K token context
        window (``_handle_large_tool_result`` scales this per device profile
        via ``truncation_budget``). Whole list items are dropped from the end
        rather than slicing the serialized text, so the result never cuts
        through the middle of a record (#2620).

        Set ``as_json=True`` when the caller will ``json.loads`` the result
        (today, only ``_handle_large_tool_result``) -- its last-resort
        fallback then wraps the sliced text in a JSON envelope so the output
        always parses. Prose callers (spliced directly into an LLM prompt
        string, e.g. ``_create_tool_message``) get the plain slice they
        always got: wrapping prose in an escaped JSON blob would waste an
        already-tight budget on punctuation the reader never needed
        (reflection C2).
        """

        # If we have test_results in the output we don't want to
        # truncate as this can contain important information on
        # how to fix the tests
        if isinstance(content, dict) and (
            "test_results" in content or "run_tests" in content
        ):
            return json.dumps(
                content, default=self._json_serialize_fallback, ensure_ascii=False
            )

        if not isinstance(content, (dict, list)):
            content_str = str(content)
            if len(content_str) <= max_chars:
                return content_str
            return self._truncate_fallback_text(content_str, max_chars, as_json)

        # ensure_ascii=False throughout this method: every returned string
        # here is prose the model reads directly, not a wire format the
        # caller re-parses -- escaping would hand it literal \uXXXX text.
        compact_str = json.dumps(
            content, default=self._json_serialize_fallback, ensure_ascii=False
        )
        if len(compact_str) <= max_chars:
            return compact_str

        # For responses with chunks (e.g., search results, document retrieval)
        if (
            isinstance(content, dict)
            and "chunks" in content
            and isinstance(content["chunks"], list)
        ):
            truncated = content.copy()

            # Keep all chunks but truncate individual chunk content if needed
            if "chunks" in truncated:
                for chunk in truncated["chunks"]:
                    if isinstance(chunk, dict) and "content" in chunk:
                        # Keep full content for chunks (they're the actual data)
                        # Only truncate if a single chunk is massive
                        if len(chunk["content"]) > CHUNK_TRUNCATION_THRESHOLD:
                            chunk["content"] = (
                                chunk["content"][:CHUNK_TRUNCATION_SIZE]
                                + "\n...[chunk truncated]...\n"
                                + chunk["content"][-CHUNK_TRUNCATION_SIZE:]
                            )

            result_str = json.dumps(
                truncated,
                indent=2,
                default=self._json_serialize_fallback,
                ensure_ascii=False,
            )
            # Use larger limit for chunked responses since chunks are the actual data
            if len(result_str) <= max_chars * 3:  # Allow up to 60KB for chunked data
                return result_str
            # If still too large, keep first 3 chunks only
            truncated["chunks"] = truncated["chunks"][:3]
            return json.dumps(
                truncated,
                indent=2,
                default=self._json_serialize_fallback,
                ensure_ascii=False,
            )

        # Dict/list content (Jira "issues", email "messages"/"awaiting_reply",
        # a bare list, ...): drop whole trailing items -- never slice inside
        # one -- so the result always re-serializes cleanly (#2620).
        dropped = self._drop_items_to_fit(content, max_chars)
        if dropped is not None:
            return dropped

        # Last resort: no list to trim (a scalar-only dict), or the single
        # surviving item still doesn't fit -- see _drop_items_to_fit.
        return self._truncate_fallback_text(compact_str, max_chars, as_json)

    def _truncate_fallback_text(self, text: str, max_chars: int, as_json: bool) -> str:
        """Last-resort slice when there is no whole item left to drop.

        ``as_json`` callers get a JSON envelope (``json.dumps`` escapes the
        slice, so the result always parses); prose callers get the plain
        mid-slice they always got (#2620, reflection C2).
        """
        if as_json:
            envelope_overhead = len(
                json.dumps(
                    {"truncated": True, "original_chars": len(text), "content": ""},
                    default=self._json_serialize_fallback,
                    ensure_ascii=False,
                )
            )
            budget = max(0, max_chars - envelope_overhead)
            return json.dumps(
                {
                    "truncated": True,
                    "original_chars": len(text),
                    "content": text[:budget],
                },
                default=self._json_serialize_fallback,
                ensure_ascii=False,
            )

        half = max_chars // 2 - 20
        return f"{text[:half]}\n...[truncated]...\n{text[-half:]}"

    def _drop_items_to_fit(self, content: Any, max_chars: int) -> Optional[str]:
        """Fit ``content`` under ``max_chars`` by dropping whole items from
        the end of its largest list member, re-serializing through
        ``json.dumps`` on every attempt so the result is always valid JSON
        (#2620). Handles a bare list, or a dict with one or more
        list-valued fields (trimming whichever field currently holds the
        most items first, repeating across fields as needed -- reflection
        A2).

        Returns ``None`` when there is no list to trim, or when even the
        single item surviving across every field still exceeds
        ``max_chars`` -- the caller then falls back to a text slice rather
        than silently discarding that last item's content (reflection C3).
        """
        is_root_list = isinstance(content, list)
        if is_root_list:
            lists: Dict[str, List[Any]] = {"__root__": list(content)}
            base: Dict[str, Any] = {}
        else:
            lists = {
                key: list(value)
                for key, value in content.items()
                if isinstance(value, list) and value
            }
            base = {key: value for key, value in content.items() if key not in lists}

        if not lists:
            return None

        totals = {key: len(items) for key, items in lists.items()}

        def render() -> str:
            dropped_any = any(len(items) < totals[key] for key, items in lists.items())
            if is_root_list:
                payload: Any = list(lists["__root__"])
                if dropped_any:
                    payload.append(
                        {
                            "truncated": True,
                            "returned": len(lists["__root__"]),
                            "total": totals["__root__"],
                        }
                    )
            else:
                payload = {**base, **lists}
                if dropped_any:
                    payload["truncated"] = True
                    payload["truncated_fields"] = {
                        key: {"returned": len(items), "total": totals[key]}
                        for key, items in lists.items()
                        if len(items) < totals[key]
                    }
            return json.dumps(
                payload, default=self._json_serialize_fallback, ensure_ascii=False
            )

        result = render()
        while len(result) > max_chars:
            candidates = [key for key, items in lists.items() if items]
            if not candidates:
                return None
            if sum(len(items) for items in lists.values()) <= 1:
                # The last surviving item alone doesn't fit even with the
                # marker overhead. Dropping it would silently return an
                # empty shell; hand off to the text-slice fallback instead,
                # which preserves a sliced fragment of it (reflection C3).
                return None
            largest = max(candidates, key=lambda key: len(lists[key]))
            lists[largest].pop()
            result = render()

        return result

    def _namespaced_agent_id(self) -> Optional[str]:
        """Return the registry-assigned namespaced agent id, or None.

        The registry wraps each factory with ``_wrap_factory_with_namespaced_id``
        so production agent instances always carry ``_gaia_namespaced_agent_id``.
        Tests that construct agents directly (without going through the registry)
        fall back to bare ``AGENT_ID``; both keys may legitimately resolve to
        ``None`` for ad-hoc agents that opt out of the activation/grant layer
        entirely.
        """
        return getattr(self, "_gaia_namespaced_agent_id", None) or getattr(
            self, "AGENT_ID", None
        )

    # ------------------------------------------------------------------
    # Proactive lifecycle hooks (#1484)
    # ------------------------------------------------------------------

    def on_first_run(  # pylint: disable=unused-argument
        self, context: Optional[Dict[str, Any]]
    ) -> List["Proposal"]:
        """First-run discovery: propose actions based on initial context.

        Default: no-op, returns empty list. Override to implement proactive
        discovery behavior.
        """
        return []

    def on_heartbeat(  # pylint: disable=unused-argument
        self, context: Optional[Dict[str, Any]]
    ) -> List["Proposal"]:
        """Steady-state autonomous loop: propose actions based on current context.

        Default: no-op, returns empty list. Override to implement heartbeat
        behavior. Must act within trust bounds and never exceed permissions.
        """
        return []

    def propose(
        self,
        proposal: "Proposal",
    ) -> Optional["Goal"]:
        """Submit a proactive proposal for user approval.

        Delegates to ``GoalStore.propose()``. Every proposal goes into
        ``pending_approval`` and must be explicitly accepted by the user
        before execution, regardless of its risk tier. DB errors propagate
        from ``GoalStore.propose()``.
        """
        from gaia.agents.base.goal_store import GoalStore

        store = GoalStore()
        return store.propose(proposal)

    def _agent_identity_context(self, ns_id: Optional[str]):
        """Context manager that binds _agent_context for grant resolution.

        Shared identity binding so that both process_query and
        on_heartbeat can resolve per-agent grants via contextvars.
        """
        # `_agent_context` is intentionally PRIVATE (issue #915): imported via
        # the private path so a malicious tool body can't forge an agent
        # identity through the public gaia.connectors API.
        from gaia.connectors.context import _agent_context

        return _agent_context(ns_id) if ns_id else None

    def _active_mcp_servers(self, manager) -> List[str]:
        """Return MCP server names whose tools should be visible to this agent.

        Per issue #1005, MCP tools are gated by the activations ledger
        (``~/.gaia/connectors/activations.json``). When no namespaced agent
        id is available (e.g. test agents constructed directly), every
        connected server is returned — the activation filter only applies
        to agents that participate in the registry's identity scheme.
        """
        if manager is None:
            return []
        ns_id = self._namespaced_agent_id()
        if ns_id is None:
            return list(manager.list_servers())
        return list(manager.servers_for_agent(ns_id))

    def _console_cancelled(self) -> bool:
        """Cooperative mid-generation cancel check for the Agent-UI path.

        The Agent UI injects its ``SSEOutputHandler`` as ``self.console`` and
        sets ``console.cancelled`` (a ``threading.Event``) when the user hits
        Stop. Non-UI consoles (``AgentConsole`` / ``SilentConsole``) have no
        such attribute, so this stays a no-op for them — keeping non-UI agent
        usage unaffected. Read per streamed token so a single-shot generation
        (no step boundaries) can be aborted promptly.
        """
        cancelled = getattr(self.console, "cancelled", None)
        return cancelled is not None and cancelled.is_set()

    def finalize_answer(self, answer: str, _conversation: Any) -> str:
        """Last chance to correct the final answer, BEFORE it is emitted.

        Runs ahead of ``console.print_final_answer``, so a subclass's
        correction reaches every consumer — the SSE ``answer`` event the TUI
        renders, the CLI console, and ``process_query``'s return value — rather
        than only the callers that re-read the return dict (#2789).

        Identity by default; override to post-process.
        """
        return answer

    def process_query(
        self,
        user_input: str,
        max_steps: int = None,
        trace: bool = False,
        filename: str = None,
    ) -> Dict[str, Any]:
        """
        Process a user query and execute the necessary tools.
        Displays each step as it's being generated in real-time.

        Args:
            user_input: User's query or request
            max_steps: Maximum number of steps to take in the conversation (overrides class default if provided)
            trace: If True, write detailed JSON trace to file
            filename: Optional filename for trace output, if None a timestamped name will be generated

        Returns:
            Dict containing the final result and operation details
        """
        ns_id = self._namespaced_agent_id()
        identity_ctx = self._agent_identity_context(ns_id)
        try:
            if identity_ctx is None:
                return self._process_query_impl(user_input, max_steps, trace, filename)
            with identity_ctx:
                return self._process_query_impl(user_input, max_steps, trace, filename)
        finally:
            # The impl re-raises on purpose (the wrong-ctx reload its caller
            # retries). A recorder left attached would fold the next turn's
            # calls into this one. Idempotent when the turn already sealed.
            self._finish_turn_record("", 0)

    def _process_query_impl(
        self,
        user_input: str,
        max_steps: int = None,
        trace: bool = False,
        filename: str = None,
    ) -> Dict[str, Any]:
        """Inner implementation of ``process_query`` — see public method docstring."""
        start_time = time.time()  # Track query processing start time

        # Store query for error context (used in _execute_tool for error formatting)
        self._current_query = user_input
        self._single_tool_done = False

        # Proactive skill discovery: a skill the user never named can become
        # loaded here, registering its tools — so it must run BEFORE the tool
        # filter, or those tools are invisible on the very turn that loaded the
        # skill, and before the body filter for the same reason.
        self._discover_skills_for_turn(user_input)

        # Dynamic tool selection (#1449): pick this turn's tool subset and
        # recompute the cached system prompt only when it changes.
        self._refresh_active_tool_filter(user_input)

        # Lazy skill-body activation (#2848 follow-up): same per-turn timing
        # as the tool filter above, so a stale skill match never survives
        # into a turn that no longer needs it.
        self._refresh_active_skill_filter(user_input)

        logger.debug(f"Processing query: {user_input}")
        conversation = []
        # Build messages array for chat completions
        messages = []

        # Per-turn performance record (dev mode; no-op unless GAIA_TURN_LOG is
        # set). Built here so it spans the whole turn — the total it reports is
        # submit-to-answer, which is what the user actually waits through.
        self._turn_recorder = self._begin_turn_record(user_input)
        # Set by whichever exit seals the record; read by the tail below.
        turn_record: Optional[Dict[str, Any]] = None

        # Prepopulate with conversation history if available (for session persistence)
        if hasattr(self, "conversation_history") and self.conversation_history:
            messages.extend(self.conversation_history)
            logger.debug(
                f"Loaded {len(self.conversation_history)} messages from conversation history"
            )

        steps_taken = 0
        final_answer = None
        # Set when the Agent-UI Stop is observed mid-generation (per-token) so
        # the turn ends with empty text instead of a completed answer (#2157).
        cancelled_by_console = False
        error_count = 0
        tool_call_history = []  # Track recent tool calls to detect loops (last 5 calls)
        tool_call_log = (
            []
        )  # Full unbounded log of all tool calls this turn (for workflow guards)
        # Issue #1023: track the latest outcome of any capability tool
        # (currently ``generate_image``) so the verbose-failure override
        # downstream fires only when the tool actually errored.  ``None``
        # = not called yet, ``True`` = last call succeeded, ``False`` =
        # last call returned an error.
        capability_tool_last_succeeded: Optional[bool] = None
        query_result_cache: dict[str, int] = (
            {}
        )  # result_hash → call count (result-based dedup)
        mutation_call_cache: dict[str, int] = (
            {}
        )  # (tool, normalized args) → call count (input-based dedup, #1317)
        last_error = None  # Track the last error to handle it properly
        previous_outputs = []  # Track previous tool outputs (truncated for context)
        step_results = []  # Track full tool results for parameter substitution

        # Reset state management
        self.execution_state = self.STATE_PLANNING
        self.current_plan = None
        self.current_step = 0
        self.total_plan_steps = 0
        self.plan_iterations = 0  # Reset plan iteration counter
        # Tool-reported LLM usage this turn (see _fold_tool_usage / #2899) —
        # reset per-turn since an Agent instance persists across queries in
        # an interactive session.
        self._tool_reported_usage: List[Dict[str, Any]] = []

        # Add user query to the conversation history
        conversation.append({"role": "user", "content": user_input})
        messages.append({"role": "user", "content": user_input})

        # Use provided max_steps or fall back to class default
        steps_limit = max_steps if max_steps is not None else self.max_steps

        # Print initial message with max steps info
        self.console.print_processing_start(user_input, steps_limit, self.model_id)
        logger.debug(f"Using max_steps: {steps_limit}")

        prompt = f"User request: {user_input}\n\n"

        # Only add planning reminder in PLANNING state
        if self.execution_state == self.STATE_PLANNING:
            prompt += (
                "IMPORTANT: ALWAYS BEGIN WITH A PLAN before executing any tools.\n"
                "First create a detailed plan with all necessary steps, then execute the first step.\n"
                "When creating a plan with multiple steps:\n"
                "   1. ALWAYS follow the plan in the correct order, starting with the FIRST step.\n"
                "   2. Include both a plan and a 'tool' field, the 'tool' field MUST match the tool in the first step of the plan.\n"
                "   3. Create plans with clear, executable steps that include both the tool name and the exact arguments for each step.\n"
            )

        logger.debug(f"Input prompt: {prompt[:200]}...")

        # Process the query in steps, allowing for multiple tool usages
        while steps_taken < steps_limit and final_answer is None:
            # Cooperative cancellation: if a consumer (e.g. the Agent UI's
            # stream-timeout/disconnect cleanup) signalled cancel, stop here so
            # the producer thread is torn down rather than left running. Checked
            # at the step boundary; per-tool timeouts keep each step bounded so
            # this point is always reached in finite time.
            cancel_event = getattr(self, "_cancel_event", None)
            if cancel_event is not None and cancel_event.is_set():
                logger.warning(
                    "Agent run cancelled at step %d/%d via cancel_event",
                    steps_taken,
                    steps_limit,
                )
                final_answer = (
                    "The request was stopped because it exceeded the allowed "
                    "time before completing. Try a simpler request or break it "
                    "into smaller steps."
                )
                break

            # Build the next prompt based on current state (this is for fallback mode only)
            # In chat mode, we'll just add to messages array
            steps_taken += 1
            logger.debug(f"Step {steps_taken}/{steps_limit}")
            if self._turn_recorder is not None and self.chat is not None:
                self.chat.turn_step = steps_taken

            # Display current step
            self.console.print_step_header(steps_taken, steps_limit)

            # Skip automatic finalization for single-step plans - always request proper final answer

            # If we're executing a plan, we might not need to query the LLM again
            if (
                self.execution_state == self.STATE_EXECUTING_PLAN
                and self.current_step < self.total_plan_steps
            ):
                logger.debug(
                    f"Executing plan step {self.current_step + 1}/{self.total_plan_steps}"
                )
                self.console.print_state_info(
                    f"EXECUTING PLAN: Step {self.current_step + 1}/{self.total_plan_steps}"
                )

                # Display the current plan with the current step highlighted
                if self.current_plan:
                    self.console.print_plan(self.current_plan, self.current_step)

                # Extract next step from plan
                next_step = self.current_plan[self.current_step]

                if (
                    isinstance(next_step, dict)
                    and next_step.get("tool")
                    and "tool_args" in next_step
                ):
                    # We have a properly formatted step with tool and args
                    tool_name = next_step["tool"]
                    tool_args = next_step["tool_args"]

                    # Resolve dynamic parameters from previous step results
                    tool_args = self._resolve_plan_parameters(tool_args, step_results)

                    # Create a parsed response structure as if it came from the LLM
                    parsed = {
                        "thought": f"Executing step {self.current_step + 1} of the plan",
                        "goal": f"Following the plan to {user_input}",
                        "tool": tool_name,
                        "tool_args": tool_args,
                    }

                    # Add to conversation
                    conversation.append({"role": "assistant", "content": parsed})

                    # Display the agent's reasoning for the step
                    self.console.print_thought(
                        parsed.get("thought", "Executing plan step")
                    )
                    self.console.print_goal(parsed.get("goal", "Following the plan"))

                    # Display the tool call in real-time
                    self.console.print_tool_usage(tool_name)

                    # Start progress indicator for tool execution
                    self.console.start_progress(f"Executing {tool_name}")

                    # Execute the tool
                    tool_result = self._execute_tool_timed(tool_name, tool_args)

                    # Stop progress indicator
                    self.console.stop_progress()

                    # Issue #1023: record success/failure of capability tools
                    # so the verbose-failure override downstream can fire
                    # only when the tool actually errored.  ``.lower()``
                    # mirrors the defensive check at
                    # ``has_tried_capability_tool`` so a model that emits
                    # ``Generate_Image`` doesn't slip past the tracker.
                    if any(
                        tool_name.lower().startswith(_s) for _s in _SD_CAPABILITY_TOOLS
                    ):
                        capability_tool_last_succeeded = not (
                            isinstance(tool_result, dict)
                            and tool_result.get("status") in ("error", "denied")
                        )

                    # Handle domain-specific post-processing.
                    # A returned plan switches the agent into
                    # STATE_EXECUTING_PLAN for declarative multi-step
                    # recovery (e.g., prereq-enable + retry). The base
                    # impl returns None, but the hook's annotation is
                    # Optional[List[...]], so pylint's None-inference
                    # is wrong here — silence it explicitly.
                    # pylint: disable-next=assignment-from-none
                    _next_plan = self._post_process_tool_result(
                        tool_name, tool_args, tool_result
                    )
                    if _next_plan is not None:
                        self._inject_recovery_plan(_next_plan)

                    # Handle large tool results
                    truncated_result = self._handle_large_tool_result(
                        tool_name, tool_result, conversation, tool_args
                    )

                    # Display the tool result in real-time (show full result to user).
                    # Emit result BEFORE complete so SSE latency is captured.
                    self.console.pretty_print_json(tool_result, "Tool Result")
                    self.console.print_tool_complete()

                    # Store the truncated output for future context
                    previous_outputs.append(
                        {
                            "tool": tool_name,
                            "args": tool_args,
                            "result": truncated_result,
                        }
                    )

                    # Store full result for parameter substitution in subsequent plan steps
                    step_results.append(tool_result)

                    # Share tool output with subsequent LLM calls
                    messages.append(
                        self._create_tool_message(tool_name, truncated_result)
                    )

                    # Check for error (support multiple error formats)
                    is_error = isinstance(tool_result, dict) and (
                        tool_result.get("status") == "error"  # Standard format
                        or tool_result.get("success")
                        is False  # Tools returning success: false
                        or tool_result.get("has_errors") is True  # CLI tools
                        or tool_result.get("return_code", 0) != 0  # Build failures
                    )

                    if is_error:
                        error_count += 1
                        # Extract error message from various formats
                        # Prefer error_brief for logging (avoids duplicate formatted output)
                        last_error = (
                            tool_result.get("error_brief")
                            or tool_result.get("error")
                            or tool_result.get("stderr")
                            or tool_result.get("hint")  # Many tools provide hints
                            or tool_result.get(
                                "suggested_fix"
                            )  # Some tools provide fix suggestions
                            or f"Command failed with return code {tool_result.get('return_code')}"
                        )
                        logger.warning(
                            f"Tool execution error in plan (count: {error_count}): {last_error}"
                        )
                        # Only print if error wasn't already displayed by _execute_tool
                        if not tool_result.get("error_displayed"):
                            # STATE_ERROR_RECOVERY below retries this — not a
                            # fatal top-level failure (#2515).
                            self.console.print_error(last_error, recoverable=True)

                        # Switch to error recovery state
                        self.execution_state = self.STATE_ERROR_RECOVERY
                        self.console.print_state_info(
                            "ERROR RECOVERY: Handling tool execution failure"
                        )

                        # Break out of plan execution to trigger error recovery prompt
                        continue
                    else:
                        # Success - move to next step in plan
                        self.current_step += 1

                        # Check if we've completed the plan
                        if self.current_step >= self.total_plan_steps:
                            logger.debug("Plan execution completed")
                            self.execution_state = self.STATE_COMPLETION
                            self.console.print_state_info(
                                "COMPLETION: Plan fully executed"
                            )

                            # Increment plan iteration counter
                            self.plan_iterations += 1
                            logger.debug(
                                f"Plan iteration {self.plan_iterations} completed"
                            )

                            # Check if we've reached max plan iterations
                            reached_max_iterations = (
                                self.max_plan_iterations > 0
                                and self.plan_iterations >= self.max_plan_iterations
                            )

                            # Prepare message for final answer with the completed plan context
                            plan_context = {
                                "completed_plan": self.current_plan,
                                "total_steps": self.total_plan_steps,
                            }
                            plan_context_raw = json.dumps(
                                plan_context,
                                default=self._json_serialize_fallback,
                                ensure_ascii=False,
                            )
                            if len(plan_context_raw) > 20000:
                                # Prose call site: spliced into an f-string
                                # prompt below, never json.loads'd (#2620,
                                # reflection C2).
                                plan_context_str = self._truncate_large_content(
                                    plan_context, max_chars=20000
                                )
                            else:
                                plan_context_str = plan_context_raw

                            if reached_max_iterations:
                                # Force final answer after max iterations
                                completion_message = (
                                    f"Maximum plan iterations ({self.max_plan_iterations}) reached for task: {user_input}\n"
                                    f"Task: {user_input}\n"
                                    f"Plan information:\n{plan_context_str}\n\n"
                                    f"IMPORTANT: You MUST now provide a final answer with an honest assessment:\n"
                                    f"- Summarize what was successfully accomplished\n"
                                    f"- Clearly state if anything remains incomplete or if errors occurred\n"
                                    f"- If the task is fully complete, state that clearly\n\n"
                                    f'Provide {{"thought": "...", "goal": "...", "answer": "..."}}'
                                )
                            else:
                                completion_message = (
                                    "You have successfully completed all steps in the plan.\n"
                                    f"Task: {user_input}\n"
                                    f"Plan information:\n{plan_context_str}\n\n"
                                    f"Plan iteration: {self.plan_iterations}/{self.max_plan_iterations if self.max_plan_iterations > 0 else 'unlimited'}\n"
                                    "Check if more work is needed:\n"
                                    "- If the task is complete and verified, provide a final answer\n"
                                    "- If critical validation/testing is needed, you may create ONE more plan\n"
                                    "- Only create additional plans if absolutely necessary\n\n"
                                    'If more work needed: Provide a NEW plan with {{"thought": "...", "goal": "...", "plan": [...]}}\n'
                                    'If everything is complete: Provide {{"thought": "...", "goal": "...", "answer": "..."}}'
                                )

                            # Debug logging - only show if truncation happened
                            if self.debug and len(plan_context_raw) > 2000:
                                print(
                                    "\n[DEBUG] Plan context truncated for completion message"
                                )

                            # Add completion request to messages
                            messages.append(
                                {"role": "user", "content": completion_message}
                            )

                            # Send the completion prompt to get final answer
                            self.console.print_state_info(
                                "COMPLETION: Requesting final answer"
                            )

                            # Continue to next iteration to get final answer
                            continue
                        else:
                            # Continue with next step - no need to query LLM again
                            continue
                else:
                    # Plan step doesn't have proper format, fall back to LLM
                    logger.warning(
                        f"Plan step {self.current_step + 1} doesn't have proper format: {next_step}"
                    )
                    self.console.print_warning(
                        f"Plan step {self.current_step + 1} format incorrect, asking LLM for guidance"
                    )
                    prompt = (
                        f"You are following a plan but step {self.current_step + 1} doesn't have proper format: {next_step}\n"
                        "Please interpret this step and decide what tool to use next.\n\n"
                        f"Task: {user_input}\n\n"
                    )
            else:
                # Normal execution flow - query the LLM
                if self.execution_state == self.STATE_DIRECT_EXECUTION:
                    self.console.print_state_info("DIRECT EXECUTION: Analyzing task")
                elif self.execution_state == self.STATE_PLANNING:
                    self.console.print_state_info("PLANNING: Creating or refining plan")
                elif self.execution_state == self.STATE_ERROR_RECOVERY:
                    self.console.print_state_info(
                        "ERROR RECOVERY: Handling previous error"
                    )

                    # Truncate previous outputs if too large to avoid overwhelming the LLM.
                    # Prose call site: spliced into the recovery prompt below,
                    # never json.loads'd (#2620, reflection C2).
                    truncated_outputs = (
                        self._truncate_large_content(previous_outputs, max_chars=500)
                        if previous_outputs
                        else "None"
                    )

                    # Create a specific error recovery prompt
                    last_tool = (
                        tool_call_history[-1][0]
                        if tool_call_history
                        else "unknown tool"
                    )
                    prompt = (
                        "TOOL EXECUTION FAILED!\n\n"
                        f"You were trying to execute: {last_tool}\n"
                        f"Error: {last_error}\n\n"
                        f"Original task: {user_input}\n\n"
                        f"Current plan step {self.current_step + 1}/{self.total_plan_steps} failed.\n"
                        f"Current plan: {self.current_plan}\n\n"
                        f"Previous successful outputs: {truncated_outputs}\n\n"
                        "INSTRUCTIONS:\n"
                        "1. Analyze the error and understand what went wrong\n"
                        "2. Create a NEW corrected plan that fixes the error\n"
                        "3. Make sure to use correct tool parameters (check the available tools)\n"
                        "4. Start executing the corrected plan\n\n"
                        "Respond with your analysis, a corrected plan, and the first tool to execute."
                    )

                    # Add the error recovery prompt to the messages array so it gets sent to LLM
                    messages.append({"role": "user", "content": prompt})

                    # Reset state to planning after creating recovery prompt
                    self.execution_state = self.STATE_PLANNING
                    self.current_plan = None
                    self.current_step = 0
                    self.total_plan_steps = 0
                    step_results.clear()  # Clear stale results from failed plan

                elif self.execution_state == self.STATE_COMPLETION:
                    self.console.print_state_info("COMPLETION: Finalizing response")

            # Print the prompt if show_prompts is enabled (separate from debug_prompts)
            if self.show_prompts:
                # Build context from system prompt and messages
                context_parts = [
                    (
                        f"SYSTEM: {self.system_prompt[:200]}..."
                        if len(self.system_prompt) > 200
                        else f"SYSTEM: {self.system_prompt}"
                    )
                ]

                for msg in messages:
                    role = msg.get("role", "user").upper()
                    content = str(msg.get("content", ""))[:150]
                    context_parts.append(
                        f"{role}: {content}{'...' if len(str(msg.get('content', ''))) > 150 else ''}"
                    )

                if not messages and prompt:
                    context_parts.append(
                        f"USER: {prompt[:150]}{'...' if len(prompt) > 150 else ''}"
                    )

                self.console.print_prompt("\n".join(context_parts), "LLM Context")

            # Handle streaming or non-streaming LLM response
            # Initialize response_stats so it's always in scope
            response_stats = None

            if self.streaming:
                # Streaming mode - raw response will be streamed
                # (SilentConsole will suppress this, AgentConsole will show it)

                # Add prompt to conversation if debug is enabled
                if self.debug_prompts:
                    conversation.append(
                        {"role": "system", "content": {"prompt": prompt}}
                    )
                    # Print the prompt if show_prompts is enabled
                    if self.show_prompts:
                        self.console.print_prompt(
                            prompt, f"Prompt (Step {steps_taken})"
                        )

                # Get streaming response. Same context-overflow retry-on-trim
                # behaviour as the non-streaming branch below — needed because
                # multi-step ReAct loops accumulate tool results in `messages`.
                _retried_after_trim_stream = False
                while True:
                    try:
                        response_stream = self.chat.send_messages_stream(
                            messages=messages,
                            system_prompt=self.system_prompt,
                            tools=self._openai_tools,
                        )

                        # Process the streaming response chunks as they arrive
                        full_response = ""
                        for chunk_response in response_stream:
                            # Cooperative cancel: the Agent UI's Stop sets
                            # console.cancelled. Observe it per token so a
                            # single-shot RAG/chat answer (no step boundaries)
                            # halts promptly and the upstream Lemonade stream is
                            # closed (GeneratorExit cascades to the llm_client
                            # generator, which shuts the HTTP socket). #2157
                            if self._console_cancelled():
                                cancelled_by_console = True
                                try:
                                    response_stream.close()
                                except Exception:  # noqa: BLE001 - best-effort teardown
                                    logger.debug(
                                        "Failed to close Lemonade stream on cancel",
                                        exc_info=True,
                                    )
                                break
                            if chunk_response.is_complete:
                                response_stats = chunk_response.stats
                                # Non-empty complete chunk = tool_calls sentinel from
                                # native tool-calling path (no streaming for tool calls)
                                if chunk_response.text:
                                    full_response = chunk_response.text
                            else:
                                self.console.print_streaming_text(chunk_response.text)
                                full_response += chunk_response.text

                        if cancelled_by_console:
                            break

                        self.console.print_streaming_text("", end_of_stream=True)
                        response = full_response
                        break
                    except ConnectionError as e:
                        error_msg = (
                            f"LLM Server Connection Failed (streaming): {str(e)}"
                        )
                        logger.error(error_msg)
                        self.console.print_error(error_msg)
                        self.error_history.append(
                            {
                                "step": steps_taken,
                                "error": error_msg,
                                "type": "llm_connection_error",
                            }
                        )
                        final_answer = (
                            f"I'm having trouble reaching the language model right now. "
                            f"Please make sure Lemonade Server is running.\n\n"
                            f"*Technical details: {str(e)}*"
                        )
                        break
                    except Exception as e:
                        logger.error(f"Unexpected error during streaming: {e}")
                        err_text = str(e).lower()
                        is_ctx_overflow = is_context_overflow_error(err_text)
                        # See non-streaming branch for explanation: re-raise
                        # if model was loaded with the wrong (small) ctx so
                        # the chat helper can reload it at 32K.
                        is_wrong_ctx_loaded = is_ctx_overflow and (
                            "context size (4096" in err_text
                            or "context size (8192" in err_text
                            or "context size (16384" in err_text
                            or "n_ctx': 4096" in err_text
                            or "n_ctx': 8192" in err_text
                            or "n_ctx': 16384" in err_text
                        )
                        if is_ctx_overflow and not is_wrong_ctx_loaded:
                            is_wrong_ctx_loaded = self._is_loaded_ctx_too_small()
                        if is_wrong_ctx_loaded:
                            self.error_history.append(
                                {
                                    "step": steps_taken,
                                    "error": str(e),
                                    "type": "llm_wrong_ctx_loaded_reraise",
                                }
                            )
                            logger.warning(
                                "Wrong ctx_size loaded (streaming) — re-raising"
                                " so chat helper can reload model: %s",
                                e,
                            )
                            raise
                        if is_ctx_overflow and not _retried_after_trim_stream:
                            messages = self._shrink_messages_for_overflow(messages)
                            self.error_history.append(
                                {
                                    "step": steps_taken,
                                    "error": str(e),
                                    "type": "llm_context_overflow_trimmed",
                                }
                            )
                            logger.warning(
                                "Context overflow mid-loop (streaming) — "
                                "shrunk messages to %d entries and retrying",
                                len(messages),
                            )
                            _retried_after_trim_stream = True
                            continue

                        self.error_history.append(
                            {
                                "step": steps_taken,
                                "error": str(e),
                                "type": "llm_streaming_error",
                            }
                        )
                        if is_ctx_overflow:
                            # Name the actual constraint and a next step
                            # (#2763) -- "re-ask with just the essentials" told
                            # the user nothing about WHAT was too big or HOW to
                            # shrink it, so every retry looked identical.
                            final_answer = _CONTEXT_STILL_OVERFLOWING_MESSAGE
                        else:
                            final_answer = (
                                f"Sorry, I ran into a problem while processing your request. "
                                f"This might be a temporary issue — try again in a moment.\n\n"
                                f"*Technical details: {str(e)}*"
                            )
                        break
                if final_answer is not None or cancelled_by_console:
                    break
            else:
                # Use progress indicator for non-streaming mode
                self.console.start_progress(self._progress_label())

                # Debug logging before LLM call
                if self.debug:

                    print(f"\n[DEBUG] About to call LLM with {len(messages)} messages")
                    print(
                        f"[DEBUG] Last message role: {messages[-1]['role'] if messages else 'No messages'}"
                    )
                    if messages and len(messages[-1].get("content", "")) < 500:
                        print(
                            f"[DEBUG] Last message content: {messages[-1]['content']}"
                        )
                    else:
                        print(
                            f"[DEBUG] Last message content length: {len(messages[-1].get('content', ''))}"
                        )
                    print(f"[DEBUG] Execution state: {self.execution_state}")
                    if self.execution_state == "PLANNING":
                        print("[DEBUG] Current step: Planning (no active plan yet)")
                    else:
                        print(
                            f"[DEBUG] Current step: {self.current_step}/{self.total_plan_steps}"
                        )

                # Get complete response from AgentSDK. On context overflow
                # mid-loop (the cumulative messages array got too long during
                # this turn — common after several search_file/index calls),
                # trim the oldest tool-result messages and retry ONCE before
                # giving up. Keeps the conversation salvageable instead of
                # failing the whole turn.
                _retried_after_trim = False
                while True:
                    try:
                        chat_response = self.chat.send_messages(
                            messages=messages,
                            system_prompt=self.system_prompt,
                            tools=self._openai_tools,
                        )
                        response = chat_response.text
                        response_stats = chat_response.stats
                        break  # success → exit retry loop
                    except ConnectionError as e:
                        self.console.stop_progress()
                        error_msg = f"LLM Server Connection Failed: {str(e)}"
                        logger.error(error_msg)
                        self.console.print_error(error_msg)
                        self.error_history.append(
                            {
                                "step": steps_taken,
                                "error": error_msg,
                                "type": "llm_connection_error",
                            }
                        )
                        final_answer = (
                            f"I'm having trouble reaching the language model right now. "
                            f"Please make sure Lemonade Server is running.\n\n"
                            f"*Technical details: {str(e)}*"
                        )
                        break
                    except Exception as e:
                        self.console.stop_progress()
                        if self.debug:
                            print(f"[DEBUG] Error calling LLM: {e}")
                        logger.error(f"Unexpected error calling LLM: {e}")

                        # Did we hit a context-overflow mid-loop? Classify by
                        # structure-then-text since typed exceptions get
                        # wrapped by AgentSDK and the backend's error shape
                        # varies (llama.cpp vs. FastFlowLM, #2513).
                        err_text = str(e).lower()
                        is_ctx_overflow = is_context_overflow_error(err_text)
                        # Detect "wrong ctx size loaded" — substring match on
                        # error text first (when raw payload is preserved),
                        # then probe Lemonade health if substring missed
                        # (typical: AgentSDK stringifies typed exception to
                        # user_message, dropping n_ctx detail).
                        is_wrong_ctx_loaded = is_ctx_overflow and (
                            "context size (4096" in err_text
                            or "context size (8192" in err_text
                            or "context size (16384" in err_text
                            or "n_ctx': 4096" in err_text
                            or "n_ctx': 8192" in err_text
                            or "n_ctx': 16384" in err_text
                        )
                        if is_ctx_overflow and not is_wrong_ctx_loaded:
                            is_wrong_ctx_loaded = self._is_loaded_ctx_too_small()
                        if is_wrong_ctx_loaded:
                            self.error_history.append(
                                {
                                    "step": steps_taken,
                                    "error": str(e),
                                    "type": "llm_wrong_ctx_loaded_reraise",
                                }
                            )
                            logger.warning(
                                "Wrong ctx_size loaded — re-raising so chat "
                                "helper can reload model: %s",
                                e,
                            )
                            raise
                        if is_ctx_overflow and not _retried_after_trim:
                            # Aggressive shrink: keep all message slots so the
                            # model still sees its tool-call history, but cap
                            # any single tool-result content to 500 chars and
                            # drop all-but-last-2 tool results entirely.
                            messages = self._shrink_messages_for_overflow(messages)
                            self.error_history.append(
                                {
                                    "step": steps_taken,
                                    "error": str(e),
                                    "type": "llm_context_overflow_trimmed",
                                }
                            )
                            logger.warning(
                                "Context overflow mid-loop — shrunk messages "
                                "to %d entries and retrying once",
                                len(messages),
                            )
                            _retried_after_trim = True
                            continue  # retry with smaller payload

                        # Either context-overflow after trim, or unrelated.
                        # Give up gracefully.
                        self.error_history.append(
                            {
                                "step": steps_taken,
                                "error": str(e),
                                "type": "llm_error",
                            }
                        )
                        if is_ctx_overflow:
                            # Name the actual constraint and a next step
                            # (#2763) -- "re-ask with just the essentials" told
                            # the user nothing about WHAT was too big or HOW to
                            # shrink it, so every retry looked identical.
                            final_answer = _CONTEXT_STILL_OVERFLOWING_MESSAGE
                        else:
                            # If we have a typed Lemonade error in the
                            # cause-chain (e.g. ``LemonadeUpstreamTimeoutError``
                            # from #1030), surface its actionable
                            # ``user_message`` verbatim instead of wrapping it
                            # with the generic "try again in a moment" copy —
                            # that wrapper actively misleads users on
                            # non-retryable failures.
                            typed_msg = self._extract_lemonade_user_message(e)
                            if typed_msg is not None:
                                final_answer = typed_msg
                            else:
                                final_answer = (
                                    f"Sorry, I ran into an unexpected problem. "
                                    f"This might be a temporary issue — try "
                                    f"again in a moment.\n\n"
                                    f"*Technical details: {str(e)}*"
                                )
                        break
                if final_answer is not None:
                    break

                # Stop the progress indicator
                self.console.stop_progress()

            # Strip <think>...</think> blocks emitted by reasoning models
            # (e.g. Qwen3.5).  Must happen before parsing so the JSON extractor
            # finds clean input, and before the response is stored in
            # conversation_history so the thinking text never bleeds into the
            # next turn and confuses the model about the current user message.
            response = re.sub(
                r"<think>.*?</think>", "", response, flags=re.DOTALL
            ).strip()

            # Print the LLM response to the console
            logger.debug(f"LLM response: {response[:200]}...")
            if self.show_prompts:
                self.console.print_response(response, "LLM Response")

            # Parse the response. Small models (e.g. 4B) sometimes emit malformed
            # tool_calls JSON — concatenated enum values, unterminated strings,
            # 1000+ char arguments. Don't fail the whole turn: log the error,
            # nudge the model to retry with simpler args, and continue the loop.
            try:
                parsed = self._parse_llm_response(response)
            except ValueError as parse_exc:
                logger.warning(
                    "Tool-call parse failed (step %d): %s — recovering with retry prompt",
                    steps_taken,
                    parse_exc,
                )
                self.error_history.append(
                    {
                        "step": steps_taken,
                        "error": str(parse_exc),
                        "type": "tool_call_parse_error",
                    }
                )
                error_count += 1
                # Issue #1023: pull the most recent successful image path
                # out of step_results so both the recovery prompt and the
                # give-up fallback can surface it.  When the SD two-step
                # flow's step-1 succeeded and step-2 parse-failed, the
                # canonical path is the breadcrumb the model needs to
                # retry verbatim, and the breadcrumb the user needs in the
                # final answer so the successful generation isn't lost.
                # No-op for agents that don't emit ``image_path``.
                _last_image_path = next(
                    (
                        r["image_path"]
                        for r in reversed(step_results)
                        if isinstance(r, dict)
                        and r.get("status") == "success"
                        and r.get("image_path")
                    ),
                    None,
                )
                # If we've already retried several times, give up gracefully and
                # answer in plain text rather than spamming the user.
                if error_count >= 3:
                    if _last_image_path:
                        final_answer = (
                            f"I generated your image at `{_last_image_path}`, "
                            "but I couldn't finish the follow-up step. "
                            "Try asking for the next step in a fresh message."
                        )
                    else:
                        final_answer = (
                            "I had trouble formatting my tool call. Could you "
                            "rephrase or break the request into smaller pieces?"
                        )
                    break
                assistant_msg = (
                    "[I tried to call a tool but my arguments were malformed.]"
                )
                user_msg = (
                    "Your last tool call had malformed arguments. "
                    "Please try again. Use ONLY the documented enum "
                    "values for each argument (e.g. 'brief', "
                    "'detailed', 'bullets' — never a long sentence). "
                    "If you don't need a tool, answer in plain text."
                )
                if _last_image_path:
                    user_msg += (
                        f"\n\nYour previous step generated an image at "
                        f"`{_last_image_path}`. If your next tool call "
                        "needs this path, copy that string VERBATIM — do "
                        "not retype it."
                    )
                    logger.info(
                        "[PARSE-RECOVERY] injected canonical image_path=%s",
                        _last_image_path,
                    )
                # Push a synthetic assistant turn + recovery user message so the
                # next LLM call has context. Don't include the raw envelope to
                # keep noise out of the conversation history.
                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_msg,
                    }
                )
                messages.append(
                    {
                        "role": "user",
                        "content": user_msg,
                    }
                )
                steps_taken += 1
                continue
            logger.debug(f"Parsed response: {parsed}")
            conversation.append({"role": "assistant", "content": parsed})

            # Add assistant response to messages for chat history (OpenAI
            # shape for native tool_calls, raw text otherwise — see
            # ``_build_assistant_message`` for the why).
            messages.append(self._build_assistant_message(response, parsed))

            # If the LLM needs to create a plan first, re-prompt it specifically for that
            if "needs_plan" in parsed and parsed["needs_plan"]:
                # Prepare a special prompt that specifically requests a plan
                deferred_tool = parsed.get("deferred_tool", None)
                deferred_args = parsed.get("deferred_tool_args", {})

                plan_prompt = (
                    "You MUST create a detailed plan first before taking any action.\n\n"
                    f"User request: {user_input}\n\n"
                )

                if deferred_tool:
                    plan_prompt += (
                        f"You initially wanted to use the {deferred_tool} tool with these arguments:\n"
                        f"{json.dumps(deferred_args, indent=2, default=self._json_serialize_fallback, ensure_ascii=False)}\n\n"
                        "However, you MUST first create a plan. Please create a plan that includes this tool usage as a step.\n\n"
                    )

                plan_prompt += (
                    "Create a detailed plan with all necessary steps in JSON format, including exact tool names and arguments.\n"
                    "Respond with your reasoning, plan, and the first tool to use."
                )

                # Store the plan prompt in conversation if debug is enabled
                if self.debug_prompts:
                    conversation.append(
                        {"role": "system", "content": {"prompt": plan_prompt}}
                    )
                    if self.show_prompts:
                        self.console.print_prompt(plan_prompt, "Plan Request Prompt")

                # Notify the user we're asking for a plan
                self.console.print_info("Requesting a detailed plan before proceeding")

                # Get the planning response
                if self.streaming:
                    # Add prompt to conversation if debug is enabled
                    if self.debug_prompts:
                        conversation.append(
                            {"role": "system", "content": {"prompt": plan_prompt}}
                        )
                        # Print the prompt if show_prompts is enabled
                        if self.show_prompts:
                            self.console.print_prompt(
                                plan_prompt, f"Prompt (Step {steps_taken})"
                            )

                    # Handle streaming as before
                    full_response = ""
                    # Add plan request to messages
                    messages.append({"role": "user", "content": plan_prompt})

                    # Use AgentSDK for streaming plan response
                    stream_gen = self.chat.send_messages_stream(
                        messages=messages,
                        system_prompt=self.system_prompt,
                        tools=self._openai_tools,
                    )

                    for chunk_response in stream_gen:
                        if chunk_response.is_complete:
                            if chunk_response.text:
                                full_response = chunk_response.text
                        else:
                            chunk = chunk_response.text
                            if hasattr(self.console, "print_streaming_text"):
                                self.console.print_streaming_text(chunk)
                            else:
                                print(chunk, end="", flush=True)
                            full_response += chunk

                    if hasattr(self.console, "print_streaming_text"):
                        self.console.print_streaming_text("", end_of_stream=True)
                    else:
                        print("", flush=True)

                    plan_response = full_response
                else:
                    # Use progress indicator for non-streaming mode
                    self.console.start_progress("Creating plan")

                    # Store the plan prompt in conversation if debug is enabled
                    if self.debug_prompts:
                        conversation.append(
                            {"role": "system", "content": {"prompt": plan_prompt}}
                        )
                        if self.show_prompts:
                            self.console.print_prompt(
                                plan_prompt, "Plan Request Prompt"
                            )

                    # Add plan request to messages
                    messages.append({"role": "user", "content": plan_prompt})

                    # Use AgentSDK for non-streaming plan response
                    chat_response = self.chat.send_messages(
                        messages=messages,
                        system_prompt=self.system_prompt,
                        tools=self._openai_tools,
                    )
                    plan_response = chat_response.text
                    self.console.stop_progress()

                # Strip <think> blocks before parsing (same reason as main path)
                plan_response = re.sub(
                    r"<think>.*?</think>", "", plan_response, flags=re.DOTALL
                ).strip()

                # Parse the plan response
                parsed_plan = self._parse_llm_response(plan_response)
                logger.debug(f"Parsed plan response: {parsed_plan}")
                conversation.append({"role": "assistant", "content": parsed_plan})

                # Add plan response to messages for chat history. Same
                # OpenAI-shape rule as the main-response append (issue
                # #944): a tool-calling-trained planner can emit native
                # ``tool_calls`` instead of (or alongside) the expected
                # plan JSON — those must be preserved as a structured
                # assistant turn so the fan-out below can correlate
                # results back via ``tool_call_id``.
                messages.append(
                    self._build_assistant_message(plan_response, parsed_plan)
                )

                # Display the agent's reasoning for the plan
                self.console.print_thought(parsed_plan.get("thought", "Creating plan"))
                self.console.print_goal(parsed_plan.get("goal", "Planning for task"))

                # Set the parsed response to the new plan for further processing
                parsed = parsed_plan
            else:
                # Display the agent's reasoning in real-time (only if provided)
                # Skip if we just displayed thought/goal for a plan request above
                thought = parsed.get("thought", "").strip()
                goal = parsed.get("goal", "").strip()

                if thought and thought != "No explicit reasoning provided":
                    self.console.print_thought(thought)

                if goal and goal != "No explicit goal provided":
                    self.console.print_goal(goal)

            # Process plan if available
            if "plan" in parsed:
                # Validate that plan is actually a list, not a string or other type
                if not isinstance(parsed["plan"], list):
                    logger.error(
                        f"Invalid plan format: expected list, got {type(parsed['plan']).__name__}. "
                        f"Plan content: {parsed['plan']}"
                    )
                    # The "continue" below asks the LLM to correct itself —
                    # not a fatal top-level failure (#2515).
                    self.console.print_error(
                        f"LLM returned invalid plan format (expected array, got {type(parsed['plan']).__name__}). "
                        "Asking for correction...",
                        recoverable=True,
                    )

                    # Create error recovery prompt
                    error_msg = (
                        "ERROR: You provided a plan in the wrong format.\n"
                        "Expected: an array of step objects\n"
                        f"You provided: {type(parsed['plan']).__name__}\n\n"
                        "The correct format is:\n"
                        f'{{"plan": [{{"tool": "tool_name", "tool_args": {{...}}, "description": "..."}}]}}\n\n'
                        f"Please create a proper plan as an array of step objects for: {user_input}"
                    )
                    messages.append({"role": "user", "content": error_msg})

                    # Continue to next iteration to get corrected plan
                    continue

                # Validate that plan items are dictionaries with required fields
                invalid_steps = []
                for i, step in enumerate(parsed["plan"]):
                    if not isinstance(step, dict):
                        invalid_steps.append((i, type(step).__name__, step))
                    elif "tool" not in step or not step["tool"]:
                        invalid_steps.append((i, "missing tool field", step))
                    elif "tool_args" not in step:
                        # Auto-add empty tool_args for convenience
                        # LLMs sometimes omit this for tools with all optional parameters
                        step["tool_args"] = {}
                        logger.debug(
                            f"Auto-added empty tool_args for step {i+1}: {step['tool']}"
                        )

                if invalid_steps:
                    logger.error(f"Invalid plan steps found: {invalid_steps}")
                    # The "continue" below asks the LLM to correct itself —
                    # not a fatal top-level failure (#2515).
                    self.console.print_error(
                        f"Plan contains {len(invalid_steps)} invalid step(s). Asking for correction...",
                        recoverable=True,
                    )

                    # Create detailed error message
                    error_details = "\n".join(
                        [
                            f"Step {i+1}: {issue} - {step}"
                            for i, issue, step in invalid_steps[
                                :3
                            ]  # Show first 3 errors
                        ]
                    )

                    error_msg = (
                        f"ERROR: Your plan contains invalid steps:\n{error_details}\n\n"
                        f"Each step must be a dictionary with 'tool' and 'tool_args' fields:\n"
                        f'{{"tool": "tool_name", "tool_args": {{...}}, "description": "..."}}\n\n'
                        f"Please create a corrected plan for: {user_input}"
                    )
                    messages.append({"role": "user", "content": error_msg})

                    # Continue to next iteration to get corrected plan
                    continue

                # Plan is valid - proceed with execution
                self.current_plan = parsed["plan"]
                self.current_step = 0
                self.total_plan_steps = len(self.current_plan)
                self.execution_state = self.STATE_EXECUTING_PLAN
                logger.debug(
                    f"New plan created with {self.total_plan_steps} steps: {self.current_plan}"
                )

            # === Native parallel tool_calls fan-out (issue #944) ===
            #
            # Tool-calling-trained models (Gemma-4-E4B-it-GGUF — GAIA's
            # default per #865 — and the Qwen3-Instruct line) routinely emit
            # multiple ``tool_calls`` in a single response when the user
            # utterance contains multiple distinct intents. We drain them
            # sequentially within the same loop iteration, appending one
            # ``role=tool`` message per call (with its real ``tool_call_id``)
            # before re-prompting the LLM. This matches the OpenAI Chat
            # Completions tools API contract and makes parallel calls
            # behave like N independent sequential calls without the
            # overhead of N LLM round-trips.
            #
            # The legacy single-tool path below ONLY fires for embedded
            # JSON responses (no ``tool_calls`` field set) so that for
            # native single calls we still get proper ``tool_call_id``
            # linkage on the result message.
            if parsed.get("tool_calls"):
                tc_list = parsed["tool_calls"]
                any_error = False
                last_error = None
                fanout_repeat_break = False

                for fan_idx, tc in enumerate(tc_list):
                    tool_name = tc["name"]
                    tool_args = tc["tool_args"]
                    tool_call_id = tc["id"]
                    logger.debug(
                        "Tool call %d/%d: %s with args %s",
                        fan_idx + 1,
                        len(tc_list),
                        tool_name,
                        tool_args,
                    )

                    # Display the tool call in real-time
                    self.console.print_tool_usage(tool_name)
                    if tool_args:
                        self.console.pretty_print_json(tool_args, "Arguments")
                    self.console.start_progress(f"Executing {tool_name}")

                    # Loop detection — same shape as legacy path so
                    # repeated calls across iterations are still caught.
                    current_call = (tool_name, str(tool_args))
                    tool_call_history.append(current_call)
                    tool_call_log.append(current_call)
                    if len(tool_call_history) > 5:
                        tool_call_history.pop(0)
                    consecutive_count = 0
                    for prior in reversed(tool_call_history):
                        if prior == current_call:
                            consecutive_count += 1
                        else:
                            break
                    if consecutive_count >= self.max_consecutive_repeats:
                        self.console.stop_progress()
                        # NATIVE path appends results to ``previous_outputs``
                        # (line 3387), not ``step_results``. Unwrap the
                        # ``result`` field so the helper sees actual tool
                        # results, not the wrapper dicts.
                        recent_results = [o.get("result") for o in previous_outputs]
                        final_answer = self._build_loop_break_summary(
                            tool_name, consecutive_count, recent_results
                        )
                        self.console.print_repeated_tool_warning()
                        fanout_repeat_break = True
                        break

                    # Execute
                    tool_result = self._execute_tool_timed(tool_name, tool_args)
                    self.console.stop_progress()

                    # Result-based dedup for query family tools
                    _QUERY_TOOLS = (
                        "query_documents",
                        "query_specific_file",
                        "query_indexed_documents",
                    )
                    if tool_name in _QUERY_TOOLS:
                        result_key = f"{tool_name}:{hash(str(tool_result))}"
                        query_result_cache[result_key] = (
                            query_result_cache.get(result_key, 0) + 1
                        )
                        if query_result_cache[result_key] >= 2:
                            logger.debug(
                                "[DEDUP] Same query result returned %d times "
                                "— injecting stop signal",
                                query_result_cache[result_key],
                            )
                            dedup_msg = (
                                f"[SYSTEM] You have received this same result "
                                f"from {tool_name} "
                                f"{query_result_cache[result_key]} times. "
                                "Querying again will not yield new "
                                "information. STOP querying and answer "
                                "directly from what you have retrieved, "
                                "OR check your prior turn responses for "
                                "relevant data, OR state that the "
                                "information was not found in the document."
                            )
                            messages.append({"role": "user", "content": dedup_msg})

                    # Input-based dedup for mutation tools (#1317): catch an
                    # identical mutation re-issue at the first repeat. Errored
                    # calls are skipped so a rejected retry isn't mistaken for
                    # an applied change (#2464).
                    self._dedup_mutation_call(
                        tool_name,
                        tool_args,
                        mutation_call_cache,
                        messages,
                        tool_result,
                    )

                    # Domain hooks. A returned plan switches the agent into
                    # STATE_EXECUTING_PLAN for prereq-style recovery. The
                    # base impl returns None but the hook's annotation is
                    # Optional[List[...]] — silence pylint's None-inference.
                    # pylint: disable-next=assignment-from-none
                    _next_plan = self._post_process_tool_result(
                        tool_name, tool_args, tool_result
                    )
                    if _next_plan is not None:
                        self._inject_recovery_plan(_next_plan)

                    # Truncate large results before logging
                    truncated_result = self._handle_large_tool_result(
                        tool_name, tool_result, conversation, tool_args
                    )

                    self.console.pretty_print_json(tool_result, "Result")
                    self.console.print_tool_complete()

                    previous_outputs.append(
                        {
                            "tool": tool_name,
                            "args": tool_args,
                            "result": truncated_result,
                        }
                    )

                    # Append the tool result message with the *real*
                    # tool_call_id from the originating call so the next
                    # LLM round can correlate this result with the right
                    # call in the prior assistant turn (issue #944).
                    messages.append(
                        self._create_tool_message(
                            tool_name,
                            truncated_result,
                            tool_call_id=tool_call_id,
                        )
                    )

                    # Track errors but DON'T break early — drain all N
                    # tool calls first so conversation history reflects
                    # the full set, per #944 acceptance criterion (b).
                    is_error = isinstance(tool_result, dict) and (
                        tool_result.get("status") == "error"
                        or tool_result.get("success") is False
                        or tool_result.get("has_errors") is True
                        or tool_result.get("return_code", 0) != 0
                    )
                    if is_error:
                        error_count += 1
                        last_error = (
                            tool_result.get("error_brief")
                            or tool_result.get("error")
                            or tool_result.get("stderr")
                            or tool_result.get("hint")
                            or tool_result.get("suggested_fix")
                            or (
                                "Command failed with return code "
                                f"{tool_result.get('return_code')}"
                            )
                        )
                        logger.warning(
                            "Tool execution error in parallel call "
                            "%d/%d (count: %d): %s",
                            fan_idx + 1,
                            len(tc_list),
                            error_count,
                            last_error,
                        )
                        if not tool_result.get("error_displayed"):
                            # any_error below switches to STATE_ERROR_RECOVERY
                            # and retries — not a fatal top-level failure
                            # (#2515, the archive_message_batch repro).
                            self.console.print_error(last_error, recoverable=True)
                        any_error = True

                if fanout_repeat_break:
                    break  # break outer while

                if any_error:
                    # All N results have been appended. Now transition to
                    # error recovery so the next LLM round can react.
                    self.execution_state = self.STATE_ERROR_RECOVERY
                    self.console.print_state_info(
                        "ERROR RECOVERY: Handling tool execution failure"
                    )
                    continue

                # Otherwise fall through to stats / answer / next iter.
                # The legacy ``if parsed.get("tool")`` branch below is
                # gated to skip when ``tool_calls`` is set so it won't
                # double-execute the first call.

            # If the response contains a tool call, execute it (legacy
            # embedded-JSON path). Skipped when ``tool_calls`` is set —
            # those have already been dispatched by the fan-out above.
            if (
                parsed.get("tool")
                and "tool_args" in parsed
                and not parsed.get("tool_calls")
            ):

                # Display the current plan with the current step highlighted
                if self.current_plan:
                    self.console.print_plan(self.current_plan, self.current_step)

                # When both plan and tool are present, prioritize the plan execution
                # If we have a plan, we should execute from the plan, not the standalone tool call
                if "plan" in parsed and self.current_plan and self.total_plan_steps > 0:
                    # Skip the standalone tool execution and let the plan execution handle it
                    # The plan execution logic will handle this in the next iteration
                    logger.debug(
                        "Plan and tool both present - deferring to plan execution logic"
                    )
                    continue  # Skip tool execution, let plan execution handle it

                # If this was a single-step plan, mark as completed after tool execution
                if self.total_plan_steps == 1:
                    logger.debug(
                        "Single-step plan will be marked completed after tool execution"
                    )
                    self.execution_state = self.STATE_COMPLETION

                tool_name = parsed["tool"]
                tool_args = parsed["tool_args"]
                logger.debug(f"Tool call detected: {tool_name} with args {tool_args}")

                # Display the tool call in real-time
                self.console.print_tool_usage(tool_name)

                if tool_args:
                    self.console.pretty_print_json(tool_args, "Arguments")

                # Start progress indicator for tool execution
                self.console.start_progress(f"Executing {tool_name}")

                # Check for repeated tool calls (allow up to 3 identical calls)
                current_call = (tool_name, str(tool_args))
                tool_call_history.append(current_call)
                tool_call_log.append(
                    current_call
                )  # Full unbounded log for workflow guards

                # Keep only last 5 calls for loop detection
                if len(tool_call_history) > 5:
                    tool_call_history.pop(0)

                # Count consecutive identical calls
                consecutive_count = 0
                for call in reversed(tool_call_history):
                    if call == current_call:
                        consecutive_count += 1
                    else:
                        break

                # Stop after max_consecutive_repeats identical calls
                if consecutive_count >= self.max_consecutive_repeats:
                    # Stop progress indicator
                    self.console.stop_progress()

                    # Force a final answer if the same tool is called repeatedly.
                    # Branches on whether the recent calls were errors so we
                    # never claim success on a loop of failures.
                    final_answer = self._build_loop_break_summary(
                        tool_name, consecutive_count, step_results
                    )

                    self.console.print_repeated_tool_warning()
                    break

                # Execute the tool
                tool_result = self._execute_tool_timed(tool_name, tool_args)

                # Stop progress indicator
                self.console.stop_progress()

                # Issue #1023: record success/failure of capability tools so
                # the verbose-failure override downstream fires only when the
                # tool actually errored.  ``.lower()`` mirrors the defensive
                # check at ``has_tried_capability_tool`` so a model that emits
                # ``Generate_Image`` doesn't slip past the tracker.
                if any(tool_name.lower().startswith(_s) for _s in _SD_CAPABILITY_TOOLS):
                    capability_tool_last_succeeded = not (
                        isinstance(tool_result, dict)
                        and tool_result.get("status") in ("error", "denied")
                    )

                # Issue #1023: mirror the plan-execution branch (L2330) so
                # ``step_results`` is consistent regardless of whether the
                # LLM emitted a multi-step plan or a series of single-tool
                # responses.  Downstream consumers (parse-error recovery
                # prompt, give-up fallback) read ``step_results`` for the
                # canonical ``image_path`` — without this append, the
                # legacy single-tool path leaves them empty-handed.
                step_results.append(tool_result)

                # Result-based dedup: if this tool (query family) returns the same result
                # it returned in a prior call, inject a correction so the agent stops looping.
                _QUERY_TOOLS = (
                    "query_documents",
                    "query_specific_file",
                    "query_indexed_documents",
                )
                if tool_name in _QUERY_TOOLS:
                    result_key = f"{tool_name}:{hash(str(tool_result))}"
                    query_result_cache[result_key] = (
                        query_result_cache.get(result_key, 0) + 1
                    )
                    if query_result_cache[result_key] >= 2:
                        logger.debug(
                            "[DEDUP] Same query result returned %d times — injecting stop signal",
                            query_result_cache[result_key],
                        )
                        dedup_msg = (
                            f"[SYSTEM] You have received this same result from {tool_name} "
                            f"{query_result_cache[result_key]} times. "
                            "Querying again will not yield new information. "
                            "STOP querying and answer directly from what you have retrieved, "
                            "OR check your prior turn responses for relevant data, "
                            "OR state that the information was not found in the document."
                        )
                        messages.append({"role": "user", "content": dedup_msg})

                # Input-based dedup for mutation tools (#1317): catch an
                # identical mutation re-issue at the first repeat. Errored calls
                # are skipped so a rejected retry isn't mistaken for an applied
                # change (#2464).
                self._dedup_mutation_call(
                    tool_name, tool_args, mutation_call_cache, messages, tool_result
                )

                # Handle domain-specific post-processing.
                # A returned plan switches into STATE_EXECUTING_PLAN. The
                # base impl returns None but the hook's annotation is
                # Optional[List[...]] — silence pylint's None-inference.
                # pylint: disable-next=assignment-from-none
                _next_plan = self._post_process_tool_result(
                    tool_name, tool_args, tool_result
                )
                if _next_plan is not None:
                    self._inject_recovery_plan(_next_plan)

                # Handle large tool results
                truncated_result = self._handle_large_tool_result(
                    tool_name, tool_result, conversation, tool_args
                )

                # Display the tool result in real-time (show full result to user).
                # Emit result BEFORE complete so SSE latency is captured.
                self.console.pretty_print_json(tool_result, "Result")
                self.console.print_tool_complete()

                # Store the truncated output for future context
                previous_outputs.append(
                    {"tool": tool_name, "args": tool_args, "result": truncated_result}
                )

                # Share tool output with subsequent LLM calls
                messages.append(self._create_tool_message(tool_name, truncated_result))

                # For single-step plans, we still need to let the LLM process the result
                # This is especially important for RAG queries where the LLM needs to
                # synthesize the retrieved information into a coherent answer
                if (
                    self.execution_state == self.STATE_COMPLETION
                    and self.total_plan_steps == 1
                ):
                    logger.debug(
                        "Single-step plan execution completed, requesting final answer from LLM"
                    )
                    # Don't break here - let the loop continue so the LLM can process the tool result
                    # The tool result has already been added to messages, so the next iteration
                    # will call the LLM with that result

                # Check if tool execution resulted in an error (support multiple error formats)
                is_error = isinstance(tool_result, dict) and (
                    tool_result.get("status") == "error"
                    or tool_result.get("success") is False
                    or tool_result.get("has_errors") is True
                    or tool_result.get("return_code", 0) != 0
                )
                if is_error:
                    error_count += 1
                    # Prefer error_brief for logging (avoids duplicate formatted output)
                    last_error = (
                        tool_result.get("error_brief")
                        or tool_result.get("error")
                        or tool_result.get("stderr")
                        or tool_result.get("hint")
                        or tool_result.get("suggested_fix")
                        or f"Command failed with return code {tool_result.get('return_code')}"
                    )
                    logger.warning(
                        f"Tool execution error in plan (count: {error_count}): {last_error}"
                    )
                    # Only print if error wasn't already displayed by _execute_tool
                    if not tool_result.get("error_displayed"):
                        # STATE_ERROR_RECOVERY below retries this — not a
                        # fatal top-level failure (#2515).
                        self.console.print_error(last_error, recoverable=True)

                    # Switch to error recovery state
                    self.execution_state = self.STATE_ERROR_RECOVERY
                    self.console.print_state_info(
                        "ERROR RECOVERY: Handling tool execution failure"
                    )

                    # Break out of tool execution to trigger error recovery prompt
                    continue

            # Collect and store performance stats for token tracking
            # Do this BEFORE checking for final answer so stats are always collected
            perf_stats = response_stats or self.chat.get_stats()
            if perf_stats:
                conversation.append(
                    {
                        "role": "system",
                        "content": {
                            "type": "stats",
                            "step": steps_taken,
                            "performance_stats": perf_stats,
                        },
                    }
                )

            # Check for final answer (after collecting stats)
            if "answer" in parsed:
                answer_candidate = parsed["answer"]
                # Guard against incomplete workflows: detect when the LLM outputs
                # planning text ("Let me now search...") as a final answer after
                # calling index_document but before issuing a query tool call.
                # This is a known failure pattern — the agent stops mid-workflow.
                _INDEX_TOOLS = (
                    "index_document",
                    "index_documents",
                    "index_dir",
                    "index_folder",
                )
                _PLANNING_PHRASES = (
                    "let me now",
                    "i'll now",
                    "i will now search",
                    "i will now query",
                    "i'll check",
                    "let me check",
                    "let me search",
                    "let me query",
                    "now search within",
                    "search within this",
                    "now search for",
                    "query it now",
                    "search the document",
                    "let me look",
                    "i'll look",
                    "i will look",
                    "let me retrieve",
                    "i'll retrieve",
                    "let me find",
                )
                last_was_index = bool(
                    tool_call_history
                    and any(
                        tool_call_history[-1][0].lower().startswith(p)
                        for p in _INDEX_TOOLS
                    )
                )
                # Post-index query guard: catch when the agent indexed a document but
                # answers from LLM memory without querying it first. This is a
                # hallucination pattern — the agent returns confident-sounding wrong
                # answers because it never retrieved the document's actual content.
                _QUERY_TOOLS = (
                    "query_specific_file",
                    "query_documents",
                    "query_indexed_documents",
                    "search_indexed_chunks",
                )
                last_index_pos = -1
                for _pos, (_tname, _) in enumerate(tool_call_log):
                    if any(_tname.lower().startswith(_p) for _p in _INDEX_TOOLS):
                        last_index_pos = _pos
                query_after_index = any(
                    _pos > last_index_pos
                    and any(_tname.lower().startswith(_q) for _q in _QUERY_TOOLS)
                    for _pos, (_tname, _) in enumerate(tool_call_log)
                )
                if (
                    last_index_pos >= 0
                    and not query_after_index
                    and steps_taken < steps_limit - 1
                ):
                    logger.debug(
                        "[WORKFLOW] Post-index answer without query — forcing query tool call: %s",
                        answer_candidate[:80],
                    )
                    # Deterministic fix: extract the file path from the last index_document
                    # call and execute query_specific_file directly, bypassing the LLM.
                    # This is more reliable than sending a correction and hoping the LLM
                    # complies, since the LLM may loop on the same hallucination.
                    _last_indexed_file = None
                    for _tname, _targs_str in reversed(tool_call_log):
                        if any(_tname.lower().startswith(_p) for _p in _INDEX_TOOLS):
                            try:
                                # tool_call_log stores str(tool_args) which is Python repr
                                # (single-quoted keys), NOT JSON — use ast.literal_eval
                                if isinstance(_targs_str, str):
                                    try:
                                        _targs = ast.literal_eval(_targs_str)
                                    except (ValueError, SyntaxError):
                                        _targs = json.loads(_targs_str)
                                else:
                                    _targs = _targs_str
                                _last_indexed_file = (
                                    _targs.get("file_path")
                                    or _targs.get("path")
                                    or _targs.get("document_path")
                                )
                            except Exception:
                                pass
                            break
                    if _last_indexed_file:
                        # Inject a fake assistant tool-call so the conversation shows
                        # the query happening, then execute it and inject the result.
                        _forced_query = (
                            user_input[:120]
                            if user_input
                            else "summary overview key facts"
                        )
                        _forced_tool_call = {
                            "thought": "I indexed the document but must query it before answering.",
                            "tool": "query_specific_file",
                            "tool_args": {
                                "file_path": _last_indexed_file,
                                "query": _forced_query,
                            },
                        }
                        conversation.append(
                            {"role": "assistant", "content": _forced_tool_call}
                        )
                        _forced_result = self._execute_tool_timed(
                            "query_specific_file",
                            {"file_path": _last_indexed_file, "query": _forced_query},
                        )
                        tool_call_log.append(
                            ("query_specific_file", str(_forced_tool_call["tool_args"]))
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": f"Tool result: {_forced_result}",
                            }
                        )
                        logger.debug(
                            "[WORKFLOW] Forced query result injected, resuming loop."
                        )
                    else:
                        # No file path extractable — fall back to correction message
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "SYSTEM: You indexed a document but answered without querying it. "
                                    "You MUST call query_specific_file or query_documents NOW. "
                                    "Output a query tool call immediately."
                                ),
                            }
                        )
                    continue

                # Universal planning-text guard: catch any short response that is
                # only an intent sentence ("I'll check...", "Let me query...") with
                # no actual answer, regardless of whether tools were already called.
                # This covers three cases:
                #   1. post-index planning (index_document → "Let me now search...")
                #   2. no-tool planning on follow-up turns ("I'll check the remote work policy")
                #   3. post-tool planning after getting results ("I need more info... Let me query")
                is_planning_text = len(answer_candidate) < 500 and any(
                    phrase in answer_candidate.lower() for phrase in _PLANNING_PHRASES
                )
                if is_planning_text and steps_taken < steps_limit - 1:
                    # Inject a correction message and continue the loop to force the answer
                    logger.debug(
                        "[WORKFLOW] Blocking planning-only response as final answer: %s",
                        answer_candidate[:80],
                    )
                    correction = (
                        "You produced planning text instead of an answer. "
                        "You already have the data from the tool results above — "
                        "output the final answer NOW based on what you retrieved. "
                        "Do not call another tool. Just answer the question directly."
                    )
                    if last_was_index:
                        correction = (
                            "You indexed the document but haven't answered the question yet. "
                            "Call query_specific_file or query_documents NOW to retrieve the "
                            "actual content. Output a tool call JSON — not planning text."
                        )
                    elif not tool_call_history:
                        correction = (
                            "You said you would look that up but called no tools. "
                            "Call the appropriate tool RIGHT NOW. "
                            "Output a JSON tool call — not another planning sentence."
                        )
                    messages.append({"role": "user", "content": correction})
                    continue  # Don't set final_answer — loop again to force the query

                # Tool-syntax artifact guard: catch responses that are just a tool-call label
                # like "[tool:query_specific_file]" — Qwen3 confusion where the model writes
                # the tool invocation syntax as its answer text instead of calling it.
                _TOOL_ARTIFACT_PATTERN = re.compile(
                    r"^\s*\[tool:[a-zA-Z_]+\]\s*$", re.MULTILINE
                )
                if (
                    _TOOL_ARTIFACT_PATTERN.match(answer_candidate.strip())
                    and steps_taken < steps_limit - 1
                ):
                    logger.debug(
                        "[WORKFLOW] Blocking tool-syntax artifact as final answer: %s",
                        answer_candidate[:80],
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "SYSTEM: Your response is just a tool call label (e.g. '[tool:query_specific_file]'), "
                                "not an actual answer. You have already gathered the information you need from your "
                                "previous tool calls. Write a complete prose answer to the user's question using "
                                "the information already retrieved."
                            ),
                        }
                    )
                    continue

                # Raw JSON hallucination guard: catch responses that contain fake tool-output
                # JSON blobs instead of actual prose answers. This is a failure mode where
                # the LLM writes what it imagines a tool would return rather than calling it.
                _RAW_JSON_PATTERNS = [
                    r'```json\s*\{[^`]*"status"\s*:',
                    r'```json\s*\{[^`]*"documents"\s*:',
                    r'```json\s*\{[^`]*"chunks"\s*:',
                    r'\{\s*"status"\s*:\s*"success"',
                    r'\{\s*"documents"\s*:\s*\[',
                ]
                is_raw_json = any(
                    re.search(p, answer_candidate, re.DOTALL)
                    for p in _RAW_JSON_PATTERNS
                )
                if is_raw_json and steps_taken < steps_limit - 1:
                    logger.debug(
                        "[WORKFLOW] Blocking raw-JSON hallucination as final answer: %s",
                        answer_candidate[:120],
                    )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "SYSTEM: Your response contains raw JSON that looks like a fabricated "
                                "tool output. Do NOT write JSON in your response. If you need data, "
                                "call the actual tool. Otherwise, write your answer in plain prose. "
                                "Provide a clean prose answer to the user's question now."
                            ),
                        }
                    )
                    continue

                # Capability-claim-without-attempt guard: catch responses that declare
                # a tool's availability or unavailability (e.g. "I can generate images
                # when the --sd flag is active") without having tried the tool first.
                # This fires for generate_image only — the most common failure pattern.
                # If the tool was already attempted (successfully or not), the claim is
                # based on real evidence and should be allowed through.
                _CAPABILITY_CLAIM_PATTERNS = [
                    r"--sd\b",
                    r"\bsd flag\b",
                    r"stable diffusion.*active",
                    r"stable diffusion.*when",
                    r"image generation.*flag",
                    r"generate images when",
                    r"can generate images",
                    r"i can.*create.*image",
                    r"when.*--sd",
                ]
                has_tried_capability_tool = any(
                    any(_tname.lower().startswith(_s) for _s in _SD_CAPABILITY_TOOLS)
                    for _tname, _ in tool_call_log
                )
                is_capability_claim = any(
                    re.search(_p, answer_candidate, re.IGNORECASE)
                    for _p in _CAPABILITY_CLAIM_PATTERNS
                )
                # Even when generate_image was attempted, block if the response
                # STILL makes a conditional capability claim without acknowledging
                # the actual tool outcome (error or success).
                _SD_OUTCOME_ACKNOWLEDGMENT = [
                    r"not available",
                    r"unavailable",
                    r"not.*active",
                    r"not.*enabled",
                    r"can't generate",
                    r"cannot generate",
                    r"unable to generate",
                    r"tried.*generat",
                    r"attempted.*generat",
                    r"generat.*error",
                    r"generat.*fail",
                    r"image.*generat.*not",
                    r"success",
                    r"generated.*image",
                    r"here.*image",
                ]
                outcome_acknowledged = has_tried_capability_tool and any(
                    re.search(_p, answer_candidate, re.IGNORECASE)
                    for _p in _SD_OUTCOME_ACKNOWLEDGMENT
                )
                _should_block_sd = (
                    is_capability_claim
                    and not outcome_acknowledged
                    and steps_taken < steps_limit - 1
                )
                if _should_block_sd:
                    logger.debug(
                        "[WORKFLOW] Blocking SD capability claim%s: %s",
                        " (post-attempt)" if has_tried_capability_tool else "",
                        answer_candidate[:80],
                    )
                    # Extract what the user asked for from the last user message
                    _last_user_msg = next(
                        (
                            m.get("content", "")
                            for m in reversed(messages)
                            if m.get("role") == "user"
                            and isinstance(m.get("content"), str)
                        ),
                        "the requested image",
                    )
                    if not has_tried_capability_tool:
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "SYSTEM: STOP. Do NOT write text. You must output a JSON tool call. "
                                    "You attempted to describe image generation capability without calling "
                                    "the tool. The ONLY valid next response is a generate_image tool call. "
                                    "Output this JSON right now (replace the prompt with what the user asked for):\n"
                                    '{"tool": "generate_image", "tool_args": {"prompt": "high quality photorealistic image, '
                                    + _last_user_msg[:80].replace('"', "'")
                                    + '"}}\n'
                                    "Do not write anything else. Just the JSON above."
                                ),
                            }
                        )
                    else:
                        # Tool was tried — force acknowledgment of the actual outcome
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "SYSTEM: You called generate_image and received a result. "
                                    "Your response must describe what ACTUALLY happened — either "
                                    "the image was generated successfully, or the tool returned an error. "
                                    "Do NOT say 'I can generate images when --sd is active'. "
                                    "Describe the actual tool outcome now."
                                ),
                            }
                        )
                    continue

                # Post-failure verbosity guard: when generate_image was called and
                # failed, the LLM often apologises and explains "what it would have done"
                # with prompt-engineering tips. Intercept and replace with a clean response.
                #
                # Issue #1023: gate on the LATEST outcome of the capability tool.
                # When generate_image succeeded and a *different* tool's parse
                # error provoked a verbose apology, the override used to clobber
                # the model's reply with a misleading "Image generation is not
                # available" message even though the image was generated.  Now
                # the override fires only when the most recent capability call
                # actually returned an error.
                if (
                    has_tried_capability_tool
                    and capability_tool_last_succeeded is False
                ):
                    _SD_POST_FAILURE_VERBOSE = [
                        r"would have done",
                        r"what i would",
                        r"prompt enhancement",
                        r"i apologize for the confusion",
                        r"let me explain what",
                        r"enhance.*prompt",
                        r"prompt.*technique",
                        r"following.*research",
                    ]
                    _is_verbose_sd_failure = any(
                        re.search(_p, answer_candidate, re.IGNORECASE)
                        for _p in _SD_POST_FAILURE_VERBOSE
                    )
                    if _is_verbose_sd_failure:
                        answer_candidate = (
                            "Image generation is not available in this session — "
                            "start GAIA with the `--sd` flag to enable it."
                        )

                final_answer = self.finalize_answer(answer_candidate, conversation)
                self.execution_state = self.STATE_COMPLETION
                # Compute the real token total BEFORE printing the answer so it
                # can ride the same event, instead of the post-loop aggregation
                # below which runs after print_final_answer already fired
                # (#2899). Output tokens only — "tokens actually generated",
                # matching the tok/s calc downstream which is also output-only.
                _pre_input_tokens, pre_output_tokens = _sum_conversation_tokens(
                    conversation, self._tool_reported_usage
                )
                # Sealed before the answer prints: total_s must mean "until it
                # was on screen", and the console folds the record into that
                # same event.
                turn_record = self._finish_turn_record(final_answer, steps_taken)
                self._publish_turn_metrics(turn_record)
                self.console.print_final_answer(
                    final_answer,
                    streaming=self.streaming,
                    total_tokens=pre_output_tokens,
                    ttft_seconds=_query_ttft_seconds(conversation),
                )
                break

            # Check if we're at the limit and ask user if they want to continue
            if steps_taken == steps_limit and final_answer is None:
                # Show what was accomplished
                max_steps_msg = self._generate_max_steps_message(
                    conversation, steps_taken, steps_limit
                )
                self.console.print_warning(max_steps_msg)

                # Ask user if they want to continue (skip in silent mode OR if stdin is not available)
                # IMPORTANT: Never call input() in API/CI contexts to avoid blocking threads
                import sys

                has_stdin = sys.stdin and sys.stdin.isatty()
                if has_stdin and not (
                    hasattr(self, "silent_mode") and self.silent_mode
                ):
                    try:
                        response = (
                            input("\nContinue with 50 more steps? (y/n): ")
                            .strip()
                            .lower()
                        )
                        if response in ["y", "yes"]:
                            steps_limit += 50
                            self.console.print_info(
                                f"✓ Continuing with {steps_limit} total steps...\n"
                            )
                        else:
                            self.console.print_info("Stopping at user request.")
                            break
                    except (EOFError, KeyboardInterrupt):
                        self.console.print_info("\nStopping at user request.")
                        break
                else:
                    # Silent mode - just stop
                    break

        # Cancelled mid-generation via the Agent UI Stop (#2157): end the turn
        # with empty text so it doesn't rehydrate as a completed answer and the
        # empty-answer classification (#2137/#2141) skips persistence. Returned
        # before the max-steps fallback, which would otherwise substitute a
        # non-empty "here's what I accomplished" message.
        if cancelled_by_console:
            logger.info("Agent run cancelled mid-generation via console.cancelled")
            self.last_result = {
                "status": "cancelled",
                "result": "",
                "system_prompt": self.system_prompt,
                "conversation": conversation,
                "steps_taken": steps_taken,
                "duration": time.time() - start_time,
                "error_count": len(self.error_history),
                "error_history": self.error_history,
            }
            # Returns before the tail seal below.
            self._finish_turn_record("", steps_taken)
            return self.last_result

        # Print completion message
        self.console.print_completion(steps_taken, steps_limit)

        # Calculate total duration
        total_duration = time.time() - start_time

        # Aggregate token counts from conversation stats, plus any usage a
        # tool self-reported (e.g. a triage tool's internal per-message LLM
        # calls, #2899) — same helper the pre-answer computation above uses,
        # reading identical inputs since nothing mutates conversation or
        # self._tool_reported_usage between the two calls.
        total_input_tokens, total_output_tokens = _sum_conversation_tokens(
            conversation, self._tool_reported_usage
        )

        # Return the result
        has_errors = len(self.error_history) > 0
        has_valid_answer = (
            final_answer and final_answer.strip()
        )  # Check for non-empty answer
        result = {
            "status": (
                "success"
                if has_valid_answer and not has_errors
                else ("failed" if has_errors else "incomplete")
            ),
            "result": (
                final_answer
                if final_answer
                else self._generate_max_steps_message(
                    conversation, steps_taken, steps_limit
                )
            ),
            "system_prompt": self.system_prompt,  # Include system prompt in the result
            "conversation": conversation,
            "steps_taken": steps_taken,
            "duration": total_duration,  # Total query processing time in seconds
            "input_tokens": total_input_tokens,  # Total input tokens across all steps
            "output_tokens": total_output_tokens,  # Total output tokens across all steps
            "total_tokens": total_input_tokens
            + total_output_tokens,  # Combined token count
            "error_count": len(self.error_history),
            "error_history": self.error_history,  # Include the full error history
        }

        # Write trace to file if requested
        if trace:
            file_path = self._write_json_to_file(result, filename)
            result["output_file"] = file_path

        logger.debug(f"Query processing complete: {result}")

        # Catches the exits that never printed an answer (max steps).
        turn_record = (
            self._finish_turn_record(result.get("result", ""), steps_taken)
            or turn_record
        )
        if turn_record is not None:
            result["turn_metrics"] = turn_record

        # Store the result internally
        self.last_result = result

        # Post-query hook for mixins (e.g., MemoryMixin conversation storage)
        if hasattr(self, "_after_process_query"):
            try:
                self._after_process_query(user_input, result.get("result", ""))
            except Exception as e:
                logger.warning(f"Post-query hook failed: {e}")

        return result

    @staticmethod
    def _is_error_result(result: Any) -> bool:
        """Canonical predicate for 'tool returned an error result'.

        Mirrors the error-recovery check used elsewhere in
        ``_process_query_impl`` (around lines 2319-2325 / 3471-3477) so
        subclass hooks and the framework's own error-recovery state
        transition agree on what counts as an error.
        """
        return isinstance(result, dict) and (
            result.get("status") == "error"
            or result.get("success") is False
            or result.get("has_errors") is True
            or result.get("return_code", 0) != 0
        )

    def _build_loop_break_summary(
        self,
        tool_name: str,
        consecutive_count: int,
        step_results: list,
    ) -> str:
        """Final-answer text when the loop breaks on repeats; honest on errors."""
        last = step_results[-1] if step_results else None
        if Agent._is_error_result(last):
            err = (last or {}).get("error") or "the tool returned an error"
            return (
                f"I tried calling `{tool_name}` {consecutive_count} times "
                f"and it kept failing: {err}\n\n"
                "I couldn't recover from this — please rephrase the request "
                "or check that the underlying service is running."
            )
        return f"Task completed with {tool_name}. No further action needed."

    def _dedup_mutation_call(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        mutation_call_cache: Dict[str, int],
        messages: List[Dict[str, Any]],
        tool_result: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Catch a repeated identical mutation at the FIRST repeat (#1317).

        Query dedup hashes the *result*; mutations must key on the *args*
        instead — re-issuing the same mutation (e.g. ``mark_read`` on an
        already-read id) returns a *different* result the second time, so a
        result hash would miss the repeat. Keying on ``(tool, normalized
        args)`` also leaves mutations on *different* ids untouched. Mirrors
        the query-dedup corrective signal: inject a re-plan prompt rather
        than silently dropping the call, so the model gets a chance to move
        on instead of waiting for the slow reactive loop-detector.
        """
        if tool_name not in _MUTATION_TOOLS:
            return
        # A mutation that ERRORED never took effect, so re-issuing it is a
        # legitimate retry — not a redundant repeat. Deduping it would inject
        # "the change is already applied — move on", falsely reporting success
        # and abandoning the operation (#2464: batch archive/star dead-ended
        # ~50% of the time when the model re-sent a spurious ``mailbox`` kwarg
        # the dispatcher rejects, and the 2nd rejection was mistaken for a
        # completed mutation). Only dedup calls that actually applied.
        if self._is_error_result(tool_result):
            return
        # Normalize so {"a":1,"b":2} and {"b":2,"a":1} hash identically.
        try:
            normalized = json.dumps(tool_args, sort_keys=True, default=str)
        except (TypeError, ValueError):
            normalized = str(tool_args)
        call_key = f"{tool_name}:{normalized}"
        mutation_call_cache[call_key] = mutation_call_cache.get(call_key, 0) + 1
        if mutation_call_cache[call_key] >= 2:
            logger.debug(
                "[DEDUP] Identical mutation %s issued %d times — injecting re-plan signal",
                tool_name,
                mutation_call_cache[call_key],
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"[SYSTEM] You already called {tool_name} with these exact "
                        f"arguments {mutation_call_cache[call_key]} times. The change "
                        "is already applied — repeating it has no effect. Move on to "
                        "the next item in your plan, or finish if nothing is left."
                    ),
                }
            )

    def _post_process_tool_result(
        self, _tool_name: str, _tool_args: Dict[str, Any], _tool_result: Dict[str, Any]
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Post-process the tool result for domain-specific handling.
        Override this in subclasses to provide domain-specific behavior.

        Args:
            _tool_name: Name of the tool that was executed
            _tool_args: Arguments that were passed to the tool
            _tool_result: Result returned by the tool

        Returns:
            ``None`` (default) — use the framework's current behaviour. In
            ``single_tool_per_turn=True`` mode the framework will mark the
            turn as done **only when the tool succeeded**. On error, the
            framework's own error-recovery prompt runs and the model gets
            a fresh planning turn.

            ``list[dict]`` — a multi-step plan the framework should execute
            via STATE_EXECUTING_PLAN (e.g., on a known prereq error: prepend
            an enable step and retry). Each step must have ``"tool"`` and
            ``"tool_args"`` keys.
        """
        if self.single_tool_per_turn and not self._is_error_result(_tool_result):
            self._single_tool_done = True
        return None

    def _inject_recovery_plan(self, steps: List[Dict[str, Any]]) -> None:
        """Switch the agent into ``STATE_EXECUTING_PLAN`` with the given steps.

        Controlled entry point so subclass hooks can request multi-step
        recovery without mutating four private attributes
        (``current_plan``/``current_step``/``total_plan_steps``/
        ``execution_state``) — which would violate the invariant that plan
        state transitions go through ``STATE_PLANNING``.
        Each ``step`` must be ``{"tool": <name>, "tool_args": <dict>}``.

        **Caller constraint:** safe to call from
        ``_post_process_tool_result`` when invoked from the sequential
        tool-execution path. Calling from inside an already-running
        ``STATE_EXECUTING_PLAN`` is supported but emits a WARNING because
        the new plan replaces the in-flight one, which is usually a bug
        in the subclass hook rather than intent. Fanout/native-batch
        paths haven't been exercised here — until they're tested, return
        ``None`` from those contexts.
        """
        if getattr(self, "execution_state", None) == getattr(
            self, "STATE_EXECUTING_PLAN", None
        ):
            logger.warning(
                "_inject_recovery_plan called while already in "
                "STATE_EXECUTING_PLAN — the in-flight plan will be replaced. "
                "Verify this is intentional in your subclass hook."
            )
        if not isinstance(steps, list) or not steps:
            return
        validated: List[Dict[str, Any]] = []
        for step in steps:
            if (
                isinstance(step, dict)
                and isinstance(step.get("tool"), str)
                and isinstance(step.get("tool_args", {}), dict)
            ):
                validated.append(
                    {
                        "tool": step["tool"],
                        "tool_args": step.get("tool_args", {}) or {},
                    }
                )
        if not validated:
            return
        self.current_plan = validated
        self.current_step = 0
        self.total_plan_steps = len(validated)
        self.execution_state = self.STATE_EXECUTING_PLAN
        self._single_tool_done = False
        logger.debug(
            "[_inject_recovery_plan] STATE_EXECUTING_PLAN with %d steps: %s",
            len(validated),
            [s["tool"] for s in validated],
        )

    def display_result(
        self,
        title: str = "Result",
        result: Dict[str, Any] = None,
        print_result: bool = False,
    ) -> None:
        """
        Display the result and output file path information.

        Args:
            title: Optional title for the result panel
            result: Optional result dictionary to display. If None, uses the last stored result.
            print_result: If True, print the result to the console
        """
        # Use the provided result or fall back to the last stored result
        display_result = result if result is not None else self.last_result

        if display_result is None:
            self.console.print_warning("No result available to display.")
            return

        # Print the full result with syntax highlighting
        if print_result:
            self.console.pretty_print_json(display_result, title)

        # If there's an output file, display its path after the result
        if "output_file" in display_result:
            self.console.print_info(
                f"Output written to: {display_result['output_file']}"
            )

    def get_error_history(self) -> List[str]:
        """
        Get the history of errors encountered by the agent.

        Returns:
            List of error messages
        """
        return self.error_history
