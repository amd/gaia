# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-turn proactive skill discovery — find the right skill, load it, or say why not.

:mod:`gaia.agents.base.skill_retriever` answers *which* installed skill a turn is
about. This module is what an agent actually runs: it keeps the index fresh,
applies the security gates a proactive load has to respect, performs the load,
and renders the one short prompt fragment the model sees as a result.

It runs **outside** the prompt. The system prompt never carries a catalogue of
installed skills; matching happens in Python against frontmatter the agent
already has in memory, and only the winner's body is injected. Against the
flagship's measured ~17,000-token prompt the standing cost is the grounding rule
below and nothing else — every other fragment appears only on the turns it
applies to.

Three outcomes, and the third is the point
------------------------------------------
1. **Loaded.** One skill cleared the bar; its instructions and tools are live and
   the model is told to say so in a line.
2. **Shortlisted.** Several plausible, none dominant. The model is handed the
   names and calls ``load_skill`` itself. Never a coin-flip.
3. **Could not load.** A skill matched and the load *failed* — an unbridged
   permission, a missing CLI, a malformed manifest. This is the case that
   motivated the whole module: the flagship, asked about a GitHub inbox with no
   GitHub tools registered, answered confidently from its memory store. So the
   failure is stated in the prompt as a refusal instruction, not swallowed.
   A retrieval win that still lets the agent invent an answer is not a fix.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional, Tuple

from gaia.agents.base.skill_retriever import Decision, SkillRetriever
from gaia.skills.manager import ROOT_CLAUDE_IMPORT

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.skills.format import Skill
    from gaia.skills.manager import SkillManager

logger = logging.getLogger(__name__)

#: Env override for the whole feature. Unset = on for agents that opt in.
DISCOVERY_ENV = "GAIA_SKILL_DISCOVERY"

#: Env override for :data:`~gaia.agents.base.skill_retriever.MIN_SCORE`.
DISCOVERY_THRESHOLD_ENV = "GAIA_SKILL_DISCOVERY_TAU"


def discovery_env_override() -> Optional[bool]:
    """Parse :data:`DISCOVERY_ENV`, or ``None`` when it is unset.

    Mirrors :func:`gaia.agents.base.skill_loader.dynamic_skills_env_override`.
    """
    raw = os.getenv(DISCOVERY_ENV)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


#: The standing honesty rule — the only text this feature adds to every turn.
#:
#: It is deliberately about *sourcing*, not about skills: the failure it exists
#: to stop ("what's been going on in my github inbox?" answered from the memory
#: store, zero tools called) happens on exactly the turns where no skill matched,
#: so a skills-only instruction would not have been in the prompt to prevent it.
#:
#: Cost is asserted in ``tests/unit/test_skill_discovery.py`` — see that test for
#: the measured token count and why the ceiling sits where it does.
GROUNDING_RULE = (
    "==== SOURCING ====\n"
    "Facts about live external state — a repository, an inbox, a calendar, a web "
    "page, a file on disk — must come from a tool call in THIS turn. Memory, "
    "training, and earlier turns are not sources for them. If no registered tool "
    "can fetch what was asked, say you cannot and name what is missing. Never "
    "answer from recollection, and never present an example as real data."
)


@dataclass(frozen=True)
class DiscoveryResult:
    """What discovery did this turn, and the prompt fragment it produced."""

    #: Skill auto-loaded this turn, if any.
    loaded: Optional[str] = None
    #: Names offered to the model to load itself.
    shortlist: Tuple[str, ...] = ()
    #: ``(name, reason)`` when a confident match could not be loaded.
    failed: Optional[Tuple[str, str]] = None
    #: Tools the loaded skill declares but this agent does not have registered.
    unmet_tools: Tuple[str, ...] = ()
    #: The retriever's raw verdict, for logging and tests.
    decision: Optional[Decision] = None

    @property
    def outcome(self) -> str:
        """``"loaded"`` / ``"shortlist"`` / ``"failed"`` / ``"none"``."""
        if self.failed:
            return "failed"
        if self.loaded:
            return "loaded"
        return "shortlist" if self.shortlist else "none"

    def prompt_fragment(self) -> str:
        """The turn-specific note, or "" when there is nothing to say."""
        if self.failed:
            name, reason = self.failed
            return (
                "==== SKILL UNAVAILABLE ====\n"
                f"This request matches the '{name}' skill, but it could not be "
                f"loaded: {reason}\n"
                "You therefore do not have what this request needs. Tell the "
                "user you cannot do it and give them that reason. Do not answer "
                "from memory, and do not substitute a plausible-looking result."
            )
        if self.loaded:
            note = (
                "==== SKILL ACTIVATED ====\n"
                f"'{self.loaded}' was selected for this request and its "
                "instructions are in LOADED SKILLS. Follow them, and open your "
                "reply by saying in one short line that you are using it."
            )
            if self.unmet_tools:
                # ``tools_required`` is advisory at load time — the loader logs
                # the gap and loads anyway. Unsaid, the model discovers it
                # mid-recipe as a missing tool and improvises around it, which
                # is the fabrication this module exists to stop.
                note += (
                    f"\nIt expects tool(s) {', '.join(self.unmet_tools)}, which "
                    "are NOT registered here. The steps needing them cannot run: "
                    "say so plainly instead of substituting something else."
                )
            return note
        if self.shortlist:
            names = ", ".join(f"'{n}'" for n in self.shortlist)
            return (
                "==== SKILLS THAT MAY FIT ====\n"
                f"{names} are installed and may match this request, but none was "
                "a clear enough match to activate on its own. If one of them is "
                "what the user means, call load_skill on it before answering."
            )
        return ""


class SkillDiscovery:
    """Keeps the retrieval index fresh and turns one query into one action.

    Holds no reference to an agent: :meth:`run` takes the loaded set and a load
    callable, so it is exercisable in a unit test with no LLM, no Lemonade, and
    no agent instance.
    """

    #: Roots excluded from proactive matching. ``.claude/skills`` is a read-only
    #: import of another host's marketplace — skills written for Claude Code
    #: working *on* a repo, not answers to a user's question. Indexing this
    #: repo's own set made "what is the contract for this API endpoint?" match a
    #: presentation-authoring skill (it documents "request/response contracts")
    #: and "what does the function signature mean?" match an agent-scaffolding
    #: skill. Those skills stay loadable by name; they are just not proposed.
    EXCLUDED_ROOTS = frozenset({ROOT_CLAUDE_IMPORT})

    #: Consecutive load failures after which a skill stops being proposed for
    #: the rest of the session. Without it a skill whose CLI is missing is
    #: rediscovered and re-refused on every single turn.
    MAX_FAILURES = 2

    def __init__(
        self,
        manager: "SkillManager",
        *,
        retriever: Optional[SkillRetriever] = None,
        threshold: Optional[float] = None,
        tools_fn: Optional[Callable[[], Any]] = None,
    ) -> None:
        """Build the per-turn discoverer over *manager*'s discovery roots.

        Args:
            manager: The agent's :class:`~gaia.skills.manager.SkillManager`.
            retriever: Injectable index, for tests.
            threshold: Overrides the retriever module's ``MIN_SCORE``.
            tools_fn: Returns the agent's live tool registry, used to report a
                loaded skill's unmet ``tools_required``. A callable rather than
                the mapping itself because registration is still in progress
                when the agent constructs this. Omit to skip that check.
        """
        self._manager = manager
        self._retriever = retriever if retriever is not None else SkillRetriever()
        self._threshold = threshold
        self._tools_fn = tools_fn
        self._failures: Dict[str, int] = {}
        self._turn = 0

    # ── index ───────────────────────────────────────────────────────────────

    def candidates(self) -> Dict[str, "Skill"]:
        """Installed skills eligible to be proposed, by name.

        Frontmatter only — :meth:`SkillManager.discover` caches, so a steady
        session pays a dict comprehension per turn, not a directory walk.
        """
        return {
            name: skill
            for name, skill in self._manager.discover().items()
            if skill.root not in self.EXCLUDED_ROOTS
        }

    def refresh(self) -> None:
        """Reindex when the installed set has changed since the last build."""
        skills = self.candidates()
        if self._retriever.is_stale(skills):
            self._retriever.index(skills)
            logger.info(
                "[skills] discovery index rebuilt: %d skill(s) — %s",
                self._retriever.size,
                ", ".join(self._retriever.names) or "(none)",
            )

    # ── the turn ────────────────────────────────────────────────────────────

    def run(
        self,
        query: str,
        *,
        loaded: Dict[str, "Skill"],
        load_fn: Callable[[str], "Skill"],
    ) -> DiscoveryResult:
        """Match *query*, act on the verdict, and return what to tell the model.

        Args:
            query: This turn's selection query — normally previous + current
                user message, so a short follow-up still carries context.
            loaded: The agent's already-loaded skills; excluded from matching,
                since :mod:`~gaia.agents.base.skill_loader` governs those.
            load_fn: ``Agent.load_skill``. Called at most once per turn.

        Returns:
            A :class:`DiscoveryResult`. Never raises for a skill-level failure —
            a refused or broken skill becomes a ``failed`` result whose prompt
            fragment instructs the model to refuse rather than improvise.
        """
        from gaia.skills.errors import SkillError

        self._turn += 1
        self.refresh()
        if not self._retriever.size:
            return DiscoveryResult()

        burned = {n for n, c in self._failures.items() if c >= self.MAX_FAILURES}
        decision = self._decide(query, exclude=set(loaded) | burned)
        self._log(decision, loaded)

        if decision.load:
            try:
                skill = load_fn(decision.load)
            # Broad on purpose, and NOT a silent fallback: a proactive load runs
            # code (``register_skill_tools`` imports a skill's ``tools.py``) on a
            # turn the user did not ask for it, so one broken third-party skill
            # must not take down an unrelated question. Nothing is swallowed —
            # the reason is logged with its traceback AND stated to the model as
            # an instruction to refuse. ``SkillError`` alone would leave
            # ImportError, and every other import-time failure, crashing the turn.
            except Exception as exc:  # noqa: BLE001
                self._failures[decision.load] = self._failures.get(decision.load, 0) + 1
                logger.warning(
                    "[skills] '%s' matched this turn but would not load: %s: %s",
                    decision.load,
                    type(exc).__name__,
                    exc,
                    exc_info=not isinstance(exc, SkillError),
                )
                reason = (
                    str(exc)
                    if isinstance(exc, SkillError)
                    else f"{type(exc).__name__}: {exc}"
                )
                return DiscoveryResult(
                    failed=(decision.load, reason), decision=decision
                )

            unmet = self._unmet_tools(skill)
            logger.info(
                "[skills] auto-loaded '%s' for this turn%s",
                decision.load,
                f" (unmet tools_required: {', '.join(unmet)})" if unmet else "",
            )
            return DiscoveryResult(
                loaded=decision.load, unmet_tools=unmet, decision=decision
            )

        return DiscoveryResult(shortlist=decision.shortlist, decision=decision)

    def _unmet_tools(self, skill: "Skill") -> Tuple[str, ...]:
        """Tools *skill* declares that this agent never registered.

        ``tools_required`` is advisory — the base loader logs the gap and loads
        the skill anyway — so a skill can activate cleanly and then die halfway
        through its own recipe. Surfacing it here is what turns that into an
        honest "I can't do this part" instead of an improvised substitute.
        """
        if self._tools_fn is None:
            return ()
        required = getattr(getattr(skill, "gaia", None), "tools_required", None) or []
        if not required:
            return ()
        registry = self._tools_fn() or {}
        return tuple(name for name in required if name not in registry)

    def _decide(self, query: str, *, exclude) -> Decision:
        """Rank with this instance's threshold, if one was configured."""
        return self._retriever.decide(query, exclude=exclude, min_score=self._threshold)

    def _log(self, decision: Decision, loaded: Dict[str, "Skill"]) -> None:
        """One structured line per turn — the record a wrong answer is debugged from."""
        logger.info(
            "SKILL_DISCOVERY %s",
            json.dumps(
                {
                    "turn": self._turn,
                    "outcome": decision.outcome,
                    "load": decision.load,
                    "shortlist": list(decision.shortlist),
                    "scores": {c.name: round(c.score, 3) for c in decision.ranked[:5]},
                    "indexed": self._retriever.size,
                    "already_loaded": sorted(loaded),
                }
            ),
        )


__all__ = [
    "SkillDiscovery",
    "DiscoveryResult",
    "GROUNDING_RULE",
    "DISCOVERY_ENV",
    "DISCOVERY_THRESHOLD_ENV",
    "discovery_env_override",
]
