# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-turn skill suggestions: name the installed skills that fit this request.

The catalogue (:mod:`gaia.agents.base.skill_catalog`) lists every installed skill
and asks the model to load each one that matches. Large models do; small local
models often skim a long list and load nothing, or only the first skill when the
work needs two. This module does the narrowing for them. At the start of a turn
it scores the user's request against every installed skill that is not loaded
and, when one or more clearly fit, the user's message gains one line naming
them.

By default it suggests and never loads. The lexical auto-loader the catalogue
replaced loaded the right skill on 0 of 24 tasks (#3764); a wrong suggestion
costs one line the model may ignore, a wrong load costs a skill body and
possibly a CLI grant. The model still decides.

Auto-load is the opt-in exception (:meth:`SkillSuggester.select`): local models
often ignore even a correct suggestion, so a host may ask for the matches far
above the suggestion floor, and clearly ahead of every other skill, to load
before the turn. Skills that pair with the best match (``pairs_with``) are
expected to score close to it, so they do not count as a rival. Pairing loads
nothing: a companion loads only when it clears the floor and leads every
remaining skill itself; otherwise it is named after the loaded skill as
available. Everything below that bar is only suggested.

What is embedded: each skill's description under EmbeddingGemma's document
prompt (:data:`DOC_PROMPT`) and the request under its classification prompt
(:data:`QUERY_PROMPT`). The skill's name is left out: a question that merely
mentions it ("what does a source watch mean?") matched the name.

Cheap by construction: each description is embedded once and cached by
content, and a turn costs one query embedding. When the embedder is
unavailable the turn simply gets no suggestion — the catalogue is still there
— and selection backs off for a minute instead of retrying on every turn.

Calibration set: ``tests/fixtures/skills/skill_suggestion_cases.json`` holds
invented requests labelled with the hub skills they should suggest; a request
with no label should match nothing. ``tests/unit/test_skill_suggester.py``
splits it in two by a hash of the request: prompts and defaults are chosen on
the tuning half and reported on the held-out half. The
measured numbers, and the bar the defaults are held to, are ``AUTOLOAD_BAR``
and ``SUGGEST_BAR`` in that test.

The auto-loads on a request that needs no skill are questions *about* a
skill's topic ("what is RSS?"): they score as high as real requests, so no
floor removes them without losing recall. A wrong load costs context, not
correctness: at most :data:`MAX_AUTOLOADS_PER_TURN` skills load, under the
host's share-of-context cap.

Companions from ``pairs_with`` are added only when they score
:data:`DEFAULT_COMPANION_THRESHOLD` against the request themselves. Pairing is
mutual, so a hub skill can pair with five others; added unconditionally,
companions cost a third of the precision. Scores are embedder-specific:
another embedding model needs its own prompts and its own calibration, which
is why a host only auto-loads on the calibrated embedder.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    AbstractSet,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
)

import numpy as np

from gaia.agents.base.skill_catalog import CATALOG_PAIRS_SHOWN, companions, listed
from gaia.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.skills.format import Skill

log = get_logger(__name__)

#: Env override for the feature. Unset = the agent's config decides.
SUGGEST_ENV = "GAIA_SKILL_SUGGEST"

#: Env override for auto-loading. Unset = the agent's config decides.
AUTOLOAD_ENV = "GAIA_SKILL_AUTOLOAD"

#: Env overrides for the auto-load floor and lead, named like
#: ``GAIA_DYNAMIC_SKILLS_TAU``. Unset = the agent's config decides.
AUTOLOAD_THRESHOLD_ENV = "GAIA_SKILL_AUTOLOAD_TAU"
AUTOLOAD_MARGIN_ENV = "GAIA_SKILL_AUTOLOAD_MARGIN"

#: Inclusive cosine floor a skill must reach to be suggested. Chosen on the
#: tuning half: 0.30 suggested a skill on twice as many requests needing none.
DEFAULT_SUGGEST_THRESHOLD = 0.35

#: A skill is only suggested when it scores within this much of the best match.
#: 0.05 measured precision 0.77 against 0.84 at 0.02, for 0.05 more recall.
DEFAULT_SUGGEST_MARGIN = 0.02

#: A ``pairs_with`` companion of a suggested skill joins the line only when it
#: scores at least this against the request itself.
DEFAULT_COMPANION_THRESHOLD = 0.40

#: Most skills suggested from scoring alone, before ``pairs_with`` companions.
DEFAULT_SUGGEST_TOP_K = 3

#: Inclusive cosine floor for loading a skill before the turn, when a host
#: opts in. Chosen on the tuning half: 0.45 gave up a fifth of the loads.
DEFAULT_AUTOLOAD_THRESHOLD = 0.40

#: The best auto-load must lead every skill it does not pair with, loaded or
#: not, by this much: two unrelated skills scoring alike means the request is
#: ambiguous, so the model picks.
DEFAULT_AUTOLOAD_MARGIN = 0.03

#: Slack on the lead comparison, so a lead of exactly the margin counts:
#: 0.58 - 0.55 is 0.0299999... in floating point.
AUTOLOAD_LEAD_EPSILON = 1e-6

#: Most skills auto-loaded on one turn.
MAX_AUTOLOADS_PER_TURN = 2

#: The request is cut to this many characters before embedding. A pasted
#: document should not blow the embedder's context; the ask usually leads.
QUERY_CHARS = 4000

#: EmbeddingGemma's prompts for a document with no title and for a text to
#: classify. On the tuning half, unprefixed text loaded half as many right
#: skills at the same precision, and its retrieval query prompt fewer still.
DOC_PROMPT = "title: none | text: "
QUERY_PROMPT = "task: classification | query: "


def skill_match_text(skill: "Skill") -> str:
    """The text embedded for a skill: its description, or its name without one.

    Only the first non-blank line of the description, like
    :func:`gaia.agents.base.skill_loader.skill_doc_text`.
    """
    for line in (skill.description or "").splitlines():
        if line.strip():
            return DOC_PROMPT + line.strip()
    return DOC_PROMPT + skill.name


def _env_flag(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def suggest_env_override() -> Optional[bool]:
    """Parse :data:`SUGGEST_ENV`, or ``None`` when it is unset."""
    return _env_flag(SUGGEST_ENV)


def autoload_env_override() -> Optional[bool]:
    """Parse :data:`AUTOLOAD_ENV`, or ``None`` when it is unset."""
    return _env_flag(AUTOLOAD_ENV)


def _env_float(name: str) -> Optional[float]:
    raw = os.getenv(name)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be a float, got {raw!r}") from e


def autoload_threshold_env_override() -> Optional[float]:
    """Parse :data:`AUTOLOAD_THRESHOLD_ENV`, or ``None`` when it is unset.

    Raises:
        ValueError: the value is not a number.
    """
    return _env_float(AUTOLOAD_THRESHOLD_ENV)


def autoload_margin_env_override() -> Optional[float]:
    """Parse :data:`AUTOLOAD_MARGIN_ENV`, or ``None`` when it is unset.

    Raises:
        ValueError: the value is not a number.
    """
    return _env_float(AUTOLOAD_MARGIN_ENV)


@dataclass
class SkillMatches:
    """One turn's matches: skills to load before the turn, and to suggest."""

    auto_load: List[str] = field(default_factory=list)
    suggest: List[str] = field(default_factory=list)


def render_suggestion(names: Sequence[str], after_autoload: bool = False) -> str:
    """The turn-context line naming *names*, or ``""`` when there are none.

    It goes into the user's message, never the system prompt: the system prompt
    has to stay byte-identical from turn to turn for the server's prompt cache.

    The best match is an instruction, because a small local model passes over
    a line that only offers names. The rest keep their order and stay an offer.
    With *after_autoload* the best match is already loaded, so every name is
    an offer.
    """
    if not names:
        return ""
    if after_autoload:
        return (
            f"Also available: {', '.join(names)}. Call load_skill for one only "
            "if this request needs it."
        )
    line = (
        f"Call load_skill('{names[0]}') before you start: it covers this kind "
        "of request."
    )
    if len(names) > 1:
        line += f" Also consider: {', '.join(names[1:])}."
    return line


def render_autoloaded(names: Sequence[str]) -> str:
    """The turn-context line naming skills loaded for this request, or ``""``.

    It goes into the user's message, like :func:`render_suggestion`.
    """
    if not names:
        return ""
    if len(names) == 1:
        return (
            f"Loaded {names[0]} because it matches this request — its "
            "instructions are in the system prompt."
        )
    return (
        f"Loaded {', '.join(names[:-1])} and {names[-1]} because they match "
        "this request — their instructions are in the system prompt."
    )


class SkillSuggester:
    """Scores a request against installed skills and names the clear matches.

    Holds no agent state: :meth:`suggest` is handed the installed skills and the
    loaded names each turn, and returns names. The host renders them, and
    loads the ones :meth:`select` marks for auto-load.
    """

    #: Seconds without suggestions after an embedder failure, so a down
    #: embedder costs one failed call a minute rather than one per turn.
    RETRY_COOLDOWN_SECONDS = 60.0

    def __init__(
        self,
        embed_fn: Callable[[str], "np.ndarray"],
        *,
        embed_batch_fn: Optional[Callable[[Sequence[str]], "np.ndarray"]] = None,
        threshold: float = DEFAULT_SUGGEST_THRESHOLD,
        margin: float = DEFAULT_SUGGEST_MARGIN,
        top_k: int = DEFAULT_SUGGEST_TOP_K,
        companion_threshold: float = DEFAULT_COMPANION_THRESHOLD,
        autoload_threshold: Optional[float] = None,
        autoload_margin: float = DEFAULT_AUTOLOAD_MARGIN,
        max_autoloads: int = MAX_AUTOLOADS_PER_TURN,
    ) -> None:
        """Build a suggester.

        Args:
            embed_fn: Single-text embedder returning an L2-normalized vector.
            embed_batch_fn: Optional batched embedder for skill descriptions,
                preferred over per-skill ``embed_fn`` calls when given.
            threshold: Inclusive cosine floor for a suggestion.
            margin: How far below the best score a suggestion may sit.
            top_k: Most skills suggested by score (companions come on top).
            companion_threshold: Inclusive cosine floor for a companion.
            autoload_threshold: Inclusive cosine floor for an auto-load, or
                ``None`` (the default) to never auto-load.
            autoload_margin: How far the best auto-load must lead every skill
                it does not pair with.
            max_autoloads: Most skills auto-loaded per turn.
        """
        if top_k < 1:
            raise ValueError(f"top_k must be at least 1, got {top_k}")
        if max_autoloads < 0:
            raise ValueError(f"max_autoloads must not be negative, got {max_autoloads}")
        self._embed_fn = embed_fn
        self._embed_batch_fn = embed_batch_fn
        self.threshold = float(threshold)
        self.margin = float(margin)
        self.top_k = int(top_k)
        self.companion_threshold = float(companion_threshold)
        self.autoload_threshold = (
            None if autoload_threshold is None else float(autoload_threshold)
        )
        self.autoload_margin = float(autoload_margin)
        self.max_autoloads = int(max_autoloads)
        # (name, doc text) -> vector. Docs only change when a skill is edited.
        self._cache: Dict[tuple, "np.ndarray"] = {}
        self._disabled_until = 0.0

    @property
    def cooling_down(self) -> bool:
        """True while an embedder failure has suggestions paused."""
        return time.monotonic() < self._disabled_until

    def scores(
        self, query: str, skills: Dict[str, "Skill"], list_project: bool = False
    ) -> Dict[str, float]:
        """Cosine score of *query* against every listed skill in *skills*.

        *list_project* is the catalogue's flag (:func:`listed`), so only a skill
        the catalogue lists can be suggested.

        Raises whatever the embedder raises; :meth:`suggest` is the caller that
        turns a failure into "no suggestion".
        """
        candidates = {name: s for name, s in skills.items() if listed(s, list_project)}
        if not candidates:
            return {}
        vectors = self._skill_vectors(candidates)
        qvec = np.asarray(
            self._embed_fn(QUERY_PROMPT + query[:QUERY_CHARS]), dtype=np.float32
        )
        return {name: float(np.dot(qvec, vec)) for name, vec in vectors.items()}

    def suggest(
        self,
        query: str,
        skills: Dict[str, "Skill"],
        loaded: AbstractSet[str] = frozenset(),
        list_project: bool = False,
    ) -> List[str]:
        """Names to suggest for *query*: clear matches, then their companions.

        A skill is a clear match when it scores at least :attr:`threshold` and
        within :attr:`margin` of the best-scoring skill. Each match's
        ``pairs_with`` companions follow it when they score at least
        :attr:`companion_threshold`. The best score counts
        loaded skills too, so a request the loaded skill already covers does
        not drag in a weaker neighbour. Loaded skills are never suggested.

        Returns ``[]`` when nothing fits, the request is blank, or the embedder
        failed — the catalogue still lists every skill, so a missing
        suggestion degrades to the behaviour without this feature.
        """
        result, _ = self._match(query, skills, loaded, list_project)
        if result:
            log.info("Suggesting skills for this request: %s", ", ".join(result))
        return result

    def select(
        self,
        query: str,
        skills: Dict[str, "Skill"],
        loaded: AbstractSet[str] = frozenset(),
        list_project: bool = False,
    ) -> SkillMatches:
        """What :meth:`suggest` returns, split into auto-loads and suggestions.

        The best match scoring at least :attr:`autoload_threshold` becomes an
        auto-load when it leads every skill it does not pair with, loaded or
        not, by :attr:`autoload_margin`. Its best-scoring companion loads with
        it only when it clears :attr:`autoload_threshold` and leads every
        remaining skill by :attr:`autoload_margin` itself, up to
        :attr:`max_autoloads`. The rest stay suggestions, in the same order.
        With no :attr:`autoload_threshold` nothing is ever an auto-load.
        """
        matched, scored = self._match(query, skills, loaded, list_project)
        auto = self._auto_loads(matched, scored, skills, list_project)
        result = SkillMatches(
            auto_load=auto, suggest=[name for name in matched if name not in auto]
        )
        if auto:
            log.info("Skills clearing the auto-load bar: %s", ", ".join(auto))
        if result.suggest:
            log.info(
                "Suggesting skills for this request: %s", ", ".join(result.suggest)
            )
        return result

    def _auto_loads(
        self,
        matched: List[str],
        scored: Dict[str, float],
        skills: Dict[str, "Skill"],
        list_project: bool = False,
    ) -> List[str]:
        """The *matched* names to load before the turn, best first.

        A companion of the leader is expected to score close to it, so it is
        not a rival: only skills the leader does not pair with are held to the
        margin. Pairing never loads the companion: it must clear the floor and
        lead every skill but the leader on its own score.
        """
        if self.autoload_threshold is None or self.max_autoloads == 0:
            return []
        eligible = sorted(
            (name for name in matched if scored[name] >= self.autoload_threshold),
            key=lambda name: (-scored[name], name),
        )
        if not eligible:
            return []
        leader = eligible[0]
        mates = set(companions(skills, leader, list_project))
        runner_up = max(
            (
                score
                for name, score in scored.items()
                if name != leader and name not in mates
            ),
            default=float("-inf"),
        )
        if scored[leader] - runner_up + AUTOLOAD_LEAD_EPSILON < self.autoload_margin:
            return []
        # At most one companion: a second body is a lot of prompt to add unasked.
        for mate in (name for name in eligible[1:] if name in mates):
            rest = max(
                (s for name, s in scored.items() if name not in (leader, mate)),
                default=float("-inf"),
            )
            if scored[mate] - rest + AUTOLOAD_LEAD_EPSILON >= self.autoload_margin:
                return [leader, mate][: self.max_autoloads]
            break
        return [leader]

    def _match(
        self,
        query: str,
        skills: Dict[str, "Skill"],
        loaded: AbstractSet[str],
        list_project: bool,
    ) -> Tuple[List[str], Dict[str, float]]:
        """The names :meth:`suggest` returns, and every listed skill's score."""
        if not query.strip() or self.cooling_down:
            return [], {}
        try:
            scored = self.scores(query, skills, list_project)
        except Exception as exc:  # noqa: BLE001 — a suggestion is optional
            self._disabled_until = time.monotonic() + self.RETRY_COOLDOWN_SECONDS
            log.warning(
                "Skill suggestions paused for %.0fs (the catalogue still lists "
                "every skill): embedding failed: %s",
                self.RETRY_COOLDOWN_SECONDS,
                exc,
            )
            return [], {}
        if not scored:
            return [], {}

        best = max(scored.values())
        floor = max(self.threshold, best - self.margin)
        ranked = sorted(scored.items(), key=lambda item: (-item[1], item[0]))
        picked = [
            name for name, score in ranked if score >= floor and name not in loaded
        ][: self.top_k]

        result = list(picked)
        for name in picked:
            for other in companions(skills, name, list_project)[:CATALOG_PAIRS_SHOWN]:
                if (
                    other not in loaded
                    and other not in result
                    and scored.get(other, 0.0) >= self.companion_threshold
                ):
                    result.append(other)

        log.debug(
            "Skill suggestion floor=%.3f picked=%s top=%s",
            floor,
            result,
            [(name, round(score, 3)) for name, score in ranked[:5]],
        )
        return result, scored

    def _skill_vectors(self, skills: Dict[str, "Skill"]) -> Dict[str, "np.ndarray"]:
        docs = {name: skill_match_text(skill) for name, skill in skills.items()}
        missing = [name for name, doc in docs.items() if (name, doc) not in self._cache]
        if missing:
            texts = [docs[name] for name in missing]
            if self._embed_batch_fn is not None:
                vectors = self._embed_batch_fn(texts)
                if len(vectors) != len(missing):
                    raise RuntimeError(
                        f"embedding batch returned {len(vectors)} vectors for "
                        f"{len(missing)} texts"
                    )
            else:
                vectors = [self._embed_fn(text) for text in texts]
            for name, vec in zip(missing, vectors):
                self._cache[(name, docs[name])] = np.asarray(vec, dtype=np.float32)
        return {name: self._cache[(name, doc)] for name, doc in docs.items()}


__all__ = [
    "AUTOLOAD_ENV",
    "AUTOLOAD_LEAD_EPSILON",
    "AUTOLOAD_MARGIN_ENV",
    "AUTOLOAD_THRESHOLD_ENV",
    "DEFAULT_AUTOLOAD_MARGIN",
    "DEFAULT_AUTOLOAD_THRESHOLD",
    "DEFAULT_COMPANION_THRESHOLD",
    "DEFAULT_SUGGEST_MARGIN",
    "DEFAULT_SUGGEST_THRESHOLD",
    "DEFAULT_SUGGEST_TOP_K",
    "DOC_PROMPT",
    "MAX_AUTOLOADS_PER_TURN",
    "QUERY_CHARS",
    "QUERY_PROMPT",
    "SUGGEST_ENV",
    "SkillMatches",
    "SkillSuggester",
    "autoload_env_override",
    "autoload_margin_env_override",
    "autoload_threshold_env_override",
    "render_autoloaded",
    "render_suggestion",
    "skill_match_text",
    "suggest_env_override",
]
