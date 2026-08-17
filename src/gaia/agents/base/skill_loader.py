# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""SkillLoader — per-turn semantic activation of loaded-skill bodies (#2848 follow-up).

Once a skill is loaded (``Agent.load_skill``), its ``SKILL.md`` body used to be
inlined into the system prompt of every subsequent turn for the life of the
session — measured at 15-19KB for two GitHub skills, 64.8% of the flagship
agent's prompt, paid on every turn whether or not that turn was about GitHub.

This mirrors the design of :mod:`gaia.agents.base.tool_loader` (embedding
function injected by the host, content-keyed embedding cache, inclusive cosine
threshold, fail loud + fall back to "show everything" on an embedder outage) but
is deliberately **not** the same selection model:

* ToolLoader's loaded set is monotonic — once a tool matches it stays for the
  session, because a tool's prompt line costs ~100 bytes and the monotonic
  design buys KV-cache prefix stability across turns.
* A skill body costs kilobytes, not bytes. Keeping a stale match resident is
  not a cache optimization here — it is the exact bug this module exists to
  fix. So :meth:`SkillLoader.select` is recomputed **fresh every turn** from
  the currently loaded set: no accumulation, no cap, no eviction.

Capability is never fully gated on the semantic match: :meth:`Agent.load_skill`
is itself the explicit escape hatch — calling it again on an already-loaded
skill always re-activates that skill's body for the rest of the turn, whether
or not the query matched it (mirrors ToolLoader's ``load_tools`` escape hatch).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Sequence

import numpy as np

if TYPE_CHECKING:
    from gaia.skills.format import Skill

logger = logging.getLogger(__name__)

#: Inclusive cosine threshold. Same calibration basis as ToolLoader's
#: DEFAULT_THRESHOLD (#1449) — a query is only weakly similar to a short
#: trigger description, so real scores sit well below a naive 0.5+ guess.
DEFAULT_SKILL_THRESHOLD = 0.20


def dynamic_skills_env_override() -> Optional[bool]:
    """Parse the ``GAIA_DYNAMIC_SKILLS`` override, or ``None`` when it is unset.

    Mirrors :func:`gaia.agents.base.tool_loader.dynamic_tools_env_override`.
    """
    raw = os.getenv("GAIA_DYNAMIC_SKILLS")
    if raw is None:
        return None
    return raw.strip().lower() in ("1", "true", "yes", "on")


class SkillLoader:
    """Selects which loaded skills' bodies render in the prompt this turn.

    Does **not** load, unload, or otherwise touch ``Agent.loaded_skills`` — it
    only returns the sorted name subset the host should treat as "active" this
    turn. ``None`` from :meth:`select` means "session-disabled — fall back to
    rendering every loaded skill's body" (the loud, fail-safe path on embedder
    failure, mirroring ToolLoader).
    """

    def __init__(
        self,
        embed_fn: Callable[[str], "np.ndarray"],
        *,
        embed_batch_fn: Optional[Callable[[Sequence[str]], "np.ndarray"]] = None,
        threshold: float = DEFAULT_SKILL_THRESHOLD,
    ) -> None:
        """Build a per-turn skill-body selector.

        Args:
            embed_fn: Single-text embedder returning an L2-normalized vector.
            embed_batch_fn: Optional batched embedder for the loaded skills'
                descriptions, preferred over per-doc ``embed_fn`` when given.
            threshold: Inclusive cosine match threshold.
        """
        self._embed_fn = embed_fn
        self._embed_batch_fn = embed_batch_fn
        self._threshold = float(threshold)

        # Content-keyed embedding cache: (name, sha256(doc_text)) -> vector.
        # Survives reset_session() — embeddings depend only on the skill docs.
        self._embed_cache: Dict[tuple, "np.ndarray"] = {}

        self._turn = 0
        self._disabled_until_turn = 0

    #: Turns to wait before retrying after an embedder failure. A permanent
    #: disable quietly reinstated the prompt-bloat this module exists to fix:
    #: nothing in any host resets a *skill* loader mid-session, so one
    #: warm-up hiccup on turn 1 used to cost every remaining turn.
    RETRY_COOLDOWN_TURNS = 5

    @property
    def session_disabled(self) -> bool:
        """True while an embedding failure has selection on cooldown."""
        return self._turn < self._disabled_until_turn

    def reset_session(self) -> None:
        """Clear per-session state for a new conversation.

        The embedding cache survives — it depends only on skill descriptions,
        not the conversation.
        """
        self._turn = 0
        self._disabled_until_turn = 0

    def select(
        self, query: str, loaded_skills: Dict[str, "Skill"]
    ) -> Optional[List[str]]:
        """Return the sorted subset of *loaded_skills* names active this turn.

        Args:
            query: The selection query for this turn (current, or previous +
                current — the caller's choice, mirroring ToolLoader).
            loaded_skills: The agent's live ``loaded_skills`` mapping.

        Returns:
            Sorted skill names scoring ``>= threshold`` against *query*.
            Empty list when nothing is loaded or nothing matches. ``None``
            while the embedder is on failure cooldown — the caller must fall
            back to rendering every loaded skill's body.
        """
        if not loaded_skills:
            return []

        self._turn += 1
        if self._turn <= self._disabled_until_turn:
            return None

        try:
            skill_vecs = self._ensure_skill_embeddings(loaded_skills)
            qvec = self._embed_fn(query)
        except Exception as exc:  # noqa: BLE001 — disabled + re-surfaced loudly
            self._disabled_until_turn = self._turn + self.RETRY_COOLDOWN_TURNS
            logger.warning(
                "[SkillLoader] embedding service unreachable — lazy skill-body "
                "selection disabled for the next %d turns (every loaded "
                "skill's body will render in full meanwhile). Reason: %s",
                self.RETRY_COOLDOWN_TURNS,
                exc,
            )
            return None

        scores: Dict[str, float] = {}
        active: List[str] = []
        for name, vec in skill_vecs.items():
            score = float(np.dot(qvec, vec))
            scores[name] = score
            if score >= self._threshold:
                active.append(name)

        result = sorted(active)
        logger.info(
            "SKILL_LOADER %s",
            json.dumps(
                {
                    "turn": self._turn,
                    "query_sha": _sha256(query)[:12],
                    "threshold": self._threshold,
                    "scores": {k: round(v, 4) for k, v in scores.items()},
                    "loaded": sorted(loaded_skills),
                    "active": result,
                }
            ),
        )
        return result

    def _ensure_skill_embeddings(
        self, loaded_skills: Dict[str, "Skill"]
    ) -> Dict[str, "np.ndarray"]:
        """Return ``name -> vector`` for every loaded skill, embedding lazily."""
        docs: Dict[str, str] = {
            name: self._doc_text(skill) for name, skill in loaded_skills.items()
        }
        keys: Dict[str, tuple] = {
            name: (name, _sha256(text)) for name, text in docs.items()
        }

        missing = [name for name, key in keys.items() if key not in self._embed_cache]
        if missing:
            if self._embed_batch_fn is not None:
                vecs = self._embed_batch_fn([docs[n] for n in missing])
                if len(vecs) != len(missing):
                    # A warming-up embedder can answer 200 with fewer vectors
                    # than texts; a bare zip would silently drop entries and
                    # surface later as a misleading KeyError.
                    raise RuntimeError(
                        f"embedding batch returned {len(vecs)} vectors for "
                        f"{len(missing)} texts — embedder still loading?"
                    )
                for name, vec in zip(missing, vecs):
                    self._embed_cache[keys[name]] = np.asarray(vec, dtype=np.float32)
            else:
                for name in missing:
                    self._embed_cache[keys[name]] = np.asarray(
                        self._embed_fn(docs[name]), dtype=np.float32
                    )

        return {name: self._embed_cache[keys[name]] for name in docs}

    @staticmethod
    def _doc_text(skill: "Skill") -> str:
        """The text embedded for a skill: ``"{name}: {first line of description}"``."""
        description = ""
        for line in (skill.description or "").splitlines():
            if line.strip():
                description = line.strip()
                break
        return f"{skill.name}: {description}" if description else skill.name


def _sha256(text: str) -> str:
    """Hex SHA-256 of *text* (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["SkillLoader", "DEFAULT_SKILL_THRESHOLD", "dynamic_skills_env_override"]
