# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""ToolLoader — per-turn semantic tool selection for agents (#1449, parent #688).

Gates which tools appear in the LLM prompt each turn without ever changing the
global ``_TOOL_REGISTRY``. The registry stays the source of truth for *all*
registered tools and for execution; the loader only picks the subset that the
prompt renderers (text and native) surface to the model.

Selection model (binding — see the design sketch in #688)
--------------------------------------------------------
Per turn the loader computes ``CORE ∪ SEMANTIC(query)`` then pulls in whole
bundles for any semantically-matched member, and accumulates the result into a
session-scoped *loaded set* that only grows ("expand-on-new-match"). Because
the loaded set is monotonic and the output is sorted, non-expansion turns
serialize byte-identically, so the model backend's KV prefix cache stays warm.

* **CORE** — a small always-on set, admitted unconditionally and exempt from
  the cap and from eviction.
* **SEMANTIC** — cosine similarity (dot product over L2-normalized embeddings)
  of the query against ``"{name}: {description}"`` for every registered tool;
  a tool matches at ``score >= threshold`` (inclusive).
* **Bundles** — cohesion groups with *no* activation policy. When any member
  matches, the whole bundle is pulled in so related tools arrive together.
* **Bounded monotonic set** — below ``max_tools`` the loaded set is strictly
  monotonic; at the cap a non-CORE tool is LRU-evicted (oldest last-call,
  falling back to load-time for never-called tools). CORE and any tool admitted
  this turn are eviction-exempt. Evicted tools may be re-admitted later — the
  one and only monotonicity exception.

The loader never imports ``MemoryMixin``; the embedding function(s) are injected
by the host agent. Any embedding failure session-disables the loader with a
loud log (mirrors memory v2), and the caller reverts to the full registry.

Naming note: the class name ``ToolLoader`` and method ``reset_session()`` are
kept from the inert skeleton this replaced — existing call sites in ``cli.py``
and ``chat/app.py`` are ``hasattr``/``try``-guarded, so no call site changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# Default semantic match threshold (cosine). Tuned against the doc profile;
# overridable per-agent. Inclusive boundary: score == threshold matches.
DEFAULT_THRESHOLD = 0.55
# Default cap: 10 CORE + 4 dynamic slots = 14 (≈62% shrink on the 37-tool doc
# profile, clears the ≥60% Part-0 TTFT-reduction gate). See the plan deviations.
DEFAULT_MAX_TOOLS = 14


@dataclass(frozen=True)
class ToolBundle:
    """An immutable cohesion group of tools — no activation policy.

    Parameters
    ----------
    name:
        Bundle identifier (e.g. ``"rag_query"``, ``"file_edit"``).
    members:
        Frozenset of tool names that belong together. When any member is
        semantically matched, the whole bundle is pulled into the candidate set.
    description:
        One-line summary; feeds Part 2's escape-hatch menu. Not used for
        activation (keyword matching is intentionally banned).
    """

    name: str
    members: FrozenSet[str]
    description: str = ""


@dataclass
class _ToolState:
    """Mutable per-session bookkeeping for a single loaded tool."""

    loaded_at: float  # wall-clock load time; LRU fallback for never-called tools
    load_turn: int  # turn index the tool was admitted on (diagnostics)
    last_call_ts: Optional[float] = None  # set by record_tool_use when executed


@dataclass
class _Selection:
    """Internal scratch for one ``select()`` pass (kept for readability)."""

    scores: Dict[str, float] = field(default_factory=dict)
    matched: List[str] = field(default_factory=list)
    bundle_pulled: List[str] = field(default_factory=list)
    admitted: List[str] = field(default_factory=list)
    evicted: List[str] = field(default_factory=list)
    skipped_at_cap: List[str] = field(default_factory=list)


class ToolLoader:
    """Selects which registered tools appear in the LLM prompt each turn.

    The loader does **not** modify ``_TOOL_REGISTRY`` or gate execution; it
    returns the sorted name list the agent renders into its prompt. ``None``
    from :meth:`select` means "session-disabled — fall back to the full
    registry" (the loud, fail-safe path on embedder failure).
    """

    def __init__(
        self,
        core_tools: Sequence[str],
        bundles: Sequence[ToolBundle],
        embed_fn: Callable[[str], "np.ndarray"],
        *,
        embed_batch_fn: Optional[Callable[[Sequence[str]], "np.ndarray"]] = None,
        threshold: float = DEFAULT_THRESHOLD,
        max_tools: int = DEFAULT_MAX_TOOLS,
    ) -> None:
        """Build a semantic tool loader.

        Args:
            core_tools: Always-on tool names (cap- and eviction-exempt).
            bundles: Cohesion groups pulled in on a semantic member match.
            embed_fn: Single-text embedder returning an L2-normalized vector.
            embed_batch_fn: Optional batched embedder taking a list of texts and
                returning a 2-D array (one row per text). Preferred for the
                one-shot tool-doc embedding; falls back to per-doc ``embed_fn``.
            threshold: Inclusive cosine match threshold.
            max_tools: Hard ceiling on the loaded set size.
        """
        self._core: FrozenSet[str] = frozenset(core_tools)
        self._bundles: List[ToolBundle] = list(bundles)
        self._embed_fn = embed_fn
        self._embed_batch_fn = embed_batch_fn
        self._threshold = float(threshold)
        self._max_tools = int(max_tools)

        # Reverse index: tool name -> bundles that contain it.
        self._tool_to_bundles: Dict[str, List[ToolBundle]] = {}
        for bundle in self._bundles:
            for member in bundle.members:
                self._tool_to_bundles.setdefault(member, []).append(bundle)

        # Content-keyed embedding cache: (name, sha256(doc_text)) -> vector.
        # Survives reset_session() — embeddings depend only on tool docs.
        self._embed_cache: Dict[tuple[str, str], "np.ndarray"] = {}

        # Per-session mutable state (cleared by reset_session()).
        self._loaded: Dict[str, _ToolState] = {}
        self._turn = 0
        self._session_disabled = False

    # ── public API ───────────────────────────────────────────────────────

    @property
    def session_disabled(self) -> bool:
        """True once an embedding failure has disabled selection for the session."""
        return self._session_disabled

    def validate_registry(self, registry: Dict[str, dict]) -> None:
        """Raise if CORE or any bundle names a tool absent from *registry*.

        Fails loudly on drift so a new doc-profile tool forces a conscious
        bundling decision rather than silently slipping through unselected.
        """
        names = set(registry)
        missing_core = sorted(self._core - names)
        missing_bundle = sorted({m for b in self._bundles for m in b.members} - names)
        if missing_core or missing_bundle:
            raise ValueError(
                "ToolLoader configuration references tools missing from the "
                f"registry. Missing CORE: {missing_core or 'none'}; "
                f"missing bundle members: {missing_bundle or 'none'}. "
                "Add them to a bundle (or CORE) in tool_bundles.py, or correct "
                "the name — selection must account for every registered tool."
            )

    def select(self, query: str, registry: Dict[str, dict]) -> Optional[List[str]]:
        """Return the sorted loaded set for this turn, or ``None`` if disabled.

        ``None`` is the fail-safe signal: the session is disabled (embedder
        down) and the caller must render the full registry / legacy prompt.
        """
        if self._session_disabled:
            return None

        self._turn += 1

        try:
            tool_vecs = self._ensure_tool_embeddings(registry)
            qvec = self._embed_fn(query)
        except Exception as exc:  # noqa: BLE001 — disabled + re-surfaced loudly
            self._session_disabled = True
            logger.warning(
                "[ToolLoader] embedding service unreachable — dynamic tool "
                "selection disabled for this session (all tools will be shown; "
                "start lemonade-server and reload to re-enable). Reason: %s",
                exc,
            )
            return None

        sel = _Selection()

        # Step 3: score every registry tool; matched = score >= threshold.
        for name in registry:
            vec = tool_vecs.get(name)
            if vec is None:
                continue
            score = float(np.dot(qvec, vec))
            sel.scores[name] = score
            if score >= self._threshold:
                sel.matched.append(name)

        # Step 4: bundle pull-in. Any bundle with a matched member contributes
        # all its registry-present members; a pulled mate's admission score is
        # the max of its bundle's matched members (or its own score if higher).
        matched_set = set(sel.matched)
        candidate_scores: Dict[str, float] = {n: sel.scores[n] for n in sel.matched}
        for bundle in self._bundles:
            bundle_matched = [m for m in bundle.members if m in matched_set]
            if not bundle_matched:
                continue
            bundle_score = max(sel.scores[m] for m in bundle_matched)
            for member in bundle.members:
                if member not in registry:
                    continue
                own = sel.scores.get(member, 0.0)
                new_score = max(own, bundle_score)
                if member not in candidate_scores:
                    sel.bundle_pulled.append(member)
                candidate_scores[member] = max(
                    candidate_scores.get(member, 0.0), new_score
                )

        # Step 5: admission. CORE first (unconditional, cap-exempt). Then new
        # candidates by (descending score, ascending name).
        admitted_this_turn: set[str] = set()
        for name in sorted(self._core):
            if name in registry and name not in self._loaded:
                self._admit(name, sel)
                admitted_this_turn.add(name)

        new_candidates = [
            n
            for n in candidate_scores
            if n not in self._loaded and n not in self._core and n in registry
        ]
        new_candidates.sort(key=lambda n: (-candidate_scores[n], n))

        for name in new_candidates:
            if len(self._loaded) < self._max_tools:
                self._admit(name, sel)
                admitted_this_turn.add(name)
                continue
            # At cap: evict an LRU non-CORE, non-this-turn tool, or skip.
            victim = self._pick_eviction_victim(admitted_this_turn)
            if victim is None:
                sel.skipped_at_cap.append(name)
                continue
            del self._loaded[victim]
            sel.evicted.append(victim)
            self._admit(name, sel)
            admitted_this_turn.add(name)

        loaded_sorted = sorted(self._loaded)
        self._log_selection(query, sel, loaded_sorted)
        return loaded_sorted

    def record_tool_use(self, tool_name: str) -> None:
        """Note that *tool_name* executed — updates LRU recency.

        If the tool is loaded, refresh its ``last_call_ts``. If it is **not**
        loaded, the model reached a tool the prompt didn't list (a free
        non-tool-calling recovery via the full registry); log it as the
        escape-hatch signal. This does *not* auto-load the tool — that is
        Part 2's job.
        """
        state = self._loaded.get(tool_name)
        if state is not None:
            state.last_call_ts = time.time()
            return
        logger.info(
            json.dumps(
                {
                    "event": "TOOL_LOADER_ESCAPE_HATCH",
                    "tool": tool_name,
                    "turn": self._turn,
                    "note": "executed unlisted tool via full registry (Part-2 gap)",
                }
            )
        )

    def reset_session(self) -> None:
        """Clear per-session state for a new conversation.

        The content-keyed embedding cache survives — embeddings depend only on
        the tool docs, not on the conversation.
        """
        self._loaded.clear()
        self._turn = 0
        self._session_disabled = False

    # ── internals ────────────────────────────────────────────────────────

    def _admit(self, name: str, sel: _Selection) -> None:
        """Add *name* to the loaded set with fresh bookkeeping."""
        self._loaded[name] = _ToolState(loaded_at=time.time(), load_turn=self._turn)
        sel.admitted.append(name)

    def _pick_eviction_victim(self, protected: set[str]) -> Optional[str]:
        """Return the LRU evictable tool name, or ``None`` if nothing is evictable.

        Evictable = loaded − CORE − tools admitted this turn. Ordering key:
        ``(last_call_ts or loaded_at, loaded_at, name)`` — least-recently-used
        first, load-time as the tiebreak for never-called tools, name last for
        determinism.
        """
        evictable = [
            n for n in self._loaded if n not in self._core and n not in protected
        ]
        if not evictable:
            return None

        def _key(n: str) -> tuple[float, float, str]:
            st = self._loaded[n]
            recency = st.last_call_ts if st.last_call_ts is not None else st.loaded_at
            return (recency, st.loaded_at, n)

        return min(evictable, key=_key)

    def _ensure_tool_embeddings(
        self, registry: Dict[str, dict]
    ) -> Dict[str, "np.ndarray"]:
        """Return ``name -> vector`` for every registry tool, embedding lazily.

        Uses the content-keyed cache so unchanged tool docs are embedded once
        per process. Missing docs are batched into a single embed call when an
        ``embed_batch_fn`` was provided, else embedded per-doc.
        """
        docs: Dict[str, str] = {
            name: self._doc_text(name, info) for name, info in registry.items()
        }
        keys: Dict[str, tuple[str, str]] = {
            name: (name, _sha256(text)) for name, text in docs.items()
        }

        missing = [name for name, key in keys.items() if key not in self._embed_cache]
        if missing:
            if self._embed_batch_fn is not None:
                vecs = self._embed_batch_fn([docs[n] for n in missing])
                for name, vec in zip(missing, vecs):
                    self._embed_cache[keys[name]] = np.asarray(vec, dtype=np.float32)
            else:
                for name in missing:
                    self._embed_cache[keys[name]] = np.asarray(
                        self._embed_fn(docs[name]), dtype=np.float32
                    )

        return {name: self._embed_cache[keys[name]] for name in docs}

    @staticmethod
    def _doc_text(name: str, info: dict) -> str:
        """The text embedded for a tool: ``"{name}: {first line of description}"``."""
        description = ""
        for line in (info.get("description") or "").splitlines():
            if line.strip():
                description = line.strip()
                break
        return f"{name}: {description}" if description else name

    def _log_selection(
        self, query: str, sel: _Selection, loaded_sorted: List[str]
    ) -> None:
        """Emit one structured ``TOOL_LOADER`` INFO line (Part-2 tuning data)."""
        logger.info(
            "TOOL_LOADER %s",
            json.dumps(
                {
                    "turn": self._turn,
                    "query_sha": _sha256(query)[:12],
                    "threshold": self._threshold,
                    "max_tools": self._max_tools,
                    "scores": {k: round(v, 4) for k, v in sel.scores.items()},
                    "matched": sorted(sel.matched),
                    "bundle_pulled": sorted(sel.bundle_pulled),
                    "admitted": sorted(sel.admitted),
                    "evicted": sorted(sel.evicted),
                    "skipped_at_cap": sorted(sel.skipped_at_cap),
                    "loaded": loaded_sorted,
                }
            ),
        )


def _sha256(text: str) -> str:
    """Hex SHA-256 of *text* (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
