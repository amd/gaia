# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``GaiaAgent`` — the flagship general-purpose agent.

This is the agent a new user meets first: conversation, document Q&A over their
own files, data exploration, web research, and a memory that persists across
sessions — extended by skills rather than by shipping a new agent per task.

**It composes ``ChatAgent`` rather than forking it.** ChatAgent's ``doc`` profile
already carries the RAG prompt, smart document discovery, cross-turn session
persistence, memory v2, and MCP. Duplicating that to get a flagship would mean
maintaining two copies of the hardest-won prompt in the repo. What GaiaAgent adds
is *breadth*: the capability flags ChatAgent leaves off by default, plus a bundled
skill library.

Why breadth is a requirement and not a preference
-------------------------------------------------
``tools_required`` in a ``SKILL.md`` is **advisory** — the loader logs at INFO when
a declared tool is absent and loads the skill anyway. So a skill dropped into an
agent that lacks its tools does not fail at load; it fails mid-run when the model
calls a tool that was never registered. A general-purpose skill host therefore has
to carry the union of what its skills can ask for, or the failure surfaces to the
user as a broken answer instead of a clear refusal. The starter pack's needs map
directly onto the flags below:

    document-brief   -> RAG            (the ``doc`` prompt profile)
    data-explore     -> scratchpad     (``enable_scratchpad``)
    research-report  -> browser + file (``enable_browser`` + ``enable_filesystem``)
    check-in         -> memory         (on by default in the base agent)
    github-triage    -> MCP connector  (inherited from ChatAgent)

Skills are discovered from the bundled ``skills/`` directory (highest-precedence
root) and declared in ``gaia-agent.yaml``. Following the email agent's precedent
(#2848), **no skill set loads by default** — the manifest ships its
``default_skill_set`` commented out until an eval measures the prompt-token cost.
No sets are declared, so any ``GAIA_SKILL_SET`` stops startup. Task skills load
mid-session through the skill-library tools in
:mod:`gaia.agents.tools.skill_library_tools`, which let the model discover,
install, load, and unload skills on demand without a restart. Those
tools never load anything on their own, so the out-of-the-box prompt budget is
unchanged.
"""

from __future__ import annotations

import os
import sys
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Dict, FrozenSet, List, Optional, Set, Tuple

from gaiapy.chat_agent import ChatAgent, ChatAgentConfig
from gaiapy.connectors import MAILBOX_REQUIREMENTS
from gaiapy.engineering_tools import (
    ENGINEERING_SKILL,
    ENGINEERING_TOOL_NAMES,
    EngineeringToolsMixin,
)
from gaiapy.profiles import get_profile_spec
from gaiapy.research import (
    CITATION_REPAIR,
    DOWNLOAD_READ_TOOLS,
    RESEARCH_BRIEF,
    RESEARCH_SKILL,
    RESEARCH_TOOLS,
    WEB_CALLS_KEPT,
    WEB_RECORD_TOOLS,
    citation_problems,
    downloaded_path,
    file_read_path,
    research_brief,
    research_message,
    split_research_command,
)

from gaia.agents.base.agent import default_max_steps, parse_skill_manifest
from gaia.agents.base.history import context_window
from gaia.agents.base.project_map import ProjectMapMixin, is_code_repository
from gaia.agents.base.skill_catalog import catalog_env_override
from gaia.agents.base.skill_loader import (
    DEFAULT_SKILL_THRESHOLD,
    SkillLoader,
    dynamic_skills_env_override,
)
from gaia.agents.base.skill_suggester import (
    AUTOLOAD_ENV,
    DEFAULT_AUTOLOAD_MARGIN,
    DEFAULT_AUTOLOAD_THRESHOLD,
    DEFAULT_SUGGEST_THRESHOLD,
    SkillMatches,
    SkillSuggester,
    autoload_env_override,
    autoload_margin_env_override,
    autoload_threshold_env_override,
    render_autoloaded,
    render_suggestion,
    suggest_env_override,
)
from gaia.agents.base.text_task import instruction_part
from gaia.agents.base.tool_grants import BROWSER_PANE_SKILL
from gaia.agents.registry import DEFAULT_EMBEDDING_MODEL
from gaia.agents.tools.code_index_tools import CodeIndexToolsMixin
from gaia.agents.tools.email_tools import EmailToolsMixin
from gaia.agents.tools.skill_learning_tools import SkillLearningToolsMixin
from gaia.agents.tools.skill_library_tools import (
    SkillLibraryToolsMixin,
    uncaptured_skill_path,
)
from gaia.config import GaiaConfig
from gaia.connectors.providers.base import ConnectorRequirement
from gaia.llm.hybrid import (
    HybridModelError,
    HybridRouter,
    RouteDecision,
    explain_route_failure,
    is_hybrid_model,
)
from gaia.logger import get_logger
from gaia.project_context import project_context_setting
from gaia.skills.errors import SkillSetError

logger = get_logger(__name__)


#: Bundled skills ship inside the package so they survive both the wheel and the
#: frozen sidecar; as ``SKILL_DIRS`` they outrank a same-named user or Claude Code copy.
_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

#: Answer-check problem when the user named a skill folder and nothing captured it.
_SKILL_NOT_ADDED = "No skill was added from"

# Rendered in a code repository for a model that gets the full project context
# (gaia.project_context). General software practice, not any one repo's rules:
# those come from its CLAUDE.md/AGENTS.md and its .claude/skills.
REPOSITORY_WORK_PROMPT = """**WORKING IN A CODE REPOSITORY**
You are working in the code repository at the current directory. Work like a careful senior engineer:
- Follow the project's own instructions (CLAUDE.md / AGENTS.md) over your habits. They are already in this prompt under PROJECT INSTRUCTIONS: do not read those files again. Before a task one of the project's skills covers (code review, issue replies, releases, testing...), load that skill and follow it.
- Ground every claim in code you have read in this session. Before saying something exists, is missing, or appears N times, search the WHOLE repository (every top-level folder, not only src/) and count the hits; say which files you checked. If you could not verify something, say so instead of guessing.
- Read with intent: search for the symbol, then read the lines around it. Read a large file in ranges; never assume what you did not read.
- Trace a change across files: who calls it, what tests cover it, what docs or configs describe it, what the inverse path does (create vs. delete, install vs. uninstall, start vs. stop).
- Reviews and audits: go through the diff or files systematically and keep going after the first few findings. Check that every command, path, flag, setting and file a change introduces or documents (docs, test plans, scripts, error messages) really exists and would work as written. Report only defects you can show from the code, with file:line and a concrete failure scenario; most severe first. Match severity to evidence: block or escalate only for defects that would break users or are real security holes, not for missing polish or hypotheticals.
- When you change code: find the root cause and fix it for every input it affects, not only the reported example; look for the same bug at other call sites; key the fix on the real condition, not a heuristic stand-in for it (keyword matching, special-casing one path or value). Keep the change focused, match the surrounding style, add or update tests for the behaviour, and run only the tests that cover what you changed (one file or test, not the whole suite) before you say it works. Say exactly what you ran and its result. When you only read, review or write prose, do not run tests unless one specific claim needs it.
- Output: produce exactly the format the task asks for, written to the file it names. Lead with the finding or verdict; no preamble, no thanks, no restating the task.
- Before you finish, re-read the task's requirements and the project's writing rules and check your draft against each one: right format, required sections present, length within limits, nothing the rules forbid. Fix what fails, then deliver."""

#: This file's parent folders in a source checkout, nearest first.
_CHECKOUT_LAYOUT = ("gaiapy", "python", "gaia", "agents", "hub")


def _checkout_skills_dir(agent_file: Path) -> Optional[Path]:
    """``hub/skills`` when *agent_file* sits at hub/agents/gaia/python/gaiapy/.

    A bundled root's code runs without ``gaia skill promote``, and an installed
    package sits at no fixed depth, so a bare ``parents[4]`` would trust
    whatever ``skills/`` folder lies beside the environment.
    """
    parents = list(agent_file.parents)[: len(_CHECKOUT_LAYOUT)]
    if tuple(p.name for p in parents) != _CHECKOUT_LAYOUT:
        return None
    return parents[-1] / "skills"


#: The starter pack's canonical home, for a source checkout only.
#:
#: Packaging stages the pack into ``_SKILLS_DIR``; in a checkout that directory
#: holds just a ``.gitkeep``, so without this the agent discovers NO skills and
#: "load the github-triage skill" fails on a tree that visibly contains it.
#: ``None`` outside a checkout. Frozen builds use _SKILLS_DIR alone, since the
#: freeze already bundles the pack there as --add-data.
_HUB_SKILLS_DIR: Optional[Path] = (
    _SKILLS_DIR
    if getattr(sys, "frozen", False)
    else _checkout_skills_dir(Path(__file__).resolve())
)


def _bundled_skill_roots() -> List[str]:
    """Existing bundled-skill roots, highest precedence first.

    Mirrors ``_MANIFEST_CANDIDATES``: the packaged location wins, and the
    source-checkout location stands in when the package was never staged. Both
    are returned when both exist, so a staged copy shadows the checkout rather
    than the two disagreeing silently.

    Discovery only. Bundling a skill makes it *loadable on request*; it does not
    load it. The prompt-budget trade this agent has deliberately not taken is
    ``default_skill_set`` (below in gaia-agent.yaml), which is what costs tokens
    by loading skill bodies into every prompt. That stays commented out, so the
    out-of-the-box prompt is byte-identical — this only means that when a user
    asks for a skill by name, it is there to load.
    """
    roots: List[str] = []
    for directory in (_SKILLS_DIR, _HUB_SKILLS_DIR):
        if directory is None:
            continue
        path = str(directory)
        if directory.is_dir() and path not in roots:
            roots.append(path)
    return roots


_MANIFEST_CANDIDATES = (
    # Packaged: staged into the package (frozen sidecar --add-data, wheel package-data).
    Path(__file__).resolve().parent / "gaia-agent.yaml",
    # Source checkout / editable install: the canonical hub artifact.
    Path(__file__).resolve().parent.parent / "gaia-agent.yaml",
)

#: Env override for the active skill set, mirroring the email agent's channel.
SKILL_SET_ENV = "GAIA_SKILL_SET"

#: Env override for the fast conversational path (#4103).
FAST_ENV = "GAIA_FAST"

#: The profile a fast session composes as: the only spec with no tool groups.
_FAST_PROFILE = "chat"


def fast_env_override() -> Optional[bool]:
    """Parse ``GAIA_FAST``, or ``None`` when it is unset.

    Same truthy set as ``GAIA_DYNAMIC_TOOLS`` and ``GAIA_SKILL_DISCOVERY`` so a
    user who has learned one of this agent's switches has learned all of them.
    """
    raw = os.getenv(FAST_ENV)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


#: The flagship's step limit when nothing overrides it: a backstop for a runaway
#: turn, high enough that real work never reaches it. ``GAIA_AGENT_MAX_STEPS``
#: and an explicit ``max_steps`` still win.
FLAGSHIP_MAX_STEPS = 1000


def flagship_max_steps() -> int:
    """``GAIA_AGENT_MAX_STEPS`` if set, else :data:`FLAGSHIP_MAX_STEPS`."""
    return default_max_steps(FLAGSHIP_MAX_STEPS)


def _locate_agent_manifest() -> Optional[str]:
    """Absolute path to this package's ``gaia-agent.yaml``, or ``None``.

    Returning ``None`` rather than raising keeps an unpackaged checkout usable:
    the agent still runs, it just has no declarative skill sets. A *missing but
    declared* manifest is what the base class treats as an error.
    """
    for candidate in _MANIFEST_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return None


@dataclass
class GaiaAgentConfig(ChatAgentConfig):
    """Flagship defaults: ChatAgent's ``doc`` profile with the breadth flags on.

    Every field here exists on :class:`ChatAgentConfig` already — this only
    changes defaults, so anything ChatAgent accepts still works.
    """

    # "full" — NOT "doc". This is the load-bearing line of the whole package.
    # ChatAgent registers tools from ``ProfileSpec.tool_groups``, which is keyed
    # on the profile alone; the ``enable_*`` flags below do NOT feed tool
    # registration. Setting profile="doc" with all three flags on yields RAG and
    # files but ZERO scratchpad and ZERO browser tools — measured, not assumed —
    # so data-explore and research-report would load and then die mid-run.
    # "full" is the only spec whose tool_groups are the union this agent needs:
    # doc_rag + file_fs + data_scratch + web_browse + full_screenshot.
    prompt_profile: str = "full"

    # The id Settings › Connectors activates MCP servers for, so the flagship
    # offers a server's tools only once it is activated, however it was built.
    namespaced_agent_id: Optional[str] = "installed:gaia"

    # None (the CLI's "not given") also resolves to the flagship limit.
    max_steps: Optional[int] = field(default_factory=flagship_max_steps)

    # Kept explicit even though "full" already implies them: these gate mixin
    # *construction* (indexes, DB handles, HTTP session) in __init__, separately
    # from the profile's tool registration.
    enable_filesystem: bool = True
    enable_scratchpad: bool = True
    enable_browser: bool = True

    # Qwen3.6's template re-renders earlier steps with their reasoning; dropping
    # it at the next request re-reads the whole previous tool turn uncached.
    resend_reasoning_across_requests: bool = True

    # Which declared skill set to load. None = load nothing (the #2848 default);
    # resolution order is explicit arg -> env -> manifest default.
    skill_set: Optional[str] = None

    # Explicit opt-in, independent of diagnostic --dev output. Inherited by WebUI.
    developer_mode: bool = field(
        default_factory=lambda: os.environ.get("GAIA_DEVELOPER_MODE") == "1"
    )

    # Lazy skill-body activation (#2848 follow-up): per-turn semantic
    # selection of which LOADED skill's body actually renders, instead of
    # every loaded skill's body riding along on every turn for the life of
    # the session. On by default for this agent specifically — it is the one
    # that measurably bleeds prompt budget on this (64.8% of the prompt with
    # two skills loaded, #2848). Overridable via GAIA_DYNAMIC_SKILLS.
    dynamic_skills: bool = True
    dynamic_skills_threshold: float = DEFAULT_SKILL_THRESHOLD

    # Start on the core tools in short form and move to every tool at most once,
    # so a first turn reads far less and the backend's prompt cache breaks at most
    # once a session. Follows ``dynamic_tools`` in ~/.gaia/config.json. Overridable
    # via GAIA_DYNAMIC_TOOLS.
    dynamic_tools: bool = field(default_factory=lambda: GaiaConfig.load().dynamic_tools)

    # List every installed skill in the system prompt so the model loads one
    # when the work fits, and the user never has to know a skill's name. On for
    # this agent specifically — it ships a skill library and meets users who
    # have never read it. Overridable via GAIA_SKILL_DISCOVERY.
    skill_discovery: bool = True

    # Name the catalogue skills that match each request in one line of the
    # turn's context, so a small local model is not left to pick from the whole
    # list — the step it skips. Suggestion only: the model still calls
    # load_skill. Needs the catalogue on. Overridable via GAIA_SKILL_SUGGEST.
    skill_suggestions: bool = True
    skill_suggestion_threshold: float = DEFAULT_SUGGEST_THRESHOLD

    # Load a suggested skill before the turn when it scores far above the
    # suggestion floor and clearly ahead of every skill it does not pair with:
    # local models often ignore a correct suggestion. Opt-in until the floor
    # and lead are measured on the current labelled requests. Needs suggestions
    # on. Overridable via GAIA_SKILL_AUTOLOAD, GAIA_SKILL_AUTOLOAD_TAU (floor)
    # and GAIA_SKILL_AUTOLOAD_MARGIN (lead).
    skill_autoload: bool = False
    skill_autoload_threshold: float = DEFAULT_AUTOLOAD_THRESHOLD
    skill_autoload_margin: float = DEFAULT_AUTOLOAD_MARGIN

    # On for the flagship only. It does pull a second resident model and evict
    # the chat model — a cost a document agent should not pay silently, so
    # ChatAgent keeps it off — but this is the general-purpose surface, and off
    # here means "draw me a picture" has no answer at all. The image-gen skill
    # carries the eviction cost into the procedure.
    enable_sd_tools: bool = True
    # Deliberately opt-in: TRELLIS swaps a large model and needs a dedicated
    # hardware/eval lane before it belongs in the flagship roster.
    enable_3d_tools: bool = False
    enable_audio_generation_tools: bool = True

    rag_documents: List[str] = field(default_factory=list)

    # ChatAgent defaults this to ``[Path.cwd()]``, which is wrong for a sidecar:
    # the daemon launches it with cwd = the package directory, so the agent ends
    # up sandboxed to its own source tree and refuses to read the user's files.
    # Measured: "read ~/Documents/notes.txt" fails with "not in allowed paths".
    #
    # The user's home is the honest scope for a personal document agent — it is
    # what "ask questions about my files" means — and it stays a real boundary
    # (system directories, other users, and program files are still refused).
    # Override with ``allowed_paths=[...]``, or set GAIA_ALLOWED_PATHS, to narrow it.
    allowed_paths: Optional[List[str]] = field(
        default_factory=lambda: [str(Path.home())]
    )

    # The project this task is about (#3379). ``None`` resolves through
    # ``GAIA_PROJECT_ROOT``, then the working directory or the nearest
    # repository above it — and stays ``None`` when neither is a repository,
    # which is the common case for a sidecar launched from its package dir.
    project_root: Optional[str] = None

    # Build the semantic code index at task start when the project is a
    # repository and has none. Off by default: the first search_code_index
    # builds it instead, so a task that never searches never pays for an embed
    # pass over the whole repository. ``GAIA_PROJECT_MAP_AUTO_INDEX=1`` opts in.
    auto_index: bool = False

    # The fast conversational path (#4103). Trades this agent's whole tool
    # surface for the prefill of a plain chat agent, for a session that is only
    # ever going to be conversation. Session-scoped and one-way: a fast session
    # has no documents, no files, no web and no skills, and cannot acquire them
    # mid-run — start an ordinary session for that. Overridable via GAIA_FAST.
    #
    # Everything it switches off is derived in ``_apply_fast_mode``; the field
    # itself only records the choice, so a caller reading back the config can
    # tell a fast session from one that happens to be narrow.
    fast: bool = False

    # How much of the repository's CLAUDE.md, AGENTS.md and .claude/skills the
    # prompt carries: "full", "off", or "auto" to decide per model. None reads
    # GAIA_PROJECT_CONTEXT, then ``project_context`` in ~/.gaia/config.json.
    project_context: Optional[str] = None


def _code_search_start(allowed: List[str]) -> str:
    """Where code search starts when the session is not inside a project.

    ``allowed_paths`` also holds attached document FILES and GAIA's own
    documents folder, which may not exist yet; taking the first entry started
    code search in that folder and every search failed with "repo_path does not
    exist". A repository wins, then any folder that exists.
    """
    folders = [p for p in allowed if Path(p).is_dir()]
    repos = [p for p in folders if is_code_repository(p)]
    # Empty only when GAIA_ALLOWED_PATHS left nothing in scope; ceilings refuse all.
    return (repos or folders or allowed or [os.getcwd()])[0]


def _apply_fast_mode(config: GaiaAgentConfig) -> None:
    """Rewrite *config* in place into the fast conversational path (#4103).

    Composing as the ``chat`` profile is the whole mechanism: it is the one
    ``ProfileSpec`` with no ``tool_groups``, so ChatAgent registers a bare
    conversational surface and every downstream read of ``prompt_profile``
    already behaves. Nothing here is a special case for fast mode.

    The rest is removing what would otherwise be paid for and never used:

    * The ``enable_*`` flags add prompt TEXT, never tools. Left on, a fast
      session would describe filesystem, scratchpad and browser tooling it
      cannot call — the worst of both, tokens spent inviting a tool call that
      fails.
    * Dynamic tool and skill selection are per-turn choosers over a surface
      this session does not have. Turning skill selection off also pins
      ``gaia-voice``: it is loaded, and a greeting is exactly the turn a
      semantic chooser would score too low to render, dropping the persona.
    * ``auto_index`` would embed a whole repository in the background for a
      ``search_code_index`` that is not registered.
    """
    config.prompt_profile = _FAST_PROFILE
    config.enable_filesystem = False
    config.enable_scratchpad = False
    config.enable_browser = False
    config.enable_sd_tools = False
    config.enable_3d_tools = False
    config.enable_audio_generation_tools = False
    config.dynamic_tools = False
    config.dynamic_skills = False
    config.skill_discovery = False
    config.skill_suggestions = False
    # skill_autoload stays as set: the agent warns that fast mode leaves it off.
    config.auto_index = False


# ``ProjectMapMixin`` is the one exception to "base agent first": it overrides
# ``_on_task_start`` and calls ``super()``, and ``Agent``'s no-op default sits
# ahead of every trailing mixin in the MRO — listed after ChatAgent it would
# never run. The tool mixins keep their usual place at the back, where none
# overrides anything and a future method cannot silently win over ChatAgent's.
class GaiaAgent(
    ProjectMapMixin,
    EngineeringToolsMixin,
    ChatAgent,
    SkillLibraryToolsMixin,
    SkillLearningToolsMixin,
    CodeIndexToolsMixin,
    EmailToolsMixin,
):
    """The flagship GAIA agent — conversation, documents, data, web, and skills."""

    SKILL_DIRS: ClassVar[List[str]] = _bundled_skill_roots()
    SKILL_MANIFEST: ClassVar[Optional[str]] = _locate_agent_manifest()

    # Loaded skill bodies share ~5% of the model's window (their tools stay
    # listed): on a 64K local model, two or three full skill bodies took each
    # request from ~23K to ~40K tokens and instruction following dropped.
    SKILL_CONTEXT_BUDGET: ClassVar[bool] = True

    # Declared, not acquired: the user consents once via `gaia connectors`, and
    # nothing here reaches a mailbox until an email tool is actually called.
    REQUIRED_CONNECTORS: ClassVar[List[ConnectorRequirement]] = list(
        MAILBOX_REQUIREMENTS
    )

    # Installing/capturing a skill writes third-party content under
    # ~/.gaia/skills and removing one deletes it, so all three are gated the way
    # file mutation is. capture_skill additionally feeds pasted/fetched text
    # into the system prompt — never without the human seeing the request.
    # remember_skill_lesson deliberately is not: it writes only to this agent's
    # own memory, applies at once, announces itself, and undoes in one command.
    CONFIRMATION_REQUIRED_TOOLS: ClassVar[frozenset] = frozenset(
        {"install_skill", "capture_skill", "remove_skill"}
    )
    # The calendar and send-email skills ship in this agent's bundled skills.
    BUNDLES_CALENDAR_SKILL: ClassVar[bool] = True
    BUNDLES_SEND_EMAIL_SKILL: ClassVar[bool] = True

    def __init__(self, config: Optional[GaiaAgentConfig] = None, **kwargs):
        if config is not None and kwargs:
            raise TypeError(
                "GaiaAgent takes either a ready GaiaAgentConfig or the config "
                f"fields as keyword arguments, not both. Got config= plus "
                f"{sorted(kwargs)}; those keywords would be silently dropped. "
                "Set them on the config object, or drop the config= argument."
            )
        resolved = config or GaiaAgentConfig(**kwargs)
        if resolved.max_steps is None:
            resolved.max_steps = flagship_max_steps()
        # ``GAIA_FAST`` wins in both directions, so a launcher that hard-codes
        # fast=True is still overridable from the shell. Written back to the
        # field so ``config.fast`` never disagrees with how the agent was built.
        override = fast_env_override()
        resolved.fast = override if override is not None else bool(resolved.fast)
        # Resolved now so a bad value fails at startup, not mid-prompt.
        resolved.project_context = project_context_setting(resolved.project_context)
        if resolved.fast:
            _apply_fast_mode(resolved)
            logger.info(
                "[gaia] fast mode: conversational profile only — no documents, "
                "files, web or skills this session"
            )
        self._research_brief: Optional[str] = None
        self._web_calls: deque = deque(maxlen=WEB_CALLS_KEPT)
        self._turn_web_calls: List[Tuple[str, Any, Any]] = []
        router = None
        requested = resolved.model_id
        if requested is None and not resolved.use_claude:
            requested = GaiaConfig.load().default_model
        if is_hybrid_model(requested):
            router = HybridRouter()
            # Built on the cheap model; each turn's route switches as needed.
            resolved.model_id = router.settings.cheap_model
        super().__init__(config=resolved)
        #: Picks each turn's model when the session runs on ``hybrid``.
        self.hybrid_router: Optional[HybridRouter] = router
        self.last_route: Optional[RouteDecision] = None
        if self.config.developer_mode:
            self.load_skill(ENGINEERING_SKILL)
            self._start_engineering_setup()

    def process_query(self, user_input, *args, **kwargs):
        """Run a turn; on ``hybrid``, first move to the model its route picks.

        A ``/research`` turn first loads the research skill. The topic becomes
        the user's message, so memory and history keep what the user asked;
        the research brief rides in the turn's context.
        """
        route = self._route_turn(user_input)
        result = self._run_turn(user_input, *args, **kwargs)
        if route is not None and isinstance(result, dict):
            result["route"] = route.as_record()
        return result

    def _run_turn(self, user_input, *args, **kwargs):
        self._turn_web_calls = []
        split = split_research_command(user_input)
        if split is None:
            return super().process_query(user_input, *args, **kwargs)
        notes, topic = split
        self._start_research_turn()
        self._research_brief = research_brief(topic)
        try:
            return super().process_query(
                research_message(notes, topic), *args, **kwargs
            )
        finally:
            self._research_brief = None

    def _route_turn(self, user_input: Any) -> Optional[RouteDecision]:
        router = self.hybrid_router
        if router is None:
            return None
        # A turn whose route can't be decided must not report the last one's.
        self.last_route = None
        route = router.decide(
            user_input if isinstance(user_input, str) else "",
            base_url=getattr(self.chat.config, "base_url", None),
        )
        self.last_route = route
        if route.notice:
            self.console.print_warning(route.notice)
        if route.model != self.model_id:
            from gaiapy.stdio import switch_concrete_model

            # Raises with the model's own remedy; never runs the other model.
            try:
                switch_concrete_model(self, route.model)
            except Exception as exc:
                explained = explain_route_failure(router.settings, route.model, exc)
                if explained is None:
                    raise
                raise HybridModelError(explained) from exc
        router.ran(route)
        return route

    def reset_hybrid_route(self) -> None:
        """A new conversation starts unescalated, routed afresh."""
        if self.hybrid_router is not None:
            self.hybrid_router.reset()
        self.last_route = None

    def _cloud_account_refusal(self, exc: BaseException) -> Optional[str]:
        """The base refusal, naming the hybrid setting that chose the model."""
        refusal = super()._cloud_account_refusal(exc)
        router, route = self.hybrid_router, self.last_route
        if refusal is None or router is None or route is None:
            return refusal
        return explain_route_failure(router.settings, route.model, refusal) or refusal

    @property
    def skill_manager(self):
        """Exclude the developer skill before metadata discovery in normal mode,
        and the browser-pane skill in a chat with no pane to drive."""
        if getattr(self, "_skill_manager", None) is None:
            from gaia.skills import SkillManager

            excluded = () if self.config.developer_mode else (ENGINEERING_SKILL,)
            if not self._browser_pane_attached:
                excluded += (BROWSER_PANE_SKILL,)
            # The project's .claude/skills join the catalogue, as in Claude Code.
            self._skill_manager = SkillManager(
                agent_skill_dirs=[*self.SKILL_DIRS, *self._bundled_skill_dirs()],
                excluded_names=excluded,
                project_dir=self._project_map_root(),
            )
        return self._skill_manager

    def load_skill(self, name, *, manager=None):
        # Also covers an explicitly supplied manager or a restored manifest.
        if name == ENGINEERING_SKILL and not self.config.developer_mode:
            raise PermissionError("The engineering skill requires --developer-mode.")
        return super().load_skill(name, manager=manager)

    def get_repository_work_system_prompt(self) -> str:
        """How to work inside a code repository, for a model that gets it.

        Keyed on the mapped project root, which already excludes GAIA's own
        source (the dev sidecar's cwd) and folders outside GAIA_ALLOWED_PATHS.
        Byte-stable for the session (prompt cache).
        """
        # Fast mode is the conversational path with no tools to work a repo with.
        if self.config.fast or not self._project_map_root():
            return ""
        if not self._full_project_context():
            return ""
        return REPOSITORY_WORK_PROMPT

    @property
    def _always_on_skill_names(self):
        names = super()._always_on_skill_names
        return names | {ENGINEERING_SKILL} if self.config.developer_mode else names

    def _execute_tool_timed(self, tool_name, tool_args):
        result = super()._execute_tool_timed(tool_name, tool_args)
        # What a research report's citations are checked against.
        if tool_name in WEB_RECORD_TOOLS or (
            tool_name in DOWNLOAD_READ_TOOLS
            and any(
                name == "download_file"
                and downloaded_path(saved) == file_read_path(tool_args)
                for name, _args, saved in self._web_calls
            )
        ):
            self._web_calls.append((tool_name, tool_args, result))
            self._turn_web_calls.append((tool_name, tool_args, result))
        return result

    def answer_problems(self, answer):
        problems = super().answer_problems(answer)
        request = getattr(self, "_original_user_input", None) or ""
        if self._research_brief != RESEARCH_BRIEF:
            called = {r.get("tool") for r in self._turn_tool_executions}
            path = (
                uncaptured_skill_path(str(request), called)
                if "capture_skill" in self._tools_registry
                else ""
            )
            if path:
                problems.append(f"{_SKILL_NOT_ADDED} `{path}`.")
            return problems
        return problems + citation_problems(
            answer or "",
            list(self._web_calls),
            str(request),
            turn_calls=self._turn_web_calls,
        )

    def required_calls(self):
        calls = list(super().required_calls())
        if "capture_skill" not in self._tools_registry:
            return calls
        called = {r.get("tool") for r in self._turn_tool_executions}
        request = str(getattr(self, "_original_user_input", None) or "")
        path = uncaptured_skill_path(request, called)
        if path:
            # capture_skill still asks the user before it copies anything.
            calls.append(("capture_skill", {"source": path}))
        return calls

    def answer_repair_prompt(self, problems):
        if self._research_brief != RESEARCH_BRIEF:
            prompt = super().answer_repair_prompt(problems)
            if any(p.startswith(_SKILL_NOT_ADDED) for p in problems):
                prompt += (
                    " Call capture_skill with source set to that path now; it "
                    "asks the user and reports whether the skill was added."
                )
            return prompt
        return CITATION_REPAIR.format(problems=" ".join(problems))

    def _start_research_turn(self) -> None:
        """Load ``research-report`` and keep its instructions in this turn's prompt.

        Raises:
            RuntimeError: this agent has no web tools, or the skill would not load.
        """
        from gaia.skills.errors import SkillError

        missing = [t for t in RESEARCH_TOOLS if t not in self.get_tools_info()]
        if missing:
            raise RuntimeError(
                "Research needs web search and page reading, and this agent has "
                f"no {', '.join(missing)} tool. Start a regular GAIA chat to "
                "research a topic."
            )
        already = RESEARCH_SKILL in self.loaded_skills
        if not already:
            try:
                self.load_skill(RESEARCH_SKILL)
            except SkillError as exc:
                raise RuntimeError(
                    f"Research needs the {RESEARCH_SKILL} skill, and it could "
                    f"not be loaded: {exc}"
                ) from exc
        # The per-turn selector must not collapse the body the user asked for.
        self._pin_skill_body(RESEARCH_SKILL)
        self._announce_skill(RESEARCH_SKILL, "loaded" if already else "auto")
        self.rebuild_system_prompt()

    def close(self, *, wait_for_mcp: bool = True) -> None:
        """Release this agent's watchers, HTTP session and SQLite handles now.

        ``ChatAgent`` puts that teardown in ``__del__``, which for an instance
        whose tool registry holds bound methods only runs when the cyclic
        collector gets to it — far too late for a sidecar that builds an agent
        per one-shot request. Every step is individually guarded and idempotent,
        so the later ``__del__`` is harmless. MCP servers are stopped here and
        waited for, so they are gone when ``close()`` returns; with
        ``wait_for_mcp=False`` they stop on a background thread instead, for a
        caller that something else is waiting on.
        """
        manager = getattr(self, "_mcp_manager", None)
        if manager is not None and wait_for_mcp:
            try:
                manager.disconnect_all()
            except Exception as e:  # noqa: BLE001 — logged; the rest still runs
                logger.error("Error stopping MCP servers during close: %s", e)
        self.__del__()

    def _register_tools(self) -> None:
        """ChatAgent's profile tools, plus runtime access to the skill library.

        Skill-library tools go first: ChatAgent's registration ends with
        ``_snapshot_tools()``, and anything registered after that snapshot is
        absent from this instance's registry. Code-index and email tools join
        them for the same reason.

        Semantic code search is what makes this agent usable ON a codebase
        rather than merely in one: grep finds a string, this finds the function
        that does the thing you described.

        The skill loader is built here — before ``super()._register_tools()``,
        which is what triggers ``load_declared_skills()`` at the end of
        ``Agent.__init__`` — so ``_select_skills_for_turn`` never sees a
        ``None`` loader while skills are already loading. ``self._embed_text``
        (MemoryMixin) and ``self._embed_texts_batch`` (ChatAgent) are only
        *referenced* here, not called, so this needs no embedder/Lemonade
        access at construction time — only the first real turn does.
        """
        engineering_tools = (
            self.register_engineering_tools() if self.config.developer_mode else {}
        )
        self.skill_loader = self._maybe_build_skill_loader()
        self._skill_catalog_enabled = self._resolve_skill_catalog_enabled()
        self.skill_suggester = self._maybe_build_skill_suggester()
        if self._profile_registers_tools():
            self.register_skill_library_tools()
            # Adaptive skills (#2674): lets the agent propose a correction to a
            # loaded skill that does not fit. It only ever stages one — activating
            # it is the user's own step through `gaia skill deltas --approve`.
            self.register_skill_learning_tools()
            # The project map's root when there is one, so "is the index built?"
            # and "index it" both mean the repository the task is about. Falling
            # back to allowed_paths for the same reason that field rejects cwd: the
            # daemon launches this sidecar with cwd = the package directory, so cwd
            # would sandbox code search to the agent's own source tree.
            allowed = getattr(self.config, "allowed_paths", None)
            if allowed is None:
                allowed = [str(Path.home())]
            # Through the mixin, so both read the one cached resolution and can
            # never end up describing two different trees.
            index_root = self._project_map_root() or _code_search_start(allowed)
            # The project root is where code search STARTS; allowed_paths is how far
            # it may reach. Passing one value for both locked a session that began
            # inside a repo to that repo (#3544).
            self._init_code_index_state(repo_path=index_root, ceiling_paths=allowed)
            self.register_code_index_tools()
            self.register_email_tools()
        super()._register_tools()
        if engineering_tools:
            # Keep developer closures out of the process-global registry even
            # transiently: another WebUI agent may be constructing concurrently.
            self._instance_tools = {**self._tools_registry, **engineering_tools}

    def _profile_registers_tools(self) -> bool:
        """False on a profile whose spec registers no tool groups (#4103).

        ChatAgent returns early on such a profile — ``chat`` is the only one —
        and this agent's own extras have to return early with it. Registering
        17 more tools onto a surface the profile deliberately left bare is what
        made ``prompt_profile="chat"`` cost 6.3K prefill instead of 2.2K.
        """
        profile = getattr(self.config, "prompt_profile", "full")
        return not get_profile_spec(profile).early_return

    def _workspace_core_tools(self) -> FrozenSet[str]:
        """The engineering tools in developer mode, and the shell in a repository.

        Coding requests rarely read like shell requests ("skip these tests on
        PRs"), so semantic selection left a repo session without a shell even
        though the project map tells the model which commands it accepts.
        """
        tools = set(ENGINEERING_TOOL_NAMES) if self.config.developer_mode else set()
        root = self._project_map_root()
        if root and is_code_repository(root):
            tools.add("run_shell_command")
        return frozenset(tools)

    # ── lazy skill-body loader (#2848 follow-up) ────────────────────────────

    def _resolve_skill_catalog_enabled(self) -> bool:
        """Whether the skill catalogue is shown: env wins over config."""
        override = catalog_env_override()
        if override is not None:
            return override
        return bool(getattr(self.config, "skill_discovery", False))

    def _skill_autoload_requested(self) -> bool:
        """Whether auto-load was asked for: ``GAIA_SKILL_AUTOLOAD`` wins over config."""
        override = autoload_env_override()
        if override is not None:
            return override
        return bool(getattr(self.config, "skill_autoload", False))

    def _warn_autoload_inactive(self, reason: str) -> None:
        """Say, once per agent, that a requested auto-load cannot run and why."""
        if getattr(self, "_autoload_inactive_warned", False):
            return
        self._autoload_inactive_warned = True
        logger.warning(
            "[skills] auto-load was asked for (%s or skill_autoload) but is "
            "inactive: %s",
            AUTOLOAD_ENV,
            reason,
        )

    def _maybe_build_skill_suggester(self) -> Optional[SkillSuggester]:
        """The per-request skill suggester, or ``None`` when it is off.

        Off with the catalogue: a suggestion names catalogue skills, and with
        no catalogue there is nothing for it to point into. An auto-load that
        was asked for and cannot run is reported here, once.
        """
        requested = self._skill_autoload_requested()
        if requested and getattr(self.config, "fast", False):
            self._warn_autoload_inactive("fast mode runs without skills.")
        if not self._skill_catalog_enabled:
            if requested:
                self._warn_autoload_inactive(
                    "the skill catalogue is off (GAIA_SKILL_DISCOVERY or "
                    "skill_discovery), so there is nothing to match."
                )
            return None
        override = suggest_env_override()
        enabled = (
            override
            if override is not None
            else bool(getattr(self.config, "skill_suggestions", True))
        )
        if not enabled:
            if requested:
                self._warn_autoload_inactive(
                    "skill suggestions are off (GAIA_SKILL_SUGGEST or "
                    "skill_suggestions), and auto-load picks from them."
                )
            return None
        return SkillSuggester(
            embed_fn=self._embed_text,
            embed_batch_fn=self._embed_texts_batch,
            threshold=float(
                getattr(
                    self.config,
                    "skill_suggestion_threshold",
                    DEFAULT_SUGGEST_THRESHOLD,
                )
            ),
            autoload_threshold=self._resolve_skill_autoload_threshold(),
            autoload_margin=self._resolve_skill_autoload_margin(),
        )

    def _resolve_skill_autoload_threshold(self) -> Optional[float]:
        """The auto-load floor, or ``None`` when auto-load is off.

        ``GAIA_SKILL_AUTOLOAD_TAU`` wins over the config field; a malformed
        value fails loudly. Off in fast mode and on any embedder but the one
        the floor was measured with, whatever the setting says.
        """
        if not self._skill_autoload_requested():
            return None
        if getattr(self.config, "fast", False):
            self._warn_autoload_inactive("fast mode runs without skills.")
            return None
        embedder = self._active_embedding_model()
        if embedder != DEFAULT_EMBEDDING_MODEL:
            self._warn_autoload_inactive(
                f"its threshold is calibrated for {DEFAULT_EMBEDDING_MODEL}, not "
                f"the active embedder {embedder}. Matching skills are still "
                "suggested."
            )
            return None
        if os.environ.get("GAIA_MEMORY_DISABLED") == "1":
            self._warn_autoload_inactive(
                "memory is off (GAIA_MEMORY_DISABLED=1), and skill matching "
                "uses its embedder."
            )
        override = autoload_threshold_env_override()
        if override is not None:
            return override
        return float(
            getattr(self.config, "skill_autoload_threshold", DEFAULT_AUTOLOAD_THRESHOLD)
        )

    def _resolve_skill_autoload_margin(self) -> float:
        """The auto-load lead: ``GAIA_SKILL_AUTOLOAD_MARGIN`` wins over config."""
        override = autoload_margin_env_override()
        if override is not None:
            return override
        return float(
            getattr(self.config, "skill_autoload_margin", DEFAULT_AUTOLOAD_MARGIN)
        )

    #: Share of the model's context window the skill bodies rendering this turn
    #: may fill for an auto-load to go ahead: ~5,900 tokens of the default 32,768.
    #: Only with ``SKILL_CONTEXT_BUDGET`` off; with it on, that budget decides.
    SKILL_AUTOLOAD_CONTEXT_SHARE: ClassVar[float] = 0.18

    #: Lowest hub tier whose skills load without the model asking.
    SKILL_AUTOLOAD_MIN_TIER: ClassVar[str] = "community"

    def _skill_suggester_for_turn(self, user_input: str) -> Optional[SkillSuggester]:
        """The suggester, or ``None`` when *user_input* gets no skill matching."""
        suggester = getattr(self, "skill_suggester", None)
        if suggester is None:
            return None
        if getattr(self, "_memory_store", None) is None:
            if getattr(suggester, "autoload_threshold", None) is not None:
                self._warn_autoload_inactive(
                    "the memory store is unavailable, and skill matching uses "
                    "its embedder."
                )
            logger.debug("[skills] no suggestions: memory store (embedder) is off")
            return None
        # Text handed over to translate or reword needs no skill, whatever it says.
        if instruction_part(user_input) != (user_input or ""):
            return None
        return suggester

    def _select_skill_suggestions_for_turn(self, user_input: str) -> List[str]:
        """Unloaded catalogue skills that clearly match *user_input*.

        The current message alone, unlike skill-body selection: a suggestion is
        about what this request needs, and the previous turn's skills are
        already loaded or were already declined. Empty when the suggester is
        off, or memory is off (the same embedder backs both).
        """
        suggester = self._skill_suggester_for_turn(user_input)
        if suggester is None:
            return []
        return suggester.suggest(
            user_input,
            self._catalog_skills(),
            loaded=self._skills_in_use(user_input),
            list_project=self._lists_project_skills(),
        )

    def _skills_in_use(self, user_input: str) -> FrozenSet[str]:
        """Loaded skills, plus one *user_input* runs as ``/<skill-name> ...``.

        The command loads its skill after the turn's context is built, so
        without it the hint would name the skill the user just typed.
        """
        from gaia.skills.command import parse_skill_command

        command = parse_skill_command(user_input or "")
        typed = {command.name} if command is not None else set()
        return frozenset(self.loaded_skills) | typed

    def _select_skill_matches_for_turn(self, user_input: str) -> SkillMatches:
        """This turn's matches, split into auto-loads and suggestions.

        With auto-load off this is :meth:`_select_skill_suggestions_for_turn`
        and nothing to load.
        """
        suggester = self._skill_suggester_for_turn(user_input)
        if suggester is None:
            return SkillMatches()
        if getattr(suggester, "autoload_threshold", None) is None:
            return SkillMatches(
                suggest=self._select_skill_suggestions_for_turn(user_input)
            )
        return suggester.select(
            user_input,
            self._catalog_skills(),
            loaded=self._skills_in_use(user_input),
            list_project=self._lists_project_skills(),
        )

    def _session_skill_set(self, attr: str) -> Set[str]:
        """The per-session name set stored on *attr*, created on first use."""
        names = getattr(self, attr, None)
        if names is None:
            names = set()
            setattr(self, attr, names)
        return names

    def _autoload_grants(self) -> Dict[str, List[str]]:
        """Skills auto-loaded under full access, and the programs each granted."""
        granted = getattr(self, "_autoload_granted", None)
        if granted is None:
            granted = self._autoload_granted = {}
        return granted

    def unload_skill(self, name: str) -> bool:
        """Unload *name* and remember it, so auto-load never brings it back."""
        unloaded = super().unload_skill(name)
        if unloaded:
            self._session_skill_set("_unloaded_skills").add(name)
            self._autoload_grants().pop(name, None)
        return unloaded

    def _autoload_distrust(self, skill: Any) -> str:
        """Why *skill* may not load without the model asking, or ``""``.

        Trusted: a skill bundled with the agent, and a hub install whose lock
        entry is at :attr:`SKILL_AUTOLOAD_MIN_TIER` or above. A captured or
        hand-written skill and anything from a ``.claude`` folder was never
        through the install checks, so the model has to ask for it.

        Raises:
            SkillError: the lock beside the skill cannot be read.
        """
        from gaia.skills.capture import capture_entry
        from gaia.skills.lock import SOURCE_HUB, SkillLock
        from gaia.skills.manager import CLAUDE_ROOTS, ROOT_AGENT_BUNDLED
        from gaia.skills.tiers import LOWEST_TIER, tier_rank

        if skill.root == ROOT_AGENT_BUNDLED:
            return ""
        if skill.root in CLAUDE_ROOTS:
            if skill.gaia.tools:
                return (
                    "it is a .claude skill that ships tools, which load_skill refuses"
                )
            return "it comes from a .claude folder, not from GAIA or the hub"
        if capture_entry(skill) is not None:
            return "it was captured, not installed from the hub"
        directory = getattr(skill, "directory", None)
        entry = (
            SkillLock.load(directory.parent).get(skill.name)
            if directory is not None
            else None
        )
        if entry is None or entry.source != SOURCE_HUB:
            return "skill-lock.json does not record it as a hub install"
        tier = entry.installed_tier or LOWEST_TIER
        if tier_rank(tier) < tier_rank(self.SKILL_AUTOLOAD_MIN_TIER):
            return (
                f"it was installed at the '{tier}' tier, below "
                f"'{self.SKILL_AUTOLOAD_MIN_TIER}'"
            )
        return ""

    def _rendering_skill_body_tokens(self, user_input: str) -> int:
        """Estimated tokens of the loaded skill bodies that render this turn.

        Every loaded body when per-turn selection is off or its embedder is
        down. Otherwise only the ones this request selects, the pinned ones and
        the always-on ones: a body collapsed to its one-line reminder costs
        next to nothing, and counting it would block auto-load for the rest of
        a long session.
        """
        from gaia.agents.tools.skill_library_tools import estimate_prompt_tokens

        loaded = self.loaded_skills
        rendering = set(loaded)
        if rendering and self._dynamic_skills_active():
            selected = self._select_skills_for_turn(user_input)
            if selected is not None:
                pinned = set(getattr(self, "_sticky_skill_turns", None) or ())
                rendering &= set(selected) | pinned | self._always_on_skill_names
        return sum(estimate_prompt_tokens(loaded[n].body or "") for n in rendering)

    def _autoload_skills(
        self, names: List[str], user_input: str = ""
    ) -> Tuple[List[str], List[str]]:
        """Load *names* before the turn; return ``(loaded, left to suggest)``.

        Loads as the ``load_skill`` tool would, and pins each body so this
        turn's selection cannot hide it. A skill is left as a suggestion, never
        loaded, when:

        * it was unloaded earlier this session, by anyone;
        * it is not from a trusted root (:meth:`_autoload_distrust`);
        * it would grant a command-line program and full access is off: the
          shell then runs that program without a prompt, so the model has to
          ask for it. Under full access everything is pre-approved already, so
          it loads, with a warning naming what it pre-approves;
        * its body would push the bodies rendering this turn past the skill
          body budget (:mod:`gaia.agents.base.skill_budget`) when
          :attr:`SKILL_CONTEXT_BUDGET` is on, else past
          :attr:`SKILL_AUTOLOAD_CONTEXT_SHARE` of the context window;
        * a window of 64K or less already auto-loaded its one skill this turn
          (skill context budget only);
        * loading it fails — logged as a warning, and the model's own
          ``load_skill`` call then reports why.

        The window and full access are read on every call, never cached: both
        can change between turns.
        """
        if not names:
            return [], []
        from gaia.agents.base.skill_budget import (
            SMALL_WINDOW_AUTOLOADS,
            body_tokens,
            is_small_window,
            window_budget,
        )
        from gaia.skills.binaries import binary_permissions
        from gaia.skills.errors import SkillError

        if self.SKILL_CONTEXT_BUDGET:
            window, _source, budget = window_budget(self)
            most = SMALL_WINDOW_AUTOLOADS if is_small_window(window) else len(names)
        else:
            window = context_window(self)
            budget = int(window * self.SKILL_AUTOLOAD_CONTEXT_SHARE)
            most = len(names)
        used = self._rendering_skill_body_tokens(user_input)
        full_access = self.full_access_active()
        done = self._session_skill_set("_autoloaded_skills")
        unloaded = self._session_skill_set("_unloaded_skills")
        loaded: List[str] = []
        left: List[str] = []

        def suggest_instead(name: str, why: str) -> None:
            logger.info(
                "[skills] suggesting '%s' instead of auto-loading it: %s", name, why
            )
            left.append(name)

        for name in names:
            if name in self.loaded_skills:
                continue
            if len(loaded) >= most:
                suggest_instead(
                    name, f"a {window}-token window auto-loads {most} skill per turn"
                )
                continue
            if name in unloaded or name in done:
                # The model or user dropped it; bringing it back is their call.
                suggest_instead(name, "it was unloaded earlier this session")
                continue
            try:
                # Discovery reads frontmatter only; the body sets the cost.
                skill = self.skill_manager.load(name)
                programs = sorted(
                    {
                        str(p.scope)
                        for p in binary_permissions(skill.parsed_permissions())
                    }
                )
                distrust = self._autoload_distrust(skill)
            except SkillError as exc:
                logger.warning("[skills] not auto-loading '%s': %s", name, exc)
                left.append(name)
                continue
            if distrust:
                suggest_instead(name, distrust)
                continue
            if programs and not full_access:
                suggest_instead(
                    name, f"it would grant {', '.join(programs)} without a prompt"
                )
                continue
            cost = body_tokens(skill)
            if used + cost > budget:
                logger.warning(
                    "[skills] suggesting '%s' instead of auto-loading it: its "
                    "~%d-token body on top of the ~%d tokens of skill bodies "
                    "already rendering is over the %d-token budget (%s-token "
                    "context window)",
                    name,
                    cost,
                    used,
                    budget,
                    window,
                )
                left.append(name)
                continue
            try:
                self.load_skill(name)
            except (SkillError, PermissionError) as exc:
                logger.warning("[skills] could not auto-load '%s': %s", name, exc)
                left.append(name)
                continue
            if programs:
                self._autoload_grants()[name] = programs
                logger.warning(
                    "[skills] auto-loaded '%s' under full access: it pre-approves "
                    "%s, whose allowed subcommands run without a prompt",
                    name,
                    ", ".join(programs),
                )
            self._announce_skill(name, "auto")
            self._pin_skill_body(name)
            done.add(name)
            used += cost
            loaded.append(name)
        if loaded:
            logger.info("[skills] auto-loaded for this request: %s", ", ".join(loaded))
        return loaded, left

    def _withdraw_autoload_grants(self) -> None:
        """Unload skills whose program grant only full access let auto-load.

        Run before each turn. With full access off again, nobody agreed to the
        programs those skills pre-approve, so the skills go and their grants
        with them. The model can still ask for one with ``load_skill``.
        """
        granted = self._autoload_grants()
        if not granted or self.full_access_active():
            return
        for name, programs in sorted(granted.items()):
            if name not in self.loaded_skills:
                continue
            logger.warning(
                "[skills] unloading '%s': full access is off, and it was "
                "auto-loaded with a grant for %s only because full access was on",
                name,
                ", ".join(programs),
            )
            self.unload_skill(name)
        granted.clear()

    def _prepare_turn_context(self, user_input: str) -> None:
        """Load this turn's auto-load matches before its context is built.

        The outcome waits on ``_turn_skill_lines`` for
        :meth:`get_memory_dynamic_context`, which only reports it: that getter
        is also called to inspect a turn, and inspecting must not load a skill.
        """
        super()._prepare_turn_context(user_input)
        self._turn_skill_lines = None
        self._withdraw_autoload_grants()
        matches = self._select_skill_matches_for_turn(user_input)
        auto, refused = self._autoload_skills(matches.auto_load, user_input)
        self._turn_skill_lines = (user_input, auto, refused + matches.suggest)

    def get_memory_dynamic_context(self) -> str:
        """Per-turn context, plus the skills loaded for or fitting this request.

        Reports what :meth:`_prepare_turn_context` loaded and left to suggest.
        Asked about a request no turn was prepared for, it matches without
        loading, and every match is a suggestion.

        In the user message, not the system prompt: a line that changes from
        turn to turn anywhere in the system prompt makes the server re-read the
        whole conversation (see ``test_prompt_prefix_stability.py``).
        """
        query = getattr(self, "_memory_turn_query", None) or ""
        prepared = getattr(self, "_turn_skill_lines", None)
        self._turn_skill_lines = None
        if prepared is not None and prepared[0] == query:
            _, auto, suggested = prepared
        else:
            matches = self._select_skill_matches_for_turn(query)
            auto, suggested = [], matches.auto_load + matches.suggest
        parts = (
            super().get_memory_dynamic_context(),
            getattr(self, "_research_brief", None),
            render_autoloaded(auto),
            render_suggestion(suggested, after_autoload=bool(auto)),
        )
        return "\n".join(part for part in parts if part)

    def _maybe_build_skill_loader(self) -> Optional[SkillLoader]:
        """Construct the per-turn skill-body selector, or ``None`` when off."""
        if not self._resolve_dynamic_skills_enabled():
            return None
        return SkillLoader(
            embed_fn=self._embed_text,
            embed_batch_fn=self._embed_texts_batch,
            threshold=self._resolve_dynamic_skills_threshold(),
        )

    def _resolve_dynamic_skills_enabled(self) -> bool:
        """Toggle: ``GAIA_DYNAMIC_SKILLS`` (truthy) wins over the config field."""
        override = dynamic_skills_env_override()
        if override is not None:
            return override
        return bool(getattr(self.config, "dynamic_skills", True))

    def _resolve_dynamic_skills_threshold(self) -> float:
        """Threshold: ``GAIA_DYNAMIC_SKILLS_TAU`` wins; malformed value fails loudly."""
        raw = os.environ.get("GAIA_DYNAMIC_SKILLS_TAU")
        if raw is None:
            return float(
                getattr(
                    self.config, "dynamic_skills_threshold", DEFAULT_SKILL_THRESHOLD
                )
            )
        try:
            return float(raw)
        except ValueError as e:
            raise ValueError(
                f"GAIA_DYNAMIC_SKILLS_TAU must be a float, got {raw!r}"
            ) from e

    def _dynamic_skills_active(self) -> bool:
        """True when per-turn skill-body selection should run this turn.

        Off (→ every loaded skill's body renders every turn, the legacy/base
        behavior): loader not built (toggle off), the loader disabled itself
        after an embedder failure, or memory is off (``GAIA_MEMORY_DISABLED``
        tears down ``_memory_store``, and the same embedder backs both).
        """
        active = (
            self.skill_loader is not None
            and not self.skill_loader.session_disabled
            and getattr(self, "_memory_store", None) is not None
        )
        if (
            not active
            and self.skill_loader is not None
            and not self.skill_loader.session_disabled
        ):
            # Memory off silently reverts to full-body prompts otherwise —
            # say so once per turn at INFO so a bloated prompt is explicable.
            logger.info(
                "[skills] dynamic per-turn selection off: memory store is "
                "disabled, every loaded skill's body renders in full"
            )
        return active

    def _standing_skill_tools(self) -> List[str]:
        """The active skills' tools, plus ``remember_skill_lesson`` when a skill
        is loaded.

        Semantic selection cannot rank this tool. Someone correcting a skill
        talks about their meeting brief, not about skills, so the query never
        resembles the ``skills`` bundle the tool lives in — the same ranking
        blind spot that moved the file-edit tools to CORE (#3752) and
        ``load_skill`` to proactive discovery (#3235). Left to semantics it is
        absent on exactly the turn it exists for, and the model answers from
        the bundle menu's prose instead: asked to fix a transcript skill's
        output format it reported the skill "has been updated on your machine"
        having called nothing at all.

        Conditional rather than CORE, because the flagship ships with no skills
        and a tool that can only refuse is prompt tax. It joins the list like any
        skill's tool, never as a jump, and is absent on every off-state, so a
        build with nothing loaded — or with learning switched off — stays
        byte-identical.
        """
        tools = super()._standing_skill_tools()
        if not getattr(self, "loaded_skills", None):
            return tools
        enabled = getattr(self, "learned_skills_enabled", None)
        if callable(enabled) and not enabled():
            return tools
        if "remember_skill_lesson" not in tools:
            tools.append("remember_skill_lesson")
        return tools

    def _drop_skill_tools(self, names: List[str]) -> None:
        """Take ``remember_skill_lesson`` off too when the last skill leaves."""
        if not self.loaded_skills:
            names = [*names, "remember_skill_lesson"]
        super()._drop_skill_tools(names)

    def _select_skills_for_turn(self, user_input: str) -> Optional[List[str]]:
        """This turn's active skill-body subset, or ``None`` for "render all".

        Reuses ChatAgent's ``_build_tool_selection_query`` (previous + current
        user message) so a short follow-up ("also check the linked PR") still
        matches on the prior turn's context, not just its own few words.
        """
        if not self._dynamic_skills_active():
            if self.skill_loader is not None:
                # No scores this turn: an earlier turn's must not rank bodies.
                self.skill_loader.last_scores = {}
            return None
        query = self._build_tool_selection_query(user_input)
        return self.skill_loader.select(
            query, self.loaded_skills, always_on=self._always_on_skill_names
        )

    def _skill_relevance(self) -> dict:
        """This turn's score per loaded skill, which ranks bodies over budget.

        Empty when selection is off or did not score this turn; the budget
        then ranks by explicit loads and load order alone.
        """
        loader = getattr(self, "skill_loader", None)
        return dict(loader.last_scores) if loader is not None else {}

    def select_skill_set(self) -> Optional[str]:
        """Resolve which declared skill set to load at startup.

        Explicit config wins, then ``GAIA_SKILL_SET``, then the manifest default
        (which ships unset). Returning ``None`` means load no skills — the base
        class treats that as a deliberate choice, not a missing value.
        """
        name, _origin = _user_skill_set(self.config)
        return name

    def load_declared_skills(self, manifest_path=None, *, manager=None):
        """Load declared skills, refusing a user-named set even with no manifest.

        The base returns ``{}`` early when no manifest is found, before
        :meth:`load_skill_set` can reject the config/env name — so an unpackaged
        checkout would silently drop ``GAIA_SKILL_SET`` that the transport
        pre-check already rejects.
        """
        if manifest_path is None and _user_skill_set(self.config)[0] is not None:
            return self.load_skill_set(manager=manager)
        return super().load_declared_skills(manifest_path, manager=manager)

    def load_skill_set(self, requested: Optional[str] = None, *, manager=None):
        """Load skills, treating the config/env skill set as an explicit request.

        The base class consults :meth:`select_skill_set` only when the manifest
        declares sets, so a user-supplied name would be dropped silently while
        none are declared. Passing it as *requested* makes every undeclared name
        raise, naming the valid sets.
        """
        if requested is not None or (self._requested_skill_set or "").strip():
            return super().load_skill_set(requested, manager=manager)
        name, origin = _user_skill_set(self.config)
        try:
            return super().load_skill_set(name, manager=manager)
        except SkillSetError as exc:
            if name is None:
                raise
            raise SkillSetError(_bad_skill_set_message(name, origin, exc)) from exc


def _user_skill_set(config: Optional[GaiaAgentConfig]) -> Tuple[Optional[str], str]:
    """The skill set the user asked for and where it came from, or ``None``."""
    explicit = (getattr(config, "skill_set", None) or "").strip()
    if explicit:
        return explicit, "GaiaAgentConfig.skill_set"
    from_env = (os.environ.get(SKILL_SET_ENV) or "").strip()
    if from_env:
        return from_env, SKILL_SET_ENV
    return None, ""


def _bad_skill_set_message(name: str, origin: str, exc: SkillSetError) -> str:
    return (
        f"{origin}={name!r} cannot be used: {exc} Unset {origin} to start "
        "GAIA without a skill set."
    )


def check_skill_set_selection(config: Optional[GaiaAgentConfig] = None) -> None:
    """Fail now if the requested skill set is not one this agent declares.

    Cheap — it reads only the manifest, not the model or the tool stack — so a
    transport can call it before it binds a port or opens its wire. Resolution
    goes through the same :class:`~gaia.skills.sets.SkillSets` the agent uses,
    so the two can never disagree about what is valid.

    Raises:
        SkillSetError: ``GAIA_SKILL_SET`` (or ``config.skill_set``) names a set
            the manifest does not declare; the message lists the valid sets.
    """
    name, origin = _user_skill_set(config)
    if name is None:
        return
    declarations = parse_skill_manifest(GaiaAgent.SKILL_MANIFEST)
    try:
        declarations.resolve(requested=name)
    except SkillSetError as exc:
        raise SkillSetError(_bad_skill_set_message(name, origin, exc)) from exc


__all__ = [
    "GaiaAgent",
    "GaiaAgentConfig",
    "SKILL_SET_ENV",
    "check_skill_set_selection",
    "FAST_ENV",
    "fast_env_override",
]
