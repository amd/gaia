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

import threading
import time
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from gaia.agents.base.skill_synthesis import (
    DistilledProcedure,
    GoalCluster,
    SynthesisConfig,
    cluster_by_goal,
    distill_cluster,
    extract_sequences,
    load_synthesis_config,
    reconcile_and_store,
)
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Serializes every touch of a procedures FAISS index. Synthesis now writes to
#: it from a background thread while the live turn searches it, and the native
#: index is not safe for a concurrent add + search.
_PROC_INDEX_LOCK = threading.RLock()


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

        with _PROC_INDEX_LOCK:
            self._proc_faiss_index = index
            self._proc_faiss_id_map = id_map
        logger.info(
            "[MemoryMixin] procedures FAISS index rebuilt: %d vectors", index.ntotal
        )

    def _proc_faiss_add(self, procedure_id: str, vec: np.ndarray) -> None:
        """Add a single procedure vector to the procedures FAISS index.

        Incremental update after a procedure is stored.  Skips if
        ``procedure_id`` is already indexed (idempotent on re-store), mirroring
        ``_faiss_add``.  Called from the background synthesis thread, so the
        index touch is serialized against a concurrent recall search.
        """
        if self._proc_faiss_index is None:
            return
        try:
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            with _PROC_INDEX_LOCK:
                if procedure_id in self._proc_faiss_id_map:
                    return
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

        Raises:
            RuntimeError: on a dimension mismatch with the index, or when a
                second OpenMP runtime makes the native search fatal.
        """
        # Deferred to break the memory <-> procedural_memory import cycle.
        from gaia.agents.base.memory import (
            _validated_faiss_query,
            assert_faiss_omp_safe,
        )

        index = getattr(self, "_proc_faiss_index", None)
        if index is None or index.ntotal == 0:
            return []

        query = _validated_faiss_query(query_vec, index, "procedures")
        k = min(top_k, index.ntotal)
        if k < 1:
            raise ValueError(f"top_k must be >= 1 for a FAISS search, got {top_k}")
        assert_faiss_omp_safe("Procedure recall search")

        results = []
        with _PROC_INDEX_LOCK:
            scores, indices = index.search(query, k)
            for score, idx in zip(scores[0], indices[0]):
                if 0 <= idx < len(self._proc_faiss_id_map):
                    results.append((self._proc_faiss_id_map[idx], float(score)))
        return results

    # ==================================================================
    # Skill Recall (procedural memory, #887 — RECALL)
    # ==================================================================

    def recall_skill(
        self, goal: str, top_k: int = 2, similarity_tau: Optional[float] = None
    ) -> List[DistilledProcedure]:
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
            Matched ``DistilledProcedure`` objects (full bodies; injection
            truncates, the row keeps the full body), best match first; ``[]``
            on any off-state.

        Raises:
            RuntimeError: a stale index (embedding dimension mismatch) or a
                second resident OpenMP runtime — both mean the search cannot
                run, which is a broken install rather than an off-state.
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
            # Distinct from a below-tau miss: there is nothing to even rank.
            # Expected for a new user, but also the first thing to check when
            # "recall never fires" is reported — it tells you immediately
            # whether synthesis ever ran at all.
            logger.info(
                "[MemoryMixin] procedure recall: goal=%r — no candidates "
                "(procedures index is empty)",
                goal[:80],
            )
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
            # Distinct from a below-tau miss (below): the index had nothing to
            # even rank. Without this the "procedures exist but never recall"
            # class of bug (#6.3) is invisible short of reading the DB by hand.
            logger.info(
                "[MemoryMixin] procedure recall: goal=%r — no candidates from "
                "%d indexed procedure(s)",
                goal[:80],
                index.ntotal,
            )
            return []

        tau = (
            similarity_tau
            if similarity_tau is not None
            else load_synthesis_config(_load_memory_settings()).similarity_tau
        )
        skills: List[DistilledProcedure] = []
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
                DistilledProcedure(
                    name=row["name"],
                    when_to_use=row["when_to_use"],
                    body=row["markdown_body"],
                    tools_required=row.get("tools_required") or [],
                )
            )
            recalled_ids.append(procedure_id)

        # Log the outcome either way — the best score vs. tau is exactly what
        # is needed to tell "never even close" from "just short", the question
        # that was previously only answerable by reading the DB directly.
        best_score = matches[0][1]
        if skills:
            logger.info(
                "[MemoryMixin] procedure recall: goal=%r matched %d "
                "procedure(s) (best score=%.3f >= tau=%.3f): %s",
                goal[:80],
                len(skills),
                best_score,
                tau,
                [s.name for s in skills],
            )
        else:
            logger.info(
                "[MemoryMixin] procedure recall: goal=%r — no match cleared "
                "tau (best score=%.3f < tau=%.3f among %d candidate(s))",
                goal[:80],
                best_score,
                tau,
                len(matches),
            )

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
    ) -> Tuple[List[DistilledProcedure], Optional[SynthesisConfig]]:
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
        self, skills: List[DistilledProcedure], config: Optional[SynthesisConfig]
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
        inputs: ``self._recalled_skills`` (the matched ``DistilledProcedure``
        objects, read by the tool loader through ``_recalled_skill_tools`` —
        #1451) and the
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

    def start_skill_synthesis(
        self, *, force: bool = False
    ) -> Optional[threading.Thread]:
        """Run one synthesis pass on a background thread.

        Distillation is several seconds of model time per cluster, and the
        maintenance pass that starts it sits on the user's first query — so the
        pass runs off that turn instead of in front of it.  A pass already in
        flight is left alone (its thread is returned); a host without a store
        starts nothing.

        The thread is a daemon: an unfinished pass never holds the process open,
        and whatever it did not consume is picked up next session.  Errors do
        not vanish — ``_run_skill_synthesis_pass`` logs them at ERROR with a
        traceback and keeps the exception on ``_skill_synthesis_error``.  Use
        ``wait_for_skill_synthesis`` to join it.

        Args:
            force: Re-read history synthesis already consumed (ignores the
                watermark and the per-episode marks).

        Returns:
            The running thread, or None when there is no store to read.
        """
        running = getattr(self, "_skill_synthesis_thread", None)
        if running is not None and running.is_alive():
            logger.debug(
                "[MemoryMixin] skill synthesis already running; "
                "not starting a second pass"
            )
            return running

        if getattr(self, "_memory_store", None) is None:
            return None  # memory disabled (GAIA_MEMORY_DISABLED) — no store.

        self._skill_synthesis_error = None
        thread = threading.Thread(
            target=self._run_skill_synthesis_pass,
            kwargs={"force": force},
            name="gaia-skill-synthesis",
            daemon=True,
        )
        self._skill_synthesis_thread = thread
        logger.info(
            "[MemoryMixin] skill synthesis started (background, force=%s)", force
        )
        thread.start()
        return thread

    def wait_for_skill_synthesis(self, timeout: Optional[float] = None) -> bool:
        """Block until the background synthesis pass finishes.

        The seam a caller or a test uses to observe a pass that would otherwise
        be invisible.  Returns True when no pass is running or it finished
        within ``timeout``; False when it is still going.  A pass that failed
        counts as finished — read ``_skill_synthesis_error`` for the cause.
        """
        thread = getattr(self, "_skill_synthesis_thread", None)
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _run_skill_synthesis_pass(self, force: bool = False) -> None:
        """Run one pass and log its outcome — the background thread's body.

        Thread-boundary translation only: an exception cannot propagate out of
        a thread, so it is logged at ERROR with the traceback and kept on
        ``_skill_synthesis_error`` instead of disappearing.  The pass itself
        stays fail-loud (``_synthesize_skills`` re-raises on an embedder
        failure).
        """
        started = time.monotonic()
        try:
            result = self._synthesize_skills(force=force)
        except Exception as e:
            self._skill_synthesis_error = e
            logger.error(
                "[MemoryMixin] background skill synthesis failed after %.1fs: %s",
                time.monotonic() - started,
                e,
                exc_info=True,
            )
            return

        self._skill_synthesis_result = result
        logger.info(
            "[MemoryMixin] background skill synthesis finished in %.1fs: "
            "%d episode(s) consumed, %d procedure(s) written, "
            "%d cluster(s) not usable%s",
            time.monotonic() - started,
            result["consumed"],
            result["stored"],
            result["skipped"],
            " (stopped at the call cap)" if result["capped"] else "",
        )

    def _synthesize_skills(
        self, since: Optional[str] = None, force: bool = False
    ) -> Dict:
        """Synthesize reusable procedures from clusters of successful runs.

        The Step-8 driver of the procedural-memory loop, started once per
        process by ``_run_memory_post_init`` through ``start_skill_synthesis``
        (background, off the request path).  It wires the pure
        ``skill_synthesis`` pipeline to the live seams: DETECT via
        ``MemoryStore.iter_sessions``, CLUSTER via the 768-dim embedder
        (``_embed_text``), DISTILL via ``self.chat.send_messages``, and
        RECONCILE/STORE into the ``procedures`` table, adding each new row's
        ``when_to_use`` vector to the separate procedures FAISS index.

        Each pass distils only what the previous ones did not:

        * the stored **watermark** is the DETECT ``since``, so history already
          consumed is not re-read;
        * episodes an earlier pass handed to the distiller carry a **mark** and
          are dropped from the window — including ones it could not turn into a
          procedure, which would otherwise be retried on every session start;
        * at most ``max_distill_calls_per_pass`` distillation calls are spent,
          and the watermark is then left at the last fully consumed episode so
          the remainder is picked up next pass rather than skipped.

        Off-states (``docs/plans/skill-synthesis.mdx``): no store
        (``GAIA_MEMORY_DISABLED``) -> no-op; synthesis disabled in
        ``memory_settings.json`` -> skip + log INFO; no chat SDK -> skip + log.

        Fail-loud: an embedder failure re-raises (synthesis cannot proceed
        without embeddings); a distillation LLM call that raises (Lemonade
        unreachable) aborts the whole pass + logs, and those episodes stay
        unmarked so the next pass retries them.  No smaller-model fallback in
        any path.

        Args:
            since: ISO 8601 watermark override; only ``tool_history`` newer than
                this is considered.  None reads the stored watermark.
            force: Ignore the stored watermark and the per-episode marks and
                re-read the whole history — the deliberate retry after a model
                or threshold change made a cluster distillable again.

        Returns:
            ``{clusters, stored, skipped, consumed, capped}`` — ``consumed`` is
            the episodes this pass handed to the distiller, ``capped`` whether
            it stopped at the call cap.
        """
        from gaia.agents.base.memory import (  # deferred (cycle break)
            _embedding_to_blob,
            _load_memory_settings,
        )

        result = {
            "clusters": 0,
            "stored": 0,
            "skipped": 0,
            "consumed": 0,
            "capped": False,
        }

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

        # DETECT — cheap SQL; no LLM, no embedder.  The watermark bounds it to
        # what no previous pass has consumed.
        watermark = since
        if watermark is None and not force:
            watermark = store.get_synthesis_watermark()
        window = extract_sequences(store, since=watermark, min_steps=config.min_steps)
        # The live session keeps producing tool calls; consuming it now would
        # mark it done and hide everything it does for the rest of the session.
        current_session = getattr(self, "_memory_session_id", None)
        window = [s for s in window if s["session_id"] != current_session]
        if not window:
            return result

        pending, already_consumed = self._drop_consumed_episodes(store, window, force)
        consumed_now: Set[str] = set()

        # CLUSTER — embedder failure RE-RAISES here (fail-loud).
        clusters = (
            cluster_by_goal(
                pending,
                self._embed_text,
                similarity_tau=config.similarity_tau,
                min_occurrences=config.min_occurrences,
                min_success_rate=config.min_success_rate,
            )
            if pending
            else []
        )
        result["clusters"] = len(clusters)

        calls = 0
        for cluster in clusters[: config.max_clusters_per_pass]:
            if calls >= config.max_distill_calls_per_pass:
                result["capped"] = True
                logger.info(
                    "[MemoryMixin] skill synthesis stopped at its %d-call cap; "
                    "%d cluster(s) left for the next pass",
                    config.max_distill_calls_per_pass,
                    len(clusters) - calls,
                )
                break
            calls += 1

            # DISTILL — a raised error means Lemonade is down: skip the whole
            # pass loudly (no smaller-model fallback), per the off-state table.
            # The cluster stays unmarked, so the next pass retries it.
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
                # SKIP sentinel, malformed, or truncated at the model's output
                # cap. Marked, so it is not re-attempted every session start.
                result["skipped"] += 1
                consumed_now.update(
                    self._mark_cluster_consumed(
                        store,
                        cluster,
                        "unusable",
                        detail=(
                            "distiller returned SKIP or a document that did not "
                            "parse (a response truncated at the output cap lands "
                            "here too)"
                        ),
                    )
                )
                logger.info(
                    "[MemoryMixin] skill synthesis: %d episode(s) for goal=%r "
                    "marked undistillable and will not be retried — "
                    "_synthesize_skills(force=True) or "
                    "MemoryStore.reset_synthesis_progress() re-opens them",
                    cluster.occurrences,
                    cluster.goal[:80],
                )
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
            consumed_now.update(
                self._mark_cluster_consumed(store, cluster, "distilled")
            )
            if res.action in ("add", "update") and res.skill_id:
                self._proc_faiss_add(res.skill_id, vec)
                result["stored"] += 1

        result["consumed"] = len(consumed_now)
        self._advance_synthesis_watermark(
            store, window, already_consumed | consumed_now
        )
        return result

    @staticmethod
    def _drop_consumed_episodes(
        store, window: List[Dict], force: bool
    ) -> Tuple[List[Dict], Set[str]]:
        """Split ``window`` into episodes still to distil and ones already done.

        An episode is done once a pass handed it to the distiller, whatever the
        outcome — that is what stops a cluster the model could not distil from
        being re-attempted on every session start.  ``force`` ignores the marks
        so the whole window is re-read.

        Returns:
            ``(pending, already_consumed_ids)``.
        """
        if force:
            return list(window), set()

        marks = store.get_synthesis_marks([s["session_id"] for s in window])
        if not marks:
            return list(window), set()

        unusable = [
            mark for mark in marks.values() if mark.get("outcome") == "unusable"
        ]
        if unusable:
            logger.info(
                "[MemoryMixin] skill synthesis: %d episode(s) still skipped — an "
                "earlier pass could not distil them (e.g. goal=%r: %s). "
                "_synthesize_skills(force=True) or "
                "MemoryStore.reset_synthesis_progress() retries them",
                len(unusable),
                str(unusable[0].get("goal"))[:80],
                unusable[0].get("detail"),
            )
        return [s for s in window if s["session_id"] not in marks], set(marks)

    @staticmethod
    def _mark_cluster_consumed(
        store, cluster: GoalCluster, outcome: str, detail: Optional[str] = None
    ) -> List[str]:
        """Record a cluster's episodes as consumed; return their session ids."""
        session_ids = cluster.from_sessions
        store.mark_sessions_synthesized(
            session_ids, outcome, goal=cluster.goal, detail=detail
        )
        return session_ids

    @staticmethod
    def _advance_synthesis_watermark(
        store, window: List[Dict], consumed_ids: Set[str]
    ) -> Optional[str]:
        """Move the watermark to the last *fully consumed* episode in ``window``.

        The watermark is a single timestamp, so it may only pass a contiguous
        run of consumed episodes: it stops just below the oldest episode this
        pass left behind (one the call cap cut off, or one still short of
        ``min_occurrences``).  Anything above it stays in the next pass's window
        instead of being silently skipped.  It never moves backwards, so an
        explicit older ``since`` cannot re-open consumed history.

        Returns:
            The watermark now stored, or None when it did not move.
        """
        unconsumed = [s for s in window if s["session_id"] not in consumed_ids]
        if unconsumed:
            starts = [s["started_at"] for s in unconsumed if s.get("started_at")]
            if not starts:
                return None  # no floor to stop below — do not risk skipping work
            floor = min(starts)
            stamps = [
                s["last_at"]
                for s in window
                if s["session_id"] in consumed_ids
                and s.get("last_at")
                and s["last_at"] < floor
            ]
        else:
            stamps = [s["last_at"] for s in window if s.get("last_at")]
        if not stamps:
            return None

        candidate = max(stamps)
        stored = store.get_synthesis_watermark()
        if stored is not None and candidate <= stored:
            return None
        store.set_synthesis_watermark(candidate)
        logger.debug(
            "[MemoryMixin] skill synthesis watermark advanced to %s "
            "(%d episode(s) consumed, %d left for the next pass)",
            candidate,
            len(consumed_ids),
            len(unconsumed),
        )
        return candidate
