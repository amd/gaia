# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``CoderAgent`` — the conversational, tool-using gaia-coder agent.

This is the production replacement for the Phase-1 placeholder ``CoderAgent``
in :mod:`gaia.coder.base`. It composes the existing tool mixins
(:class:`gaia.coder.tools.FileToolsMixin`, :class:`CLIToolsMixin`,
:class:`SearchToolsMixin`, :class:`gaia.coder.tools.github.GitHubToolsMixin`),
holds a :class:`gaia.coder.llm.CoderLLM`, and drives a tool-use chat loop
against Claude.

Design choices:

1. **Composition over inheritance.** We do *not* inherit from the legacy
   :class:`gaia.coder.base.CoderAgent` (whose ``run_once`` raises
   ``NotImplementedError``). The placeholder served Phase 1 and is preserved
   for backwards compatibility with code that imports it.
   :class:`Agent` here is the real thing.

2. **Single tool-use loop.** :meth:`Agent.send` runs a bounded loop:
   send → assistant turn → run any tool calls → send tool results →
   loop. The loop terminates when the assistant returns ``stop_reason=
   "end_turn"`` or when ``max_iterations`` is exceeded (fail loudly).

3. **System prompt assembly.** The system prompt is built from
   ``GAIA.md`` (identity, principles, persona) + ``ARCHITECTURE.md``
   (composition map) + ``PROJECT_MAP.md`` (project map) + the bound
   repo's ``CLAUDE.md``/``AGENTS.md`` if present. This makes every turn
   project-aware out of the box — the agent knows it is editing
   ``amd/gaia``, knows the rules, and can read its own architecture.

4. **Tool permission policy.** A pluggable
   :class:`PermissionPolicy` callback gates every tool call. Default
   policy auto-approves read-only tools and prompts (or denies) for
   write/shell/network tools. The REPL passes a callback that asks the
   user; tests pass an auto-approve callback.

5. **Cost / usage telemetry.** Every turn's :class:`gaia.coder.llm.Usage`
   is accumulated into :attr:`Agent.session_usage`. The REPL renders it
   on `/cost`. Long sessions surface the running total.

This module is *not* the long-lived daemon — that is a separate
deliverable (Phase 2 of the C-roadmap). The agent here is "one
conversation": construct it, call :meth:`send` until you're done. The
REPL ties many sends together with persistence; one-shot users can call
:meth:`send` once and inspect the result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from gaia.coder.base import (
    ARCHITECTURE_MD_PATH,
    GAIA_MD_PATH,
    PROJECT_MAP_MD_PATH,
)
from gaia.coder.llm import AssistantTurn, CoderLLM, Usage
from gaia.coder.tool_schema import ToolDispatcher, ToolResult, build_anthropic_tools
from gaia.coder.tools import CLIToolsMixin, SearchToolsMixin

# NOTE: ``SearchToolsMixin`` already inherits from ``FileToolsMixin`` (see
# src/gaia/coder/tools/search.py:49) so we don't list FileToolsMixin in
# the bases — doing so would break the MRO.

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool / permission types
# ---------------------------------------------------------------------------

#: Permission policy callback signature.
#:
#: Receives ``(tool_name, tool_input)`` and returns one of:
#:
#: * ``None`` — approve.
#: * ``"deny: <reason>"`` — block this call. The model receives an
#:   ``is_error=True`` ``tool_result`` carrying the reason.
#: * ``"prompt"`` — the dispatcher should ask the user. (Implemented as a
#:   string sentinel rather than a separate enum so the policy callback
#:   can be a single function the REPL or a test can substitute.)
PermissionPolicy = Callable[[str, Mapping[str, Any]], Optional[str]]


#: Tools that mutate the filesystem, run subprocesses, or hit the network.
#: Default policy prompts before running these.
WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "write_file",
        "edit_file",
        "run_cli_command",
        "stop_process",
        "gh_pr_create",
        "gh_pr_comment",
        "gh_pr_merge",
        "gh_pr_review",
        "gh_issue_create",
        "gh_issue_comment",
        "gh_release_create",
    }
)

#: Tools that are always safe to run without prompting.
READ_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "search_code",
        "glob",
        "generate_diff",
        "grep",
        "find_symbol",
        "list_files",
        "list_processes",
        "get_process_logs",
        "semantic_search",
        "gh_pr_view",
        "gh_pr_list",
        "gh_pr_files",
        "gh_pr_diff",
        "gh_run_list",
        "gh_run_view",
        "gh_run_watch",
    }
)


def auto_approve_policy(_name: str, _input: Mapping[str, Any]) -> Optional[str]:
    """Default policy used in tests and ``--yes`` REPL mode: approve everything."""
    return None


def safe_default_policy(name: str, _input: Mapping[str, Any]) -> Optional[str]:
    """Default REPL policy: read-only auto, write tools prompt, unknown deny.

    Returns ``None`` for read-only tools, ``"prompt"`` for write tools, and
    a ``"deny: …"`` string for tools that are neither in :data:`READ_TOOLS`
    nor :data:`WRITE_TOOLS` (i.e. the caller did not classify them — fail
    loudly so the operator extends one of the lists rather than silently
    auto-approving).
    """
    if name in READ_TOOLS:
        return None
    if name in WRITE_TOOLS:
        return "prompt"
    return (
        f"deny: tool {name!r} is not in READ_TOOLS or WRITE_TOOLS — classify it first"
    )


# ---------------------------------------------------------------------------
# Conversation message types
# ---------------------------------------------------------------------------


@dataclass
class Message:
    """One message in the conversation history.

    Carries the Anthropic-shaped ``role`` and ``content``. ``content`` is
    either a plain string (user input) or a list of content blocks
    (assistant turn or tool-result reply).
    """

    role: str
    content: Any  # str | list[dict]


@dataclass
class SessionUsage:
    """Running token / cost totals across an entire session."""

    input_tokens: int = 0
    output_tokens: int = 0
    input_cost_usd: float = 0.0
    output_cost_usd: float = 0.0
    turns: int = 0

    def add(self, u: Usage) -> None:
        self.input_tokens += u.input_tokens
        self.output_tokens += u.output_tokens
        self.input_cost_usd = round(self.input_cost_usd + u.input_cost_usd, 6)
        self.output_cost_usd = round(self.output_cost_usd + u.output_cost_usd, 6)
        self.turns += 1

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_cost_usd(self) -> float:
        return round(self.input_cost_usd + self.output_cost_usd, 6)


@dataclass
class SendResult:
    """Result of one :meth:`Agent.send` call.

    Attributes:
        text: The final assistant text (the last ``end_turn`` reply).
        tool_calls: All tool calls executed across the inner loop, in
            order. Each is a (ToolUse, ToolResult) pair so the REPL can
            display both the request and the rendered result.
        usage: Sum of token / cost telemetry across every assistant turn
            in this send (typically ≥ 1 turn — at least one extra per
            tool round-trip).
        iterations: How many assistant turns the inner loop ran. Useful
            for debugging unbounded tool-use loops.
        stopped_early: True iff the loop terminated because
            ``max_iterations`` was exceeded rather than ``end_turn``.
    """

    text: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    usage: SessionUsage = field(default_factory=SessionUsage)
    iterations: int = 0
    stopped_early: bool = False


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class Agent(SearchToolsMixin, CLIToolsMixin):
    """The conversational, tool-using gaia-coder agent.

    ``Agent`` composes :class:`SearchToolsMixin` (which itself inherits
    :class:`FileToolsMixin` so the six file tools are available) and
    :class:`CLIToolsMixin`. ``GitHubToolsMixin`` is opt-in via
    ``include_github=True`` so a coder running outside a git repo or
    without ``gh`` installed does not crash at startup.

    Example::

        agent = Agent.from_defaults()
        result = agent.send("read README.md and summarise it")
        print(result.text)
        print("cost so far:", agent.session_usage.total_cost_usd)
    """

    DEFAULT_MAX_ITERATIONS: int = 30

    def __init__(
        self,
        *,
        llm: Optional[CoderLLM] = None,
        permission_policy: Optional[PermissionPolicy] = None,
        repo_root: Optional[Path] = None,
        include_github: bool = True,
        include_tools: Optional[Sequence[str]] = None,
        exclude_tools: Optional[Sequence[str]] = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        system_prompt_extra: Optional[str] = None,
    ) -> None:
        """Construct an agent.

        Args:
            llm: Pre-built :class:`CoderLLM`. When ``None`` (the default)
                a fresh one is constructed with default model + retries.
            permission_policy: Tool permission gate. Defaults to
                :func:`safe_default_policy` (auto-approve read tools,
                prompt for write tools, deny unknowns).
            repo_root: Bound repo root. Defaults to ``Path.cwd()``. Used
                to read ``CLAUDE.md`` / ``AGENTS.md`` for the system
                prompt and to set the working directory for tool calls.
            include_github: If True (default), register the 11 ``gh`` CLI
                tools. Set False to run without GitHub network access.
            include_tools: If supplied, restrict the toolbox to this set.
            exclude_tools: If supplied, omit these tools from the toolbox.
            max_iterations: Cap on inner-loop tool round-trips per
                :meth:`send` call. Default 30 — well above the typical
                3-8 round-trips a real task takes; enough headroom for
                deep refactors; fail-loud below the runaway threshold.
            system_prompt_extra: Optional extra text appended to the
                system prompt (e.g. project conventions injected by the
                REPL on `/system` reload).
        """
        self._llm = llm or CoderLLM()
        self._policy: PermissionPolicy = permission_policy or safe_default_policy
        self._repo_root = (repo_root or Path.cwd()).resolve()
        self._max_iterations = max_iterations
        self._system_prompt_extra = system_prompt_extra
        self._include_tools = list(include_tools) if include_tools else None
        self._exclude_tools = list(exclude_tools) if exclude_tools else None
        self._history: List[Message] = []
        self.session_usage = SessionUsage()

        # Register the tool mixins. The mixin methods populate the
        # global @tool registry, so registration is a side-effecting
        # call we make once at construction.
        self.register_file_tools()
        self.register_cli_tools()
        self.register_search_tools()
        if include_github:
            try:
                # Imported lazily so a coder without ``gh`` installed
                # doesn't pay the import cost when it boots without
                # GitHub tools.
                from gaia.coder.tools.github import GitHubToolsMixin  # noqa: WPS433

                # GitHubToolsMixin is a stand-alone mixin (does not
                # share state with FileToolsMixin etc.) so we instantiate
                # it transiently to register its tools, then drop it.
                gh = GitHubToolsMixin()
                gh.register_github_tools()
                logger.debug("GitHubToolsMixin registered")
            except ImportError as e:
                logger.warning(
                    "include_github=True but GitHubToolsMixin import failed (%s). "
                    "GitHub tools will be unavailable. Install `gh` and re-run.",
                    e,
                )

        self._dispatcher = ToolDispatcher(
            permission_check=self._dispatch_permission_check
        )
        logger.info(
            "CoderAgent ready (model=%s, repo_root=%s, max_iter=%d)",
            self._llm.model,
            self._repo_root,
            self._max_iterations,
        )

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_defaults(cls, **kwargs: Any) -> "Agent":
        """Construct with default settings. Equivalent to ``Agent(**kwargs)``."""
        return cls(**kwargs)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def llm(self) -> CoderLLM:
        return self._llm

    @property
    def repo_root(self) -> Path:
        return self._repo_root

    @property
    def history(self) -> List[Message]:
        """The full conversation history. Mutated by :meth:`send`."""
        return self._history

    def reset(self) -> None:
        """Clear conversation history and session totals (``/clear`` in REPL)."""
        self._history.clear()
        self.session_usage = SessionUsage()
        logger.info("CoderAgent.reset(): history + usage cleared")

    def system_prompt(self) -> str:
        """Build the system prompt sent on every turn.

        Sources, in order:

        1. ``GAIA.md`` — identity, principles, persona.
        2. ``ARCHITECTURE.md`` — composition map.
        3. ``PROJECT_MAP.md`` — bound-project map.
        4. ``<repo_root>/CLAUDE.md`` if it exists — repo-specific
           contributor rules.
        5. ``<repo_root>/AGENTS.md`` if it exists — agent-specific rules
           (the GitHub Actions canonical AI-agent file).
        6. ``self._system_prompt_extra`` if set.

        Each section is wrapped in an XML-ish tag so the model can
        unambiguously locate it. Missing files are skipped silently
        with a debug log — the prompt is still well-formed without them.
        """
        sections: List[str] = []
        for label, path in (
            ("identity", GAIA_MD_PATH),
            ("architecture", ARCHITECTURE_MD_PATH),
            ("project_map", PROJECT_MAP_MD_PATH),
            ("repo_claude_md", self._repo_root / "CLAUDE.md"),
            ("repo_agents_md", self._repo_root / "AGENTS.md"),
        ):
            try:
                text = Path(path).read_text(encoding="utf-8")
            except FileNotFoundError:
                logger.debug("system_prompt: %s not found at %s", label, path)
                continue
            sections.append(f"<{label}>\n{text.strip()}\n</{label}>")

        sections.append(
            "<runtime>\n"
            f"You are gaia-coder, the cloud coding agent for amd/gaia. You are\n"
            f"running in interactive mode at repo_root={self._repo_root}.\n"
            "Use the provided tools to read, search, edit, and run commands.\n"
            "Prefer surgical edits (edit_file) over full rewrites (write_file).\n"
            "Run tests after non-trivial changes. Cite file:line in answers.\n"
            "Refuse to push directly to main; integrate via the coder branch.\n"
            "</runtime>"
        )
        if self._system_prompt_extra:
            sections.append(f"<extra>\n{self._system_prompt_extra.strip()}\n</extra>")
        return "\n\n".join(sections)

    def send(
        self,
        message: str,
        *,
        max_iterations: Optional[int] = None,
        on_assistant_turn: Optional[Callable[[AssistantTurn], None]] = None,
        on_tool_call: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> SendResult:
        """Send ``message`` to the agent and run the tool-use loop to ``end_turn``.

        Args:
            message: User input.
            max_iterations: Override the agent default.
            on_assistant_turn: Optional callback invoked once per
                assistant turn (immediately after parsing the response).
                The REPL uses this to print incremental output.
            on_tool_call: Optional callback invoked once per dispatched
                tool call with a dict ``{"name": str, "input": …,
                "is_error": bool, "content": str}``. The REPL uses this
                to render tool calls inline.

        Returns:
            :class:`SendResult` with the final assistant text and a log
            of every tool call.

        Raises:
            RuntimeError: if the loop runs past ``max_iterations`` without
                terminating. Per fail-loudly we surface a clear error
                rather than truncating silently.
        """
        cap = max_iterations if max_iterations is not None else self._max_iterations
        self._history.append(Message(role="user", content=message))
        tools_payload = build_anthropic_tools(
            include=self._include_tools,
            exclude=self._exclude_tools,
        )
        system = self.system_prompt()
        result = SendResult(text="")

        for iteration in range(1, cap + 1):
            logger.debug(
                "CoderAgent.send: iteration %d/%d (history=%d msgs)",
                iteration,
                cap,
                len(self._history),
            )
            turn = self._llm.chat_with_tools(
                messages=self._to_anthropic_messages(),
                tools=tools_payload,
                system=system,
            )
            if on_assistant_turn is not None:
                on_assistant_turn(turn)
            self._history.append(
                Message(role="assistant", content=list(turn.raw_content))
            )
            self.session_usage.add(turn.usage)
            result.usage.add(turn.usage)
            result.iterations = iteration

            if not turn.tool_uses:
                # Model is done. Capture the final text and exit.
                result.text = turn.text
                logger.debug(
                    "CoderAgent.send: end_turn after %d iter (cost=$%.4f)",
                    iteration,
                    self.session_usage.total_cost_usd,
                )
                return result

            # Run every requested tool. Anthropic accepts multiple
            # tool_use blocks per turn; we run them sequentially in
            # the order returned (the model expects deterministic
            # ordering for its own bookkeeping).
            tool_results: List[ToolResult] = []
            for use in turn.tool_uses:
                logger.info(
                    "tool: %s(%s)",
                    use.name,
                    ", ".join(f"{k}={v!r}"[:80] for k, v in use.input.items()),
                )
                tr = self._dispatcher.run(
                    tool_use_id=use.id,
                    name=use.name,
                    tool_input=use.input,
                )
                tool_results.append(tr)
                call_record = {
                    "name": tr.name,
                    "input": dict(use.input),
                    "is_error": tr.is_error,
                    "content": tr.content,
                }
                result.tool_calls.append(call_record)
                if on_tool_call is not None:
                    on_tool_call(call_record)

            # Send the tool results back as a single user message
            # carrying every tool_result block in original order.
            self._history.append(
                Message(
                    role="user",
                    content=[tr.to_anthropic_block() for tr in tool_results],
                )
            )

        # Loop did not terminate on end_turn — fail loudly.
        result.stopped_early = True
        raise RuntimeError(
            f"CoderAgent.send: tool-use loop exceeded max_iterations={cap} "
            "without an end_turn — possible runaway. Inspect "
            f"agent.history for the last {cap} assistant turns."
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _to_anthropic_messages(self) -> List[Dict[str, Any]]:
        """Render :attr:`_history` as the Anthropic ``messages=…`` payload."""
        out: List[Dict[str, Any]] = []
        for msg in self._history:
            out.append({"role": msg.role, "content": msg.content})
        return out

    def _dispatch_permission_check(
        self,
        name: str,
        tool_input: Mapping[str, Any],
    ) -> Optional[str]:
        """Bridge :class:`ToolDispatcher` permission shape to our policy.

        :class:`ToolDispatcher` expects ``None`` (approve) or a string
        (deny + reason). The :data:`PermissionPolicy` returns the same,
        plus a ``"prompt"`` sentinel that the REPL handles by asking the
        user. By the time the call reaches the dispatcher, ``"prompt"``
        must already have been resolved — we therefore translate any
        leftover ``"prompt"`` to a hard deny so a missed prompt path
        cannot silently auto-approve a destructive tool.
        """
        verdict = self._policy(name, tool_input)
        if verdict is None:
            return None
        if verdict == "prompt":
            return (
                "deny: tool requires user approval, but the policy "
                "returned 'prompt' without an interactive resolver. "
                "(REPL must resolve prompts before dispatch.)"
            )
        return verdict


__all__ = [
    "Agent",
    "Message",
    "PermissionPolicy",
    "READ_TOOLS",
    "SendResult",
    "SessionUsage",
    "WRITE_TOOLS",
    "auto_approve_policy",
    "safe_default_policy",
]
