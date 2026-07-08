# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
ProceduralMemoryMixin: procedural-memory orchestration for MemoryMixin (#887).

Extracted from ``memory.py`` to keep the procedural-memory layer (the
procedures FAISS index, skill recall, and skill synthesis) cohesive in one
module.  This is the orchestration half of the procedural loop; the pure
pipeline lives in ``skill_synthesis.py`` and the data access in
``memory_store.py``.

Not used standalone — ``MemoryMixin`` subclasses it, and every method here
resolves on a ``MemoryMixin`` host via the MRO.

Spec: docs/plans/skill-synthesis.mdx
"""

from typing import Dict, List, Optional, Tuple

import numpy as np

from gaia.agents.base.skill_synthesis import (
    Skill,
    SynthesisConfig,
    cluster_by_goal,
    distill_cluster,
    extract_sequences,
    load_synthesis_config,
    reconcile_and_store,
)
from gaia.logger import get_logger

logger = get_logger(__name__)


class ProceduralMemoryMixin:
    """Procedural-memory methods for MemoryMixin (#887).

    Not used standalone — MemoryMixin subclasses it; every method resolves on a
    MemoryMixin host via MRO. Relies on host state/methods that
    MemoryMixin.init_memory and MemoryMixin define: self._memory_store,
    self._proc_faiss_index, self._proc_faiss_id_map, self._recalled_skill_prompt,
    self._recalled_skills, self._embed_text, self.chat, self.rebuild_system_prompt.
    """

    # ==================================================================
    # Procedures FAISS Index (v3 — procedural memory, #887)
    # ==================================================================

    def _rebuild_proc_faiss_index(self) -> None:
        """Build the procedures FAISS index from stored procedure embeddings.

        Separate from the knowledge index (``_faiss_index``): it indexes
        ``procedures.embedding`` (the ``when_to_use`` trigger vector) so
        goal→procedure recall is isolated from knowledge search.  IndexFlatIP
        on L2-normalized vectors = cosine similarity, mirroring
        ``_rebuild_faiss_index``.  Only ``enabled`` (and non-superseded)
        procedures are indexed, so a disabled procedure is absent from recall.
        """
        # Deferred to break the memory <-> procedural_memory import cycle; read at
        # call time, after memory.py has finished loading.
        from gaia.agents.base.memory import EMBEDDING_DIM, _blob_to_embedding

        try:
            import faiss
        except ImportError:
            logger.warning(
                "[MemoryMixin] faiss-cpu not installed; procedure recall disabled"
            )
            self._proc_faiss_index = None
            self._proc_faiss_id_map = []
            return

        store = self._memory_store
        if store is None:
            self._proc_faiss_index = None
            self._proc_faiss_id_map = []
            return

        procedures = store.search_skills(
            enabled_only=True, include_superseded=False, with_embedding=True
        )

        index = faiss.IndexFlatIP(EMBEDDING_DIM)
        id_map: List[str] = []

        for proc in procedures:
            blob = proc.get("embedding")
            if not blob:
                continue
            try:
                vec = _blob_to_embedding(blob)
                if vec.shape[0] != EMBEDDING_DIM:
                    logger.debug(
                        "[MemoryMixin] skipping procedure embedding for %s: wrong dim %d",
                        proc["id"],
                        vec.shape[0],
                    )
                    continue
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
                index.add(vec.reshape(1, -1))
                id_map.append(proc["id"])
            except Exception as e:
                logger.debug(
                    "[MemoryMixin] skipping bad procedure embedding for %s: %s",
                    proc["id"],
                    e,
                )

        self._proc_faiss_index = index
        self._proc_faiss_id_map = id_map
        logger.info(
            "[MemoryMixin] procedures FAISS index rebuilt: %d vectors", index.ntotal
        )

    def _proc_faiss_add(self, procedure_id: str, vec: np.ndarray) -> None:
        """Add a single procedure vector to the procedures FAISS index.

        Incremental update after a procedure is stored.  Skips if
        ``procedure_id`` is already indexed (idempotent on re-store), mirroring
        ``_faiss_add``.
        """
        if self._proc_faiss_index is None:
            return
        try:
            if procedure_id in self._proc_faiss_id_map:
                return
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            self._proc_faiss_index.add(vec.reshape(1, -1))
            self._proc_faiss_id_map.append(procedure_id)
        except Exception as e:
            # The procedure is already persisted; surface the index miss loudly
            # (WARNING, not debug) so a recall gap is visible without --debug.
            logger.warning(
                "[MemoryMixin] procedure FAISS add failed for %s — procedure "
                "stored but absent from the recall index: %s",
                procedure_id,
                e,
            )

    def _proc_faiss_search(self, query_vec: np.ndarray, top_k: int) -> List[tuple]:
        """Search the procedures FAISS index for the top_k nearest procedures.

        Mirrors ``_faiss_search`` but over ``_proc_faiss_index`` /
        ``_proc_faiss_id_map``.  Assumes ``query_vec`` is already L2-normalized
        (``_embed_text`` guarantees this), so the inner-product score is cosine
        similarity.  The caller applies the ``SIMILARITY_TAU`` match threshold;
        this method only ranks.

        Args:
            query_vec: L2-normalized goal vector.
            top_k: Maximum number of procedures to return.

        Returns:
            ``(procedure_id, score)`` tuples, score descending.
        """
        index = getattr(self, "_proc_faiss_index", None)
        if index is None or index.ntotal == 0:
            return []
        try:
            k = min(top_k, index.ntotal)
            query = query_vec.reshape(1, -1).astype(np.float32)
            scores, indices = index.search(query, k)
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if 0 <= idx < len(self._proc_faiss_id_map):
                    results.append((self._proc_faiss_id_map[idx], float(score)))
            return results
        except Exception as e:
            logger.debug("[MemoryMixin] procedure FAISS search failed: %s", e)
            return []

    # ==================================================================
    # Skill Recall (procedural memory, #887 — RECALL)
    # ==================================================================

    def recall_skill(
        self, goal: str, top_k: int = 2, similarity_tau: Optional[float] = None
    ) -> List[Skill]:
        """Recall stored procedures whose trigger matches ``goal`` (vector search).

        The RECALL half of the procedural loop and the consumer the tool-loader
        (#1451) calls programmatically: a cosine search over
        ``procedures.embedding`` (the ``when_to_use`` trigger corpus, its own
        FAISS index), returning the matched procedures so the planner can reuse a
        proven recipe instead of re-planning.  It is an internal method, **not** a
        sixth ``@tool`` — the five-tool memory registry is unchanged.

        Off-states land on the conservative floor — *no procedural signal*, never
        a wrong answer (``docs/plans/skill-synthesis.mdx`` "Off-states as safe
        floors"):

        * no store (``GAIA_MEMORY_DISABLED=1`` / Lemonade unreachable at init)
          -> ``[]``;
        * empty goal or empty procedures index -> ``[]``;
        * a match below ``SIMILARITY_TAU`` -> dropped (an unrelated nearest
          neighbour is never injected);
        * a procedure disabled (``enabled=0``) or superseded since the index was
          built -> excluded at fetch time (``enabled_only=True``), so disabling a
          procedure prevents its recall even before the index is rebuilt;
        * the goal embedding failing -> logged + ``[]`` (recall is an
          enhancement; a transient embedder hiccup must not crash the user's turn
          or strip a capability the agent had pre-synthesis).

        Args:
            goal: The current user goal to match against procedure triggers.
            top_k: Maximum number of procedures to recall (default 2).
            similarity_tau: Cosine match threshold; ``None`` resolves it from
                ``memory_settings.json`` (the spec's "clustering AND recall"
                constant).  The injection path passes its already-resolved value
                so a recalling turn reads the settings file only once.

        Returns:
            Matched ``Skill`` objects (full bodies; injection truncates, the row
            keeps the full body), best match first; ``[]`` on any off-state.
        """
        from gaia.agents.base.memory import (
            _load_memory_settings,  # deferred (cycle break)
        )

        store = self._memory_store
        if store is None:
            return []
        if not goal or not goal.strip():
            return []
        index = getattr(self, "_proc_faiss_index", None)
        if index is None or index.ntotal == 0:
            return []

        try:
            query_vec = self._embed_text(goal)
        except Exception as e:
            logger.warning(
                "[MemoryMixin] procedure recall skipped — embedding the goal "
                "failed (start lemonade-server to re-enable recall): %s",
                e,
            )
            return []

        matches = self._proc_faiss_search(query_vec, top_k)
        if not matches:
            return []

        tau = (
            similarity_tau
            if similarity_tau is not None
            else load_synthesis_config(_load_memory_settings()).similarity_tau
        )
        skills: List[Skill] = []
        recalled_ids: List[str] = []
        for procedure_id, score in matches:
            if score < tau:
                continue
            rows = store.search_skills(
                skill_id=procedure_id,
                enabled_only=True,
                include_superseded=False,
                limit=1,
            )
            if not rows:
                # Disabled / superseded since the index was built — the AC
                # "disabling a skill prevents recall" holds even on a stale index.
                continue
            row = rows[0]
            skills.append(
                Skill(
                    name=row["name"],
                    when_to_use=row["when_to_use"],
                    body=row["markdown_body"],
                    tools_required=row.get("tools_required") or [],
                )
            )
            recalled_ids.append(procedure_id)

        # Stamp last_used_at so `gaia memory status` can report reuse. Telemetry
        # only — a write hiccup must not crash the turn or drop the recall.
        if recalled_ids:
            try:
                store.touch_skills(recalled_ids)
            except Exception as e:
                logger.debug(
                    "[MemoryMixin] last_used_at touch failed "
                    "(recall still served): %s",
                    e,
                )
        return skills

    def _recall_skills_for_turn(
        self, goal: str
    ) -> Tuple[List[Skill], Optional[SynthesisConfig]]:
        """Recall the procedures matching ``goal`` once, with the resolved config.

        The single per-turn recall pass shared by both consumers:
        ``_refresh_recalled_skills`` renders the prompt from the returned skills,
        and ``_recalled_skill_tools`` reads them for the tool-loader SKILL signal
        (#1451).  Recalling here once keeps that signal free — no second
        ``recall_skill`` (embed + FAISS) call, so the loader adds zero TTFT cost.

        Off-states return ``([], None)`` so neither consumer fires:

        * zero-cost off-state — no procedures (new user) or memory disabled
          (``GAIA_MEMORY_DISABLED`` -> no store -> index never built) -> the
          empty-index guard short-circuits before the per-turn
          ``memory_settings.json`` read (mirrors ``recall_skill``);
        * a recall hiccup (embedder down mid-turn) -> logged + ``([], config)``,
          so the turn degrades to the pre-synthesis behavior, never crashes.
        """
        index = getattr(self, "_proc_faiss_index", None)
        if index is None or index.ntotal == 0:
            return [], None

        from gaia.agents.base.memory import (
            _load_memory_settings,  # deferred (cycle break)
        )

        # Resolve thresholds once for this turn (single settings read), then pass
        # the tau down so recall_skill does not re-read the file.
        config = load_synthesis_config(_load_memory_settings())
        try:
            skills = self.recall_skill(goal, similarity_tau=config.similarity_tau)
        except Exception as e:
            logger.debug(
                "[MemoryMixin] per-turn skill recall failed "
                "(turn degrades to pre-synthesis): %s",
                e,
            )
            return [], config
        return skills, config

    def _build_recalled_skills_prompt(
        self, skills: List[Skill], config: Optional[SynthesisConfig]
    ) -> str:
        """Render the recalled-procedure system-prompt section from ``skills``.

        Pure renderer over the already-recalled ``skills`` (and the ``config``
        resolved alongside them by ``_recall_skills_for_turn``) — the recall and
        the single settings read happen once upstream and feed both this prompt
        and the loader's ``_recalled_skill_tools`` signal.  Each body is capped at
        ``config.max_recall_body_chars`` (default 1500) with an explicit
        ``… (truncated)`` marker; the full body always stays in the
        ``procedures`` row.  Returns ``""`` when ``skills`` is empty, so the
        composed system prompt is byte-identical to a no-procedure build.
        """
        if not skills:
            return ""

        cap = config.max_recall_body_chars
        blocks: List[str] = []
        for skill in skills:
            body = skill.body
            if len(body) > cap:
                body = body[:cap].rstrip() + "\n… (truncated)"
            blocks.append(
                f"## {skill.name}\nWhen to use: {skill.when_to_use}\n\n{body}"
            )

        header = (
            "=== RECALLED PROCEDURES (learned from past successful runs) ===\n"
            "You have succeeded at similar goals before. Reuse the proven "
            "procedure(s) below instead of re-planning from scratch; adapt the "
            "steps to the current request."
        )
        return header + "\n\n" + "\n\n".join(blocks)

    def _recalled_skill_tools(self) -> List[str]:
        """Return the recalled skills' ``tools_required``, flattened and deduped.

        The tool loader's SKILL signal (#1451): the exact tools the procedure(s)
        recalled this turn used, in recall rank then declaration order, each kept
        once.  Reads the per-turn cache ``_refresh_recalled_skills`` populated
        from a single ``recall_skill`` pass — no extra embed/FAISS work, so the
        signal adds no TTFT cost.  ``[]`` on every off-state (no recall this turn,
        memory disabled, or the cache never initialized), so the loader runs on
        CORE + semantic exactly as in Parts 1-2.
        """
        tools: List[str] = []
        seen: set[str] = set()
        for skill in getattr(self, "_recalled_skills", []):
            for tool in skill.tools_required:
                if tool not in seen:
                    seen.add(tool)
                    tools.append(tool)
        return tools

    def get_recalled_skills_system_prompt(self) -> str:
        """Contribute the recalled-procedure block to the composed system prompt.

        Auto-discovered by ``Agent._get_mixin_prompts`` (the ``get_*_system_prompt``
        convention).  Returns the value ``_refresh_recalled_skills`` computed for
        the current turn — ``""`` when nothing was recalled, which the composer
        drops, keeping the prompt byte-identical to a no-procedure build.
        """
        return getattr(self, "_recalled_skill_prompt", "")

    def _refresh_recalled_skills(self, goal: str) -> None:
        """Recompute the per-turn recalled-skill state for ``goal``.

        Recalls the matching procedures **once** and caches both consumers'
        inputs: ``self._recalled_skills`` (the matched ``Skill`` objects, read by
        the tool loader through ``_recalled_skill_tools`` — #1451) and the
        rendered ``self._recalled_skill_prompt`` (the system-prompt block, read by
        ``get_recalled_skills_system_prompt`` — #887).  The single recall keeps
        the loader's SKILL signal free (no second ``recall_skill``).

        Mirrors ``Agent._refresh_active_tool_filter``: it swaps the cached
        injection and rebuilds the system prompt **only when the recalled set
        changes**, so a stable recall (or no recall) leaves the cached prompt —
        and the backend's KV-cache prefix — untouched.  Called per turn from the
        ``process_query`` override with the clean user goal.
        """
        skills, config = self._recall_skills_for_turn(goal)
        self._recalled_skills = skills
        new_prompt = self._build_recalled_skills_prompt(skills, config)
        if new_prompt != getattr(self, "_recalled_skill_prompt", ""):
            self._recalled_skill_prompt = new_prompt
            # rebuild_system_prompt() recomposes via _compose_system_prompt(),
            # which re-invokes get_recalled_skills_system_prompt() and picks up
            # the new value.  Guarded: a host without it (e.g. a bare mixin) just
            # keeps the cached injection for its own composer to read.
            if hasattr(self, "rebuild_system_prompt"):
                self.rebuild_system_prompt()

    # ==================================================================
    # Skill Synthesis (procedural memory, #887)
    # ==================================================================

    def _synthesize_skills(self, since: Optional[str] = None) -> Dict:
        """Synthesize reusable procedures from clusters of successful runs.

        The Step-8 driver of the procedural-memory loop, run once per process
        inside ``_run_memory_post_init`` (off the request hot path).  It wires the
        pure ``skill_synthesis`` pipeline to the live seams: DETECT via
        ``MemoryStore.iter_sessions``, CLUSTER via the 768-dim embedder
        (``_embed_text``), DISTILL via ``self.chat.send_messages``, and
        RECONCILE/STORE into the ``procedures`` table, adding each new row's
        ``when_to_use`` vector to the separate procedures FAISS index.

        Off-states (``docs/plans/skill-synthesis.mdx``): no store
        (``GAIA_MEMORY_DISABLED``) -> no-op; synthesis disabled in
        ``memory_settings.json`` -> skip + log INFO; no chat SDK -> skip + log.

        Fail-loud: an embedder failure re-raises (synthesis cannot proceed
        without embeddings); a distillation LLM call that raises (Lemonade
        unreachable) aborts the whole pass + logs; a malformed distill output
        skips only that cluster.  No smaller-model fallback in any path.

        Args:
            since: ISO 8601 watermark; only ``tool_history`` newer than this is
                considered.  None scans all history.

        Returns:
            A summary dict ``{clusters, stored, skipped}``.
        """
        from gaia.agents.base.memory import (  # deferred (cycle break)
            _embedding_to_blob,
            _load_memory_settings,
        )

        result = {"clusters": 0, "stored": 0, "skipped": 0}

        store = self._memory_store
        if store is None:
            return result  # memory disabled (GAIA_MEMORY_DISABLED) — no store.

        config = load_synthesis_config(_load_memory_settings())
        if not config.enabled:
            logger.info(
                "[MemoryMixin] skill synthesis disabled in memory_settings.json; "
                "skipping pass"
            )
            return result

        if not hasattr(self, "chat"):
            logger.info("[MemoryMixin] no chat SDK available; skipping skill synthesis")
            return result

        # DETECT — cheap SQL; no LLM, no embedder.
        sequences = extract_sequences(store, since=since, min_steps=config.min_steps)
        if not sequences:
            return result

        # CLUSTER — embedder failure RE-RAISES here (fail-loud).
        clusters = cluster_by_goal(
            sequences,
            self._embed_text,
            similarity_tau=config.similarity_tau,
            min_occurrences=config.min_occurrences,
            min_success_rate=config.min_success_rate,
        )
        result["clusters"] = len(clusters)
        if not clusters:
            return result

        for cluster in clusters[: config.max_clusters_per_pass]:
            # DISTILL — a raised error means Lemonade is down: skip the whole
            # pass loudly (no smaller-model fallback), per the off-state table.
            try:
                candidate = distill_cluster(cluster, self.chat.send_messages)
            except Exception as e:
                logger.warning(
                    "[MemoryMixin] skill synthesis pass aborted — distillation LLM "
                    "call failed (Lemonade unreachable?): %s",
                    e,
                )
                break

            if candidate is None:
                result["skipped"] += 1  # SKIP sentinel or malformed — skip cluster.
                continue

            # Embed when_to_use (its own corpus).  Embedder failure RE-RAISES.
            vec = self._embed_text(candidate.when_to_use)
            res = reconcile_and_store(
                candidate,
                cluster,
                store,
                embedding=_embedding_to_blob(vec),
                similarity_tau=config.similarity_tau,
            )
            if res.action in ("add", "update") and res.skill_id:
                self._proc_faiss_add(res.skill_id, vec)
                result["stored"] += 1

        return result
