# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Skill auto-synthesis pipeline — procedural memory (GAIA #887).

This module is the DETECT → CLUSTER → DISTILL → RECONCILE half of the procedural
memory loop.  It turns clusters of successful tool sequences (recorded in
``tool_history``) into reusable, ``SKILL.md``-shaped procedures stored in the
``procedures`` table.

The pipeline reuses the #606 memory primitives (the 768-dim embedder, the
``self.chat`` LLM seam, the ``MemoryStore``).  The functions here are pure-ish
(they take the store / an embed callable / a send-messages callable as
arguments) so they can be unit-tested without a live backend; the
``MemoryMixin._synthesize_skills`` driver in ``memory.py`` wires them to the real
seams and is the only stateful caller.

Two corpora, one format: the on-disk #691 ``SKILL.md`` schema is *referenced*
here, never redefined.  The LLM emits only the four *derived* fields
(``name``, ``when_to_use``, ``tools_required``, body); ``Skill.parse`` validates
that intermediate shape and ``Skill.to_skill_md`` injects the two *fixed*
constants (``license: MIT``, ``version: 1.0.0``) and maps to the canonical
document.  See ``docs/plans/skill-synthesis.mdx`` "The format contract".

Fail-loud posture (``docs/plans/skill-synthesis.mdx`` "Off-states as safe
floors"):
  * embedder failure  -> re-raise (synthesis cannot proceed without embeddings);
  * Lemonade down during distillation -> caller skips the whole pass + logs;
  * malformed distill output -> ``distill_cluster`` returns ``None`` (skip that
    cluster + log); never an auto-fix, never a smaller-model fallback.
"""

import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import numpy as np
import yaml

from gaia.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# Thresholds (the maintainer's constants; overridable via memory_settings.json)
# ============================================================================

#: A successful tool span shorter than this is not worth distilling.
MIN_STEPS: int = 3

#: A cluster needs at least this many similar successful runs to qualify.
MIN_OCCURRENCES: int = 3

#: A cluster must be at least this successful (success_count / attempt_count).
MIN_SUCCESS_RATE: float = 0.80

#: Cosine threshold on goal embeddings, for clustering (and, in Phase 2, recall).
SIMILARITY_TAU: float = 0.82

#: Caps LLM distillation calls per synthesis pass — bounds cost.
MAX_CLUSTERS_PER_PASS: int = 10

#: Per-body cap on the recall-time injection (Phase 2 consumer; defined here so
#: the threshold lives with its siblings).  Full body always stays in the row.
MAX_RECALL_BODY_CHARS: int = 1500

#: Low temperature for the single distillation call per cluster.
DISTILL_TEMPERATURE: float = 0.1

#: Token budget for one distillation response (a single-screen procedure).
DISTILL_MAX_TOKENS: int = 1024

#: Literal the distiller emits when a cluster is too dissimilar / trivial.
SKIP_SENTINEL: str = "SKIP"

#: Agent Skills name rule (``skill-format.mdx``): lowercase alphanumerics with
#: single hyphens, no leading / trailing / consecutive hyphen.
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_MAX_NAME_LEN: int = 64

#: Key in ``memory_settings.json`` carrying threshold overrides.
_SETTINGS_SECTION: str = "skill_synthesis"

#: Max enabled procedures scanned for the reconcile cosine match. The corpus is
#: bounded (synthesis is gated + off-by-default), so this is generous; saturating
#: it is logged loudly rather than silently dropping rows (#1818).
_RECONCILE_SCAN_LIMIT: int = 500


@dataclass(frozen=True)
class SynthesisConfig:
    """Resolved synthesis thresholds for one pass.

    Defaults are the maintainer's constants; ``load_synthesis_config`` overlays
    any ``skill_synthesis`` section from ``~/.gaia/memory_settings.json``.
    """

    enabled: bool = True
    min_steps: int = MIN_STEPS
    min_occurrences: int = MIN_OCCURRENCES
    min_success_rate: float = MIN_SUCCESS_RATE
    similarity_tau: float = SIMILARITY_TAU
    max_clusters_per_pass: int = MAX_CLUSTERS_PER_PASS
    max_recall_body_chars: int = MAX_RECALL_BODY_CHARS


def load_synthesis_config(settings: Optional[Dict] = None) -> SynthesisConfig:
    """Resolve the synthesis thresholds, overlaying memory-settings overrides.

    The caller (``MemoryMixin``) owns reading ``~/.gaia/memory_settings.json``
    and passes the parsed dict in, so this module performs no file I/O and never
    imports ``memory.py`` (avoiding a circular import).  An absent or malformed
    section leaves every threshold at its maintainer-chosen default.

    Args:
        settings: The parsed ``memory_settings.json`` mapping, or None.  Only its
            ``"skill_synthesis"`` sub-mapping is read.

    Returns:
        A ``SynthesisConfig``; defaults when no override is present.
    """
    section: Dict = {}
    if isinstance(settings, dict):
        candidate = settings.get(_SETTINGS_SECTION)
        if isinstance(candidate, dict):
            section = candidate

    def _num(key: str, default, cast):
        if key not in section:
            return default
        try:
            return cast(section[key])
        except (TypeError, ValueError):
            logger.warning(
                "[skill_synthesis] ignoring invalid %s.%s=%r in memory_settings.json",
                _SETTINGS_SECTION,
                key,
                section[key],
            )
            return default

    return SynthesisConfig(
        enabled=bool(section.get("enabled", True)),
        min_steps=_num("min_steps", MIN_STEPS, int),
        min_occurrences=_num("min_occurrences", MIN_OCCURRENCES, int),
        min_success_rate=_num("min_success_rate", MIN_SUCCESS_RATE, float),
        similarity_tau=_num("similarity_tau", SIMILARITY_TAU, float),
        max_clusters_per_pass=_num("max_clusters_per_pass", MAX_CLUSTERS_PER_PASS, int),
        max_recall_body_chars=_num("max_recall_body_chars", MAX_RECALL_BODY_CHARS, int),
    )


# ============================================================================
# Skill — the intermediate (pre-#691) shape + the canonical mapping
# ============================================================================


@dataclass(frozen=True)
class Skill:
    """A distilled procedure in the *intermediate* four-field shape.

    These are the only fields the LLM derives.  The fixed constants
    (``license``, ``version``) are NOT carried here — they are injected by
    ``to_skill_md`` so the model can never emit a wrong value.

    Attributes:
        name: kebab-case identifier (``^[a-z0-9]+(-[a-z0-9]+)*$``, <= 64 chars).
        when_to_use: the trigger boundary; maps to #691 ``description`` and is
            the text embedded for ``recall_skill`` matching.
        body: the full Markdown procedure, including a ``## Edge cases`` section.
        tools_required: registry tool names the procedure consumes (recipe
            contract; maps to ``metadata.gaia.tools_required``).
    """

    name: str
    when_to_use: str
    body: str
    tools_required: List[str] = field(default_factory=list)

    @classmethod
    def parse(cls, intermediate_md: str) -> Optional["Skill"]:
        """Validate the distiller's intermediate Markdown into a ``Skill``.

        The expected shape is YAML frontmatter (``name``, ``when_to_use``,
        ``tools_required``) delimited by ``---`` lines, followed by the Markdown
        body.  The literal ``"SKIP"`` (the distiller's opt-out) and any
        structurally invalid document return ``None`` — the caller skips that
        cluster.  No truncation, no auto-fix.

        Args:
            intermediate_md: Raw distiller output (already stripped of think
                tags / code fences by ``distill_cluster``).

        Returns:
            A validated ``Skill``, or ``None`` for ``SKIP`` / malformed input.
        """
        if intermediate_md is None:
            return None
        text = intermediate_md.strip()
        if not text or text == SKIP_SENTINEL:
            return None

        match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
        if match is None:
            logger.info("[skill_synthesis] distill output missing YAML frontmatter")
            return None

        frontmatter_raw, body = match.group(1), match.group(2).strip()
        try:
            frontmatter = yaml.safe_load(frontmatter_raw)
        except yaml.YAMLError as e:
            logger.info(
                "[skill_synthesis] distill frontmatter is not valid YAML: %s", e
            )
            return None

        if not isinstance(frontmatter, dict):
            logger.info("[skill_synthesis] distill frontmatter is not a mapping")
            return None

        name = frontmatter.get("name")
        when_to_use = frontmatter.get("when_to_use")
        tools_required = frontmatter.get("tools_required", [])

        if (
            not isinstance(name, str)
            or not _NAME_RE.match(name)
            or len(name) > _MAX_NAME_LEN
        ):
            logger.info("[skill_synthesis] distill rejected — invalid name %r", name)
            return None
        if not isinstance(when_to_use, str) or not when_to_use.strip():
            logger.info("[skill_synthesis] distill rejected — empty when_to_use")
            return None
        if not body:
            logger.info("[skill_synthesis] distill rejected — empty body")
            return None
        if tools_required is None:
            tools_required = []
        if not isinstance(tools_required, list) or not all(
            isinstance(t, str) for t in tools_required
        ):
            logger.info(
                "[skill_synthesis] distill rejected — tools_required not a string list"
            )
            return None

        return cls(
            name=name,
            when_to_use=when_to_use.strip(),
            body=body,
            tools_required=list(tools_required),
        )

    def to_skill_md(self) -> str:
        """Render the canonical #691 ``SKILL.md`` document.

        Maps the four derived fields to the locked schema (``when_to_use`` ->
        ``description``, ``tools_required`` -> ``metadata.gaia.tools_required``)
        and injects the two fixed constants (``license: MIT``,
        ``version: 1.0.0``).  The result validates under the Agent Skills format.

        Returns:
            The full ``SKILL.md`` text (frontmatter + body).
        """
        frontmatter = {
            "name": self.name,
            "description": self.when_to_use,
            "license": "MIT",
            "version": "1.0.0",
            "metadata": {"gaia": {"tools_required": list(self.tools_required)}},
        }
        front_yaml = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        return f"---\n{front_yaml}\n---\n\n{self.body}\n"


# ============================================================================
# GoalCluster — aggregate of similar successful sessions
# ============================================================================


@dataclass(frozen=True)
class GoalCluster:
    """A cluster of similar successful sessions (the CLUSTER output).

    ``members`` are the per-session dicts produced by
    ``MemoryStore.iter_sessions``.  The aggregate properties form the empirical
    track record stored on the procedure row.
    """

    goal: str
    members: tuple

    @property
    def occurrences(self) -> int:
        """Number of distinct sessions backing this cluster."""
        return len(self.members)

    @property
    def success_count(self) -> int:
        """Total successful tool calls across the cluster's sessions."""
        return sum(int(m.get("success_count", 0)) for m in self.members)

    @property
    def attempt_count(self) -> int:
        """Total tool calls (success + failure) across the cluster's sessions."""
        return sum(int(m.get("attempt_count", 0)) for m in self.members)

    @property
    def success_rate(self) -> float:
        """Empirical success rate; 0.0 when no attempts are recorded."""
        attempts = self.attempt_count
        return self.success_count / attempts if attempts else 0.0

    @property
    def from_sessions(self) -> List[str]:
        """Session ids this cluster was distilled from (provenance trail)."""
        return [m["session_id"] for m in self.members]

    def representative(self) -> Dict:
        """The exemplar session — the one with the most successful steps."""
        return max(self.members, key=lambda m: int(m.get("success_count", 0)))

    def tool_sequence(self) -> List[Dict]:
        """The exemplar's ordered tool pattern, names only (drops noisy args)."""
        return [
            {"tool": step["tool"]}
            for step in self.representative().get("tool_sequence", [])
        ]


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of ``reconcile_and_store`` for one distilled candidate.

    Attributes:
        action: ``"add"`` (new row), ``"update"`` (new row + old superseded), or
            ``"noop"`` (existing row already dominates).
        skill_id: The stored procedure id for ``add`` / ``update``; None on noop.
        superseded_id: The id superseded by an ``update``, else None.
    """

    action: str
    skill_id: Optional[str] = None
    superseded_id: Optional[str] = None


# ============================================================================
# DISTILL prompt
# ============================================================================

DISTILL_SYSTEM_PROMPT = """\
You distill ONE reusable procedure from several successful runs of the same task.

You are given a recurring user goal and the tool sequence the agent used to
accomplish it successfully multiple times. Write the procedure as a Markdown
document with YAML frontmatter, in EXACTLY this shape and nothing else:

---
name: <kebab-case-identifier>
when_to_use: <one or two sentences describing when this procedure applies>
tools_required: [tool_a, tool_b]
---

# <Title>

1. <step that uses a tool>
2. <step>

## Edge cases
- <edge case>

Rules:
- name: lowercase letters, digits and single hyphens only
  (^[a-z0-9]+(-[a-z0-9]+)*$), 64 characters or fewer.
- when_to_use: the trigger boundary — the goal or request this procedure covers.
  Its embedding is what future goals are matched against, so be specific.
- tools_required: only tool names that appear in the observed sequence.
- Write the FULL procedure inline, including a "## Edge cases" section.
- Do NOT emit version, license, security_tier, permissions, or any other field —
  those are injected later, not authored by you.
- If the runs are too dissimilar or too trivial to generalize into one reusable
  procedure, output exactly: SKIP
"""


def _build_distill_user_prompt(cluster: GoalCluster) -> str:
    """Render the per-cluster user message handed to the distiller."""
    tool_names = [step["tool"] for step in cluster.tool_sequence()]
    numbered = "\n".join(f"{i + 1}. {name}" for i, name in enumerate(tool_names))
    other_goals = [
        m.get("goal", "")
        for m in cluster.members
        if m.get("goal") and m.get("goal") != cluster.goal
    ]
    other_block = ""
    if other_goals:
        bullets = "\n".join(f"- {g}" for g in other_goals[:5])
        other_block = (
            f"\n\nOther phrasings of the same goal in this cluster:\n{bullets}"
        )
    return (
        f"This task succeeded across {cluster.occurrences} sessions "
        f"(success rate {cluster.success_rate:.0%}). Distill it into ONE reusable "
        f"procedure.\n\n"
        f"Recurring user goal:\n{cluster.goal}\n\n"
        f"Observed successful tool sequence (the most complete run):\n{numbered}"
        f"{other_block}\n\n"
        f"Emit the intermediate SKILL frontmatter + body described in the system "
        f"prompt, or SKIP."
    )


# ============================================================================
# Pipeline — DETECT -> CLUSTER -> DISTILL -> RECONCILE/STORE
# ============================================================================


def extract_sequences(
    store, since: Optional[str] = None, min_steps: int = MIN_STEPS
) -> List[Dict]:
    """DETECT — per-session successful tool spans + the session goal.

    A thin adapter over ``MemoryStore.iter_sessions`` (the single-query DETECT
    primitive added in Phase 0).  Runs no LLM and no embedder; the heavy
    per-session eligibility filter runs in SQL.

    Args:
        store: The ``MemoryStore`` to read ``tool_history`` from.
        since: ISO 8601 watermark; only tool calls strictly newer are considered.
        min_steps: Minimum successful tool calls for a session to qualify.

    Returns:
        The per-session dicts ``iter_sessions`` returns (oldest session first).
    """
    return store.iter_sessions(since=since, min_steps=min_steps)


def cluster_by_goal(
    sequences: List[Dict],
    embed_fn: Callable[[str], np.ndarray],
    similarity_tau: float = SIMILARITY_TAU,
    min_occurrences: int = MIN_OCCURRENCES,
    min_success_rate: float = MIN_SUCCESS_RATE,
) -> List[GoalCluster]:
    """CLUSTER — group sessions whose goals embed within ``similarity_tau``.

    Embeds each session's goal with ``embed_fn`` (the #606 768-dim embedder)
    and agglomerates greedily: a seed session opens a cluster and every
    not-yet-assigned session whose goal cosine-similarity to the seed is
    ``>= similarity_tau`` joins it.  Embeddings are assumed L2-normalized
    (``_embed_text`` guarantees this), so cosine == dot product.

    Seeds are taken in a deterministic, content-derived order (goal text, then
    session id), so the result is independent of the order ``sequences`` arrives
    in — the same set of sessions always produces the same clusters, members,
    and representative goal.

    Sessions without a goal are skipped (a goal is required to cluster).  Only
    clusters with ``>= min_occurrences`` sessions AND ``>= min_success_rate``
    aggregate success rate are returned.

    Fail-loud: ``embed_fn`` failures propagate (re-raise) — synthesis cannot
    proceed without embeddings.

    Args:
        sequences: Per-session dicts from ``extract_sequences``.
        embed_fn: Callable mapping goal text to an L2-normalized vector.
        similarity_tau: Cosine threshold for two goals to share a cluster.
        min_occurrences: Minimum sessions for a cluster to qualify.
        min_success_rate: Minimum aggregate success rate for a cluster to qualify.

    Returns:
        Qualifying ``GoalCluster`` objects.
    """
    embedded: List[tuple] = []
    # Memoize by goal text within the pass: identical goals embed to the identical
    # vector, so the same recurring task (the procedural case — one goal succeeding
    # many times) costs one embedder round-trip instead of one per session. Bounds
    # the per-pass embed count by *distinct* goals, not session count.
    embed_cache: Dict[str, np.ndarray] = {}
    for session in sequences:
        goal = session.get("goal")
        if not goal or not str(goal).strip():
            logger.debug(
                "[skill_synthesis] skipping session %s: no goal to cluster on",
                session.get("session_id"),
            )
            continue
        vec = embed_cache.get(goal)
        if vec is None:
            vec = np.asarray(embed_fn(goal), dtype=np.float32)  # failure RE-RAISES
            embed_cache[goal] = vec
        embedded.append((session, vec))

    # Deterministic, content-derived seed order (goal text, then session id) so
    # cluster membership and the representative goal never hinge on the arbitrary
    # session-id order iter_sessions returns: the same set of sessions yields the
    # same clusters across runs and across any future `since` window.
    embedded.sort(
        key=lambda pair: (str(pair[0]["goal"]).strip().lower(), pair[0]["session_id"])
    )

    assigned = [False] * len(embedded)
    clusters: List[GoalCluster] = []
    for i in range(len(embedded)):
        if assigned[i]:
            continue
        seed_session, seed_vec = embedded[i]
        members = [seed_session]
        assigned[i] = True
        for j in range(i + 1, len(embedded)):
            if assigned[j]:
                continue
            sim = float(np.dot(seed_vec, embedded[j][1]))
            if sim >= similarity_tau:
                members.append(embedded[j][0])
                assigned[j] = True
        clusters.append(
            GoalCluster(goal=str(seed_session["goal"]), members=tuple(members))
        )

    qualifying = [
        c
        for c in clusters
        if c.occurrences >= min_occurrences and c.success_rate >= min_success_rate
    ]
    logger.debug(
        "[skill_synthesis] clustered %d sessions into %d clusters, %d qualifying",
        len(embedded),
        len(clusters),
        len(qualifying),
    )
    return qualifying


def distill_cluster(
    cluster: GoalCluster, send_messages_fn: Callable
) -> Optional[Skill]:
    """DISTILL — one low-temperature LLM call turning a cluster into a ``Skill``.

    Reuses the same ``self.chat.send_messages`` seam the extraction /
    consolidation passes use (no new client).  The response is stripped of think
    tags and code fences, then validated by ``Skill.parse``.

    Fail-loud split: a raised exception from ``send_messages_fn`` (Lemonade
    unreachable) propagates so the caller can skip the WHOLE pass; a structurally
    invalid response (or the ``SKIP`` sentinel) returns ``None`` so the caller
    skips only THIS cluster.

    Args:
        cluster: The qualifying cluster to distill.
        send_messages_fn: The ``self.chat.send_messages`` callable.

    Returns:
        A validated ``Skill``, or ``None`` for ``SKIP`` / malformed output.
    """
    response = send_messages_fn(
        messages=[{"role": "user", "content": _build_distill_user_prompt(cluster)}],
        system_prompt=DISTILL_SYSTEM_PROMPT,
        temperature=DISTILL_TEMPERATURE,
        max_tokens=DISTILL_MAX_TOKENS,
    )

    raw_text = response.text if hasattr(response, "text") else str(response)
    raw_text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:[a-zA-Z]+)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text).strip()

    skill = Skill.parse(raw_text)
    if skill is None:
        logger.info(
            "[skill_synthesis] no usable skill from cluster goal=%r (SKIP or malformed)",
            cluster.goal,
        )
    return skill


def _nearest_enabled_procedure(
    store,
    candidate_blob: Optional[bytes],
    similarity_tau: float,
) -> Optional[Dict]:
    """Find the enabled, non-superseded procedure nearest the candidate by meaning.

    Reconcile matches by ``when_to_use`` embedding cosine rather than exact
    ``name`` (#1818): the distiller authors a fresh kebab ``name`` each pass, so a
    recurring goal drifts (``summarize-unread-emails`` ->
    ``summarize-my-unread-emails``) and an exact-name match would ADD a duplicate
    instead of superseding.  Matching on the trigger vector keeps one proven
    recipe per goal across phrasing drift.

    The BLOB is the raw ``memory._embedding_to_blob`` float32 layout, decoded
    locally with ``np.frombuffer`` (never importing ``memory``) so the
    ``memory -> procedural_memory -> skill_synthesis`` import direction stays
    one-way.  Vectors are L2-normalized at storage (``_embed_text``), but the
    candidate and each row are re-normalized defensively so cosine == dot product.

    Args:
        store: The ``MemoryStore`` to scan (its ``search_skills`` is the boundary).
        candidate_blob: The candidate's ``when_to_use`` embedding BLOB.  Falsy or
            zero-norm means there is no meaning to match on -> treated as new.
        similarity_tau: Cosine threshold; a row matches iff its score is
            ``>= similarity_tau`` (the same tau used to cluster goals).

    Returns:
        The single highest-cosine procedure dict clearing ``similarity_tau``
        (newest-first on ties, so the most recent row wins deterministically), or
        None when nothing clears it or the candidate has no usable vector.
    """
    if not candidate_blob:
        return None
    cand = np.frombuffer(candidate_blob, dtype=np.float32)
    cand_norm = np.linalg.norm(cand)
    if cand_norm == 0:
        return None
    cand = cand / cand_norm

    rows = store.search_skills(
        enabled_only=True,
        include_superseded=False,
        with_embedding=True,
        limit=_RECONCILE_SCAN_LIMIT,
    )
    if len(rows) >= _RECONCILE_SCAN_LIMIT:
        logger.warning(
            "[skill_synthesis] reconcile scan hit the %d-row cap; a drifted "
            "duplicate beyond it could be missed (#1818)",
            _RECONCILE_SCAN_LIMIT,
        )

    best: Optional[Dict] = None
    best_score = -1.0
    for row in rows:
        blob = row.get("embedding")
        if not blob:
            continue
        vec = np.frombuffer(blob, dtype=np.float32)
        if vec.shape[0] != cand.shape[0]:  # wrong dim — not comparable, skip
            continue
        norm = np.linalg.norm(vec)
        if norm == 0:
            continue
        score = float(np.dot(cand, vec / norm))
        if score > best_score:  # strict-max keeps the newest row on ties
            best_score = score
            best = row

    return best if best_score >= similarity_tau else None


def reconcile_and_store(
    candidate: Skill,
    cluster: GoalCluster,
    store,
    embedding: Optional[bytes] = None,
    similarity_tau: float = SIMILARITY_TAU,
) -> ReconcileResult:
    """RECONCILE/STORE — persist a distilled candidate as ADD / UPDATE / NOOP.

    Matches an existing enabled, non-superseded procedure by **``when_to_use``
    embedding cosine ``>= similarity_tau``** (not by ``name``): the distiller
    authors a fresh kebab ``name`` each pass, so a recurring goal drifts across
    passes and a name match would ADD a duplicate instead of superseding (#1818).

    * no match (nothing clears ``similarity_tau``) -> **ADD** a new row (Mem0 ADD).
    * the candidate's cluster has a higher ``success_count`` than the matched
      row -> **UPDATE**: store a new row and mark the old one ``superseded_by`` it
      (Zep lineage — the same insert-new-then-supersede shape #606 uses for a
      knowledge UPDATE).  The old row is kept, never deleted.
    * otherwise -> **NOOP**.

    No path ever DELETEs.  ``success_count`` is the dominance signal because it is
    the procedure's empirical track record (the issue's stated supersede rule);
    only the *match key* moved from name to meaning — dominance is unchanged.

    Args:
        candidate: The distilled ``Skill`` (intermediate fields).
        cluster: The cluster it was distilled from (track record + provenance).
        store: The ``MemoryStore`` to write to.
        embedding: The ``when_to_use`` embedding BLOB — now both persisted and
            used as the match vector (previously persist-only).
        similarity_tau: Cosine threshold for treating a stored procedure as the
            same goal; defaults to the clustering ``SIMILARITY_TAU``.

    Returns:
        A ``ReconcileResult`` describing the action taken.
    """
    provenance = {"source": "synthesized", "from_sessions": cluster.from_sessions}
    prior = _nearest_enabled_procedure(store, embedding, similarity_tau)

    def _insert() -> str:
        return store.put_skill(
            name=candidate.name,
            when_to_use=candidate.when_to_use,
            markdown_body=candidate.body,
            tools_required=candidate.tools_required,
            tool_sequence=cluster.tool_sequence(),
            success_count=cluster.success_count,
            attempt_count=cluster.attempt_count,
            provenance=provenance,
            embedding=embedding,
        )

    if prior is None:
        new_id = _insert()
        logger.info(
            "[skill_synthesis] ADD procedure %s name=%s", new_id, candidate.name
        )
        return ReconcileResult(action="add", skill_id=new_id)

    if cluster.success_count > int(prior.get("success_count", 0)):
        new_id = _insert()
        store.supersede_skill(prior["id"], new_id)
        logger.info(
            "[skill_synthesis] UPDATE procedure %s supersedes %s "
            "(matched by meaning, new name=%s, prior name=%s)",
            new_id,
            prior["id"],
            candidate.name,
            prior.get("name"),
        )
        return ReconcileResult(
            action="update", skill_id=new_id, superseded_id=prior["id"]
        )

    logger.info(
        "[skill_synthesis] NOOP procedure: existing %s already dominates "
        "(matched by meaning, candidate name=%s, prior name=%s)",
        prior["id"],
        candidate.name,
        prior.get("name"),
    )
    return ReconcileResult(action="noop")
