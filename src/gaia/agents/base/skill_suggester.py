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

It suggests and never loads. The lexical auto-loader the catalogue replaced
loaded the right skill on 0 of 24 tasks (#3764); a wrong suggestion costs one
line the model may ignore, a wrong load costs a skill body and possibly a CLI
grant. The model still decides.

Cheap by construction: each skill's ``"name: first line of description"`` is
embedded once and cached by content, and a turn costs one query embedding. When
the embedder is unavailable the turn simply gets no suggestion — the catalogue
is still there — and selection backs off for a minute instead of retrying on
every turn.

Calibration (``tests/fixtures/skills/skill_suggestion_cases.json``, 54 invented
requests over the 20 hub skills, 12 of them requests no skill should match, 5
of those near misses such as "what is a pivot table?"), with
``embeddinggemma-300m``: the best match on a positive request scores 0.30-0.67.
The defaults below measured precision 0.84 and recall 0.93, and 3 of the 12
negatives got a suggestion — all near misses, which score as high as real
requests (0.35-0.44), so no threshold separates them without losing recall.

Companions from ``pairs_with`` are added only when they score
:data:`DEFAULT_COMPANION_THRESHOLD` against the request themselves. Pairing is
mutual, so a hub skill like ``office-docs`` pairs with five others; added
unconditionally, companions dropped precision to 0.52. Scores are
embedder-specific: another embedding model needs its own calibration.
"""

from __future__ import annotations

import os
import time
from typing import (
    TYPE_CHECKING,
    AbstractSet,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
)

import numpy as np

from gaia.agents.base.skill_catalog import CATALOG_PAIRS_SHOWN, companions, listed
from gaia.agents.base.skill_loader import skill_doc_text
from gaia.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia.skills.format import Skill

log = get_logger(__name__)

#: Env override for the feature. Unset = the agent's config decides.
SUGGEST_ENV = "GAIA_SKILL_SUGGEST"

#: Inclusive cosine floor a skill must reach to be suggested: the lowest best
#: match on a positive request in the calibration set.
DEFAULT_SUGGEST_THRESHOLD = 0.30

#: A skill is only suggested when it scores within this much of the best match.
#: 0.05 measured precision 0.77 against 0.84 at 0.02, for 0.05 more recall.
DEFAULT_SUGGEST_MARGIN = 0.02

#: A ``pairs_with`` companion of a suggested skill joins the line only when it
#: scores at least this against the request itself.
DEFAULT_COMPANION_THRESHOLD = 0.40

#: Most skills suggested from scoring alone, before ``pairs_with`` companions.
DEFAULT_SUGGEST_TOP_K = 3

#: The request is cut to this many characters before embedding. A pasted
#: document should not blow the embedder's context; the ask usually leads.
QUERY_CHARS = 4000


def suggest_env_override() -> Optional[bool]:
    """Parse :data:`SUGGEST_ENV`, or ``None`` when it is unset."""
    raw = os.getenv(SUGGEST_ENV)
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


def render_suggestion(names: Sequence[str]) -> str:
    """The turn-context line naming *names*, or ``""`` when there are none.

    It goes into the user's message, never the system prompt: the system prompt
    has to stay byte-identical from turn to turn for the server's prompt cache.
    """
    if not names:
        return ""
    return (
        "Skills that match this request and are not loaded yet: "
        f"{', '.join(names)} — load them with load_skill before starting."
    )


class SkillSuggester:
    """Scores a request against installed skills and names the clear matches.

    Holds no agent state: :meth:`suggest` is handed the installed skills and the
    loaded names each turn, and returns names. The host renders them.
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
        """
        if top_k < 1:
            raise ValueError(f"top_k must be at least 1, got {top_k}")
        self._embed_fn = embed_fn
        self._embed_batch_fn = embed_batch_fn
        self.threshold = float(threshold)
        self.margin = float(margin)
        self.top_k = int(top_k)
        self.companion_threshold = float(companion_threshold)
        # (name, doc text) -> vector. Docs only change when a skill is edited.
        self._cache: Dict[tuple, "np.ndarray"] = {}
        self._disabled_until = 0.0

    @property
    def cooling_down(self) -> bool:
        """True while an embedder failure has suggestions paused."""
        return time.monotonic() < self._disabled_until

    def scores(self, query: str, skills: Dict[str, "Skill"]) -> Dict[str, float]:
        """Cosine score of *query* against every listed skill in *skills*.

        Raises whatever the embedder raises; :meth:`suggest` is the caller that
        turns a failure into "no suggestion".
        """
        candidates = {name: s for name, s in skills.items() if listed(s)}
        if not candidates:
            return {}
        vectors = self._skill_vectors(candidates)
        qvec = np.asarray(self._embed_fn(query[:QUERY_CHARS]), dtype=np.float32)
        return {name: float(np.dot(qvec, vec)) for name, vec in vectors.items()}

    def suggest(
        self,
        query: str,
        skills: Dict[str, "Skill"],
        loaded: AbstractSet[str] = frozenset(),
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
        if not query.strip() or self.cooling_down:
            return []
        try:
            scored = self.scores(query, skills)
        except Exception as exc:  # noqa: BLE001 — a suggestion is optional
            self._disabled_until = time.monotonic() + self.RETRY_COOLDOWN_SECONDS
            log.debug(
                "Skill suggestions paused for %.0fs: embedding failed: %s",
                self.RETRY_COOLDOWN_SECONDS,
                exc,
            )
            return []
        if not scored:
            return []

        best = max(scored.values())
        floor = max(self.threshold, best - self.margin)
        ranked = sorted(scored.items(), key=lambda item: (-item[1], item[0]))
        picked = [
            name for name, score in ranked if score >= floor and name not in loaded
        ][: self.top_k]

        result = list(picked)
        for name in picked:
            for other in companions(skills, name)[:CATALOG_PAIRS_SHOWN]:
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
        if result:
            log.info("Suggesting skills for this request: %s", ", ".join(result))
        return result

    def _skill_vectors(self, skills: Dict[str, "Skill"]) -> Dict[str, "np.ndarray"]:
        docs = {name: skill_doc_text(skill) for name, skill in skills.items()}
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
    "DEFAULT_COMPANION_THRESHOLD",
    "DEFAULT_SUGGEST_MARGIN",
    "DEFAULT_SUGGEST_THRESHOLD",
    "DEFAULT_SUGGEST_TOP_K",
    "SUGGEST_ENV",
    "SkillSuggester",
    "render_suggestion",
    "suggest_env_override",
]
