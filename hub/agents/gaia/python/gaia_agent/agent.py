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
Skills are opt-in via ``--skill-set``, ``GAIA_SKILL_SET``, or — mid-session — the
skill-library tools in :mod:`gaia.agents.tools.skill_library_tools`, which let the model
discover, install, load, and unload skills on demand without a restart. Those
tools never load anything on their own, so the out-of-the-box prompt budget is
unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, List, Optional

from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

from gaia.agents.base.skill_discovery import (
    DISCOVERY_THRESHOLD_ENV,
    SkillDiscovery,
    discovery_env_override,
)
from gaia.agents.base.skill_loader import (
    DEFAULT_SKILL_THRESHOLD,
    SkillLoader,
    dynamic_skills_env_override,
)
from gaia.agents.tools.code_index_tools import CodeIndexToolsMixin
from gaia.agents.tools.skill_learning_tools import SkillLearningToolsMixin
from gaia.agents.tools.skill_library_tools import SkillLibraryToolsMixin
from gaia.logger import get_logger

logger = get_logger(__name__)


#: Bundled skills ship inside the package so they survive both the wheel and the
#: frozen sidecar; as ``SKILL_DIRS`` they outrank a same-named user or Claude Code copy.
_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

#: The starter pack's canonical home, for a source checkout only.
#:
#: Packaging stages the pack into ``_SKILLS_DIR``; in a checkout that directory
#: holds just a ``.gitkeep``, so without this the agent discovers NO skills and
#: "load the github-triage skill" fails on a tree that visibly contains it.
#: hub/agents/gaia/python/gaia_agent/agent.py -> parents[4] is hub/.
_HUB_SKILLS_DIR = Path(__file__).resolve().parents[4] / "skills"


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
    return [str(d) for d in (_SKILLS_DIR, _HUB_SKILLS_DIR) if d.is_dir()]


_MANIFEST_CANDIDATES = (
    # Packaged: staged into the package (frozen sidecar --add-data, wheel package-data).
    Path(__file__).resolve().parent / "gaia-agent.yaml",
    # Source checkout / editable install: the canonical hub artifact.
    Path(__file__).resolve().parent.parent / "gaia-agent.yaml",
)

#: Env override for the active skill set, mirroring the email agent's channel.
SKILL_SET_ENV = "GAIA_SKILL_SET"


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

    # Kept explicit even though "full" already implies them: these gate mixin
    # *construction* (indexes, DB handles, HTTP session) in __init__, separately
    # from the profile's tool registration.
    enable_filesystem: bool = True
    enable_scratchpad: bool = True
    enable_browser: bool = True

    # Which declared skill set to load. None = load nothing (the #2848 default);
    # resolution order is explicit arg -> env -> manifest default.
    skill_set: Optional[str] = None

    # Lazy skill-body activation (#2848 follow-up): per-turn semantic
    # selection of which LOADED skill's body actually renders, instead of
    # every loaded skill's body riding along on every turn for the life of
    # the session. On by default for this agent specifically — it is the one
    # that measurably bleeds prompt budget on this (64.8% of the prompt with
    # two skills loaded, #2848). Overridable via GAIA_DYNAMIC_SKILLS.
    dynamic_skills: bool = True
    dynamic_skills_threshold: float = DEFAULT_SKILL_THRESHOLD

    # Per-turn semantic tool selection. On by default for this agent
    # specifically: breadth is its whole point, and breadth is what makes the
    # un-trimmed native ``tools=`` payload cost ~10.2K tiktoken tokens on every
    # LLM call of a 2-5 call ReAct turn — 60% of the fixed prefill a 4B model
    # re-reads each step. ChatAgent keeps dynamic_tools=False; no other profile
    # pays a 66-tool registry. Overridable via GAIA_DYNAMIC_TOOLS.
    dynamic_tools: bool = True

    # 12 CORE (FULL_CORE_TOOLS) + 14 dynamic slots. The inherited 14 was sized
    # for the doc profile's 11 CORE, leaving 3 slots — less than one 6-member
    # bundle, so the flagship would truncate a cohesion group mid-pull instead
    # of loading it. Swept offline against nine representative queries: 22 cut
    # the web bundle in half on a research question, 26 lands every matched
    # bundle whole, and 30 buys nothing further. Costs ~4.2K tiktoken tokens of
    # tools= against 10.5K for the whole registry.
    dynamic_tools_max: int = 26

    # Proactive skill discovery: match each turn against skills that are
    # INSTALLED BUT NOT LOADED and activate the winner, so the user never has
    # to know a skill's name. On for this agent specifically — it is the one
    # that ships a skill library and meets users who have never read it.
    # Overridable via GAIA_SKILL_DISCOVERY.
    skill_discovery: bool = True
    skill_discovery_threshold: Optional[float] = None

    # Image generation stays off: it pulls a second resident model, and evicting
    # the chat model to draw a picture is not a trade a document agent should
    # make silently.
    enable_sd_tools: bool = False

    rag_documents: List[str] = field(default_factory=list)

    # ChatAgent defaults this to ``[Path.cwd()]``, which is wrong for a sidecar:
    # the daemon launches it with cwd = the package directory, so the agent ends
    # up sandboxed to its own source tree and refuses to read the user's files.
    # Measured: "read ~/Documents/notes.txt" fails with "not in allowed paths".
    #
    # The user's home is the honest scope for a personal document agent — it is
    # what "ask questions about my files" means — and it stays a real boundary
    # (system directories, other users, and program files are still refused).
    # Override with ``allowed_paths=[...]`` to narrow it.
    allowed_paths: Optional[List[str]] = field(
        default_factory=lambda: [str(Path.home())]
    )


# Base agent first, tool mixins after — the repo's MRO convention for every
# hub agent. Neither mixin overrides anything today; this order keeps a future
# mixin method from silently winning over ChatAgent's.
class GaiaAgent(
    ChatAgent, SkillLibraryToolsMixin, SkillLearningToolsMixin, CodeIndexToolsMixin
):
    """The flagship GAIA agent — conversation, documents, data, web, and skills."""

    SKILL_DIRS: ClassVar[List[str]] = _bundled_skill_roots()
    SKILL_MANIFEST: ClassVar[Optional[str]] = _locate_agent_manifest()

    # Installing a skill writes third-party code under ~/.gaia/skills and
    # removing one deletes it, so both are gated the way file mutation is.
    CONFIRMATION_REQUIRED_TOOLS: ClassVar[frozenset] = frozenset(
        {"install_skill", "remove_skill"}
    )

    def __init__(self, config: Optional[GaiaAgentConfig] = None, **kwargs):
        if config is not None and kwargs:
            raise TypeError(
                "GaiaAgent takes either a ready GaiaAgentConfig or the config "
                f"fields as keyword arguments, not both. Got config= plus "
                f"{sorted(kwargs)}; those keywords would be silently dropped. "
                "Set them on the config object, or drop the config= argument."
            )
        super().__init__(config=config or GaiaAgentConfig(**kwargs))

    def close(self) -> None:
        """Release this agent's watchers, HTTP session and SQLite handles now.

        ``ChatAgent`` puts that teardown in ``__del__``, which for an instance
        whose tool registry holds bound methods only runs when the cyclic
        collector gets to it — far too late for a sidecar that builds an agent
        per one-shot request. Every step is individually guarded and idempotent,
        so the later ``__del__`` is harmless.
        """
        self.__del__()

    def _register_tools(self) -> None:
        """ChatAgent's profile tools, plus runtime access to the skill library.

        Skill-library tools go first: ChatAgent's registration ends with
        ``_snapshot_tools()``, and anything registered after that snapshot is
        absent from this instance's registry. Code-index tools join them for the
        same reason.

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
        self.skill_loader = self._maybe_build_skill_loader()
        self._skill_discovery = self._maybe_build_skill_discovery()
        self.register_skill_library_tools()
        # Adaptive skills (#2674): lets the agent correct a loaded skill that does
        # not fit, under the same confirmation gate as any other write.
        self.register_skill_learning_tools()
        # Same scope as allowed_paths, for the same reason that field rejects
        # cwd: the daemon launches this sidecar with cwd = the package
        # directory, so cwd would sandbox code search to the agent's own
        # source tree — and index it by default.
        allowed = getattr(self.config, "allowed_paths", None) or [str(Path.home())]
        self._init_code_index_state(repo_path=allowed[0])
        self.register_code_index_tools()
        super()._register_tools()

    # ── lazy skill-body loader (#2848 follow-up) ────────────────────────────

    def _maybe_build_skill_discovery(self) -> Optional[SkillDiscovery]:
        """Construct the proactive discoverer, or ``None`` when switched off.

        Built here rather than lazily so ``_discover_skills_for_turn`` never
        races the first turn, and so a misconfigured threshold fails at startup
        instead of mid-conversation.
        """
        override = discovery_env_override()
        enabled = (
            override if override is not None else bool(self.config.skill_discovery)
        )
        if not enabled:
            return None
        return SkillDiscovery(
            self.skill_manager,
            threshold=self._resolve_discovery_threshold(),
            # Lambda, not the mapping: tool registration is still running when
            # this is built, so a snapshot taken here would be empty and every
            # skill would look like it had unmet requirements.
            tools_fn=lambda: self._tools_registry,
        )

    def _resolve_discovery_threshold(self) -> Optional[float]:
        """Threshold override: env wins over config; a malformed value fails loudly."""
        raw = os.environ.get(DISCOVERY_THRESHOLD_ENV)
        if raw is None:
            return getattr(self.config, "skill_discovery_threshold", None)
        try:
            return float(raw)
        except ValueError as e:
            raise ValueError(
                f"{DISCOVERY_THRESHOLD_ENV} must be a float, got {raw!r}"
            ) from e

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

    def _select_skills_for_turn(self, user_input: str) -> Optional[List[str]]:
        """This turn's active skill-body subset, or ``None`` for "render all".

        Reuses ChatAgent's ``_build_tool_selection_query`` (previous + current
        user message) so a short follow-up ("also check the linked PR") still
        matches on the prior turn's context, not just its own few words.
        """
        if not self._dynamic_skills_active():
            return None
        query = self._build_tool_selection_query(user_input)
        return self.skill_loader.select(query, self.loaded_skills)

    def select_skill_set(self) -> Optional[str]:
        """Resolve which declared skill set to load at startup.

        Explicit config wins, then ``GAIA_SKILL_SET``, then the manifest default
        (which ships unset). Returning ``None`` means load no skills — the base
        class treats that as a deliberate choice, not a missing value.
        """
        explicit = getattr(self.config, "skill_set", None)
        if explicit:
            return explicit
        return os.environ.get(SKILL_SET_ENV) or None


__all__ = ["GaiaAgent", "GaiaAgentConfig", "SKILL_SET_ENV"]
