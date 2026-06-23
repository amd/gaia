# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the skill-synthesis pipeline (procedural memory, #887).

Covers the pure pipeline in ``gaia.agents.base.skill_synthesis``:
``Skill.parse`` / ``Skill.to_skill_md`` round-trip and the #691 emit/inject
split, ``load_synthesis_config`` overrides, ``cluster_by_goal`` grouping at the
cosine threshold, ``distill_cluster`` contract-shape + fail-loud split, and
``reconcile_and_store`` ADD / UPDATE / NOOP (never DELETE).

All tests run without a live backend — the embedder and the chat LLM are passed
in as plain callables, and store-backed tests use a temp-file ``MemoryStore``.
"""

import logging
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import yaml

from gaia.agents.base.memory_store import MemoryStore
from gaia.agents.base.skill_synthesis import (
    _RECONCILE_SCAN_LIMIT,
    DISTILL_SYSTEM_PROMPT,
    DISTILL_TEMPERATURE,
    SIMILARITY_TAU,
    GoalCluster,
    ReconcileResult,
    Skill,
    SynthesisConfig,
    _build_distill_user_prompt,
    _nearest_enabled_procedure,
    cluster_by_goal,
    distill_cluster,
    load_synthesis_config,
    reconcile_and_store,
)


@pytest.fixture
def store(tmp_path):
    """A fresh temp-file MemoryStore (v3 schema with the procedures table)."""
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INTERMEDIATE_MD = """\
---
name: triage-support-ticket
when_to_use: Triage an inbound support ticket end to end. Use when the user pastes a ticket.
tools_required: [query_documents, read_file, remember]
---

# Triage a Support Ticket

1. Pull matching policy docs with `query_documents`.
2. Read any attached log with `read_file`.
3. Record the disposition with `remember`.

## Edge cases
- If no policy doc matches, escalate rather than guessing.
"""


def _unit(values):
    """Return an L2-normalized float32 vector from a list of components."""
    vec = np.array(values, dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm else vec


def _session(session_id, goal, tools, success_count=None, attempt_count=None):
    """Build a minimal iter_sessions-shaped session dict."""
    success_count = len(tools) if success_count is None else success_count
    attempt_count = success_count if attempt_count is None else attempt_count
    return {
        "session_id": session_id,
        "goal": goal,
        "tools": tools,
        "tool_sequence": [{"tool": t, "args": {}} for t in tools],
        "success_count": success_count,
        "attempt_count": attempt_count,
        "started_at": "2026-01-01T00:00:00Z",
        "last_at": "2026-01-01T00:01:00Z",
    }


def _llm_returning(text):
    """A send_messages mock whose response exposes .text == *text*."""
    mock = MagicMock()
    mock.return_value = MagicMock(text=text)
    return mock


def _cluster(goal="do the thing", n=3, tools=("a", "b", "c")):
    members = tuple(_session(f"sess_{i}", goal, list(tools)) for i in range(n))
    return GoalCluster(goal=goal, members=members)


# ===========================================================================
# Skill.parse / to_skill_md — the emit/inject split
# ===========================================================================


class TestSkillParse:
    """Skill.parse validates the intermediate four-field shape."""

    def test_parses_valid_intermediate(self):
        skill = Skill.parse(_INTERMEDIATE_MD)
        assert skill is not None
        assert skill.name == "triage-support-ticket"
        assert skill.when_to_use.startswith("Triage an inbound support ticket")
        assert skill.tools_required == ["query_documents", "read_file", "remember"]
        assert "## Edge cases" in skill.body
        # The intermediate shape carries NO license/version — those are injected.
        assert "license" not in skill.body

    def test_skip_sentinel_returns_none(self):
        assert Skill.parse("SKIP") is None
        assert Skill.parse("  SKIP  ") is None

    def test_missing_frontmatter_returns_none(self):
        assert Skill.parse("# Just a heading\n\nno frontmatter here") is None

    def test_empty_returns_none(self):
        assert Skill.parse("") is None
        assert Skill.parse(None) is None

    @pytest.mark.parametrize(
        "bad_name",
        ["Triage_Ticket", "triage ticket", "-leading", "trailing-", "UPPER", "a--b"],
    )
    def test_invalid_name_rejected(self, bad_name):
        md = _INTERMEDIATE_MD.replace("triage-support-ticket", bad_name, 1)
        assert Skill.parse(md) is None

    def test_name_over_64_chars_rejected(self):
        long_name = "a" * 65
        md = _INTERMEDIATE_MD.replace("triage-support-ticket", long_name, 1)
        assert Skill.parse(md) is None

    def test_empty_when_to_use_rejected(self):
        md = """\
---
name: valid-name
when_to_use: ""
tools_required: [a]
---

# Body
1. step
"""
        assert Skill.parse(md) is None

    def test_empty_body_rejected(self):
        md = "---\nname: valid-name\nwhen_to_use: trigger text\ntools_required: [a]\n---\n"
        assert Skill.parse(md) is None

    def test_tools_required_defaults_to_empty_list(self):
        md = """\
---
name: no-tools
when_to_use: A procedure that consumes no registry tools.
---

# Body
1. think
"""
        skill = Skill.parse(md)
        assert skill is not None
        assert skill.tools_required == []

    def test_non_string_tools_required_rejected(self):
        md = _INTERMEDIATE_MD.replace(
            "tools_required: [query_documents, read_file, remember]",
            "tools_required: [1, 2, 3]",
        )
        assert Skill.parse(md) is None

    def test_tolerates_code_fence_free_intermediate(self):
        # Skill.parse expects fence-free text (distill_cluster strips fences),
        # but a stray trailing newline must not break parsing.
        skill = Skill.parse(_INTERMEDIATE_MD + "\n\n")
        assert skill is not None


class TestSkillToSkillMd:
    """to_skill_md injects the fixed constants and emits a valid #691 document."""

    def test_round_trips_to_valid_691_document(self):
        skill = Skill.parse(_INTERMEDIATE_MD)
        doc = skill.to_skill_md()

        # Split frontmatter from body and parse the YAML.
        assert doc.startswith("---\n")
        _, frontmatter_raw, body = doc.split("---\n", 2)
        front = yaml.safe_load(frontmatter_raw)

        # Required #691 fields present, with the injected constants.
        assert front["name"] == "triage-support-ticket"
        assert front["description"].startswith("Triage an inbound support ticket")
        assert front["license"] == "MIT"  # injected, not emitted by the LLM
        assert front["version"] == "1.0.0"  # injected, not emitted by the LLM
        assert front["metadata"]["gaia"]["tools_required"] == [
            "query_documents",
            "read_file",
            "remember",
        ]
        assert "# Triage a Support Ticket" in body

    def test_when_to_use_maps_to_description_not_when_to_use(self):
        skill = Skill.parse(_INTERMEDIATE_MD)
        front = yaml.safe_load(skill.to_skill_md().split("---\n", 2)[1])
        # The on-disk schema uses `description`, never the intermediate label.
        assert "when_to_use" not in front
        assert "description" in front


# ===========================================================================
# load_synthesis_config — thresholds + memory_settings.json override
# ===========================================================================


class TestLoadSynthesisConfig:
    def test_defaults_when_no_settings(self):
        cfg = load_synthesis_config(None)
        assert cfg == SynthesisConfig()
        assert cfg.enabled is True
        assert cfg.min_steps == 3
        assert cfg.min_occurrences == 3
        assert cfg.min_success_rate == 0.80
        assert cfg.similarity_tau == 0.82
        assert cfg.max_clusters_per_pass == 10

    def test_overrides_applied_from_section(self):
        cfg = load_synthesis_config(
            {
                "skill_synthesis": {
                    "enabled": False,
                    "min_steps": 5,
                    "similarity_tau": 0.9,
                    "max_clusters_per_pass": 2,
                }
            }
        )
        assert cfg.enabled is False
        assert cfg.min_steps == 5
        assert cfg.similarity_tau == 0.9
        assert cfg.max_clusters_per_pass == 2
        # Untouched keys keep their defaults.
        assert cfg.min_occurrences == 3

    def test_invalid_override_falls_back_to_default(self):
        cfg = load_synthesis_config({"skill_synthesis": {"min_steps": "not-an-int"}})
        assert cfg.min_steps == 3  # invalid value ignored, default kept

    def test_non_dict_section_ignored(self):
        cfg = load_synthesis_config({"skill_synthesis": "garbage"})
        assert cfg == SynthesisConfig()


# ===========================================================================
# cluster_by_goal — grouping at the cosine threshold
# ===========================================================================


class TestClusterByGoal:
    def test_groups_similar_goals_into_one_cluster(self):
        # Three identical goal vectors → one cluster of three.
        vecs = {
            "g": _unit([1.0, 0.0, 0.0]),
        }
        sessions = [_session(f"s{i}", "g", ["a", "b", "c"]) for i in range(3)]

        clusters = cluster_by_goal(sessions, lambda text: vecs[text], min_occurrences=3)

        assert len(clusters) == 1
        assert clusters[0].occurrences == 3
        assert clusters[0].from_sessions == ["s0", "s1", "s2"]

    def test_dissimilar_goal_splits_off(self):
        vecs = {
            "same": _unit([1.0, 0.0]),
            "other": _unit([0.0, 1.0]),  # orthogonal → cosine 0 < tau
        }
        sessions = [
            _session("a1", "same", ["x", "y", "z"]),
            _session("a2", "same", ["x", "y", "z"]),
            _session("b1", "other", ["p", "q", "r"]),
        ]

        clusters = cluster_by_goal(sessions, lambda text: vecs[text], min_occurrences=2)

        # Only the "same" pair clears min_occurrences=2; "other" is alone → dropped.
        assert len(clusters) == 1
        assert clusters[0].goal == "same"
        assert clusters[0].occurrences == 2

    def test_below_min_occurrences_dropped(self):
        vecs = {"g": _unit([1.0, 0.0])}
        sessions = [_session("s0", "g", ["a", "b", "c"])]
        assert cluster_by_goal(sessions, lambda t: vecs[t], min_occurrences=3) == []

    def test_below_min_success_rate_dropped(self):
        vecs = {"g": _unit([1.0, 0.0])}
        # 3 successes but 30 attempts → 10% success rate, far below 0.80.
        sessions = [
            _session(f"s{i}", "g", ["a", "b", "c"], success_count=3, attempt_count=30)
            for i in range(3)
        ]
        assert cluster_by_goal(sessions, lambda t: vecs[t], min_occurrences=3) == []

    def test_goalless_session_skipped(self):
        vecs = {"g": _unit([1.0, 0.0])}
        sessions = [
            _session("s0", "g", ["a", "b", "c"]),
            _session("s1", "g", ["a", "b", "c"]),
            _session("s2", None, ["a", "b", "c"]),  # no goal → skipped
        ]
        clusters = cluster_by_goal(sessions, lambda t: vecs[t], min_occurrences=2)
        assert clusters[0].occurrences == 2  # the goalless session did not join

    def test_embedder_failure_reraises(self):
        """Fail-loud: an embedder error propagates, never swallowed."""

        def boom(_text):
            raise RuntimeError("Embedding failed: Lemonade unreachable")

        sessions = [_session("s0", "g", ["a", "b", "c"])]
        with pytest.raises(RuntimeError, match="Embedding failed"):
            cluster_by_goal(sessions, boom)

    def test_clustering_is_order_independent(self):
        """Any input order → identical clusters, seed goal, and provenance.

        Seeds are taken in content-derived order (goal text, then session id),
        so the arbitrary row order ``iter_sessions`` returns can never change
        membership or which goal becomes the cluster representative.
        """
        # All three goals embed identically → one cluster; the seed is therefore
        # decided purely by the deterministic ordering, not by input position.
        shared = _unit([1.0, 0.0, 0.0])
        vecs = {
            "deploy app": shared,
            "deploy service": shared,
            "ship release": shared,
        }
        sessions = [
            _session("s3", "ship release", ["a", "b", "c"]),
            _session("s1", "deploy app", ["a", "b", "c"]),
            _session("s2", "deploy service", ["a", "b", "c"]),
        ]

        forward = cluster_by_goal(sessions, lambda t: vecs[t], min_occurrences=3)
        reverse = cluster_by_goal(
            list(reversed(sessions)), lambda t: vecs[t], min_occurrences=3
        )

        assert len(forward) == 1
        # Seed is the lexically-smallest goal, not the first row of the input.
        assert forward[0].goal == "deploy app"
        assert forward[0].from_sessions == ["s1", "s2", "s3"]
        assert forward[0].goal == reverse[0].goal
        assert forward[0].from_sessions == reverse[0].from_sessions

    def test_identical_goals_embed_once_per_distinct_goal(self):
        """Per-pass memoization: the embedder is called once per *distinct* goal,
        not once per session — so a recurring task does not re-embed N times."""
        vecs = {"g": _unit([1.0, 0.0, 0.0]), "other": _unit([0.0, 1.0, 0.0])}
        calls: list = []

        def counting_embed(text):
            calls.append(text)
            return vecs[text]

        # 5 sessions share goal "g", 1 has "other" → 2 distinct goals.
        sessions = [_session(f"s{i}", "g", ["a", "b", "c"]) for i in range(5)]
        sessions.append(_session("s5", "other", ["a", "b", "c"]))

        cluster_by_goal(sessions, counting_embed, min_occurrences=3)

        assert calls.count("g") == 1  # embedded once despite 5 sessions
        assert calls.count("other") == 1
        assert len(calls) == 2  # 2 distinct goals, not 6 sessions


# ===========================================================================
# distill_cluster — contract-shape + fail-loud split
# ===========================================================================


class TestDistillCluster:
    def test_calls_llm_with_distill_prompt_and_low_temp(self):
        """Contract-shape: the distill call uses the DISTILL system prompt + low temp."""
        send = _llm_returning(_INTERMEDIATE_MD)

        skill = distill_cluster(_cluster(), send)

        assert skill is not None
        assert skill.name == "triage-support-ticket"
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        assert kwargs["system_prompt"] == DISTILL_SYSTEM_PROMPT
        assert kwargs["temperature"] == DISTILL_TEMPERATURE
        assert kwargs["temperature"] <= 0.2  # "one low-temp LLM call"

    def test_strips_think_tags_and_code_fences(self):
        wrapped = f"<think>reasoning</think>\n```yaml\n{_INTERMEDIATE_MD}```"
        skill = distill_cluster(_cluster(), _llm_returning(wrapped))
        assert skill is not None
        assert skill.name == "triage-support-ticket"

    def test_skip_output_returns_none(self):
        assert distill_cluster(_cluster(), _llm_returning("SKIP")) is None

    def test_malformed_output_returns_none(self):
        # Structurally invalid (no frontmatter) → skip THIS cluster, no raise.
        assert distill_cluster(_cluster(), _llm_returning("garbage")) is None

    def test_llm_exception_propagates(self):
        """Fail-loud: a raised LLM error propagates so the driver can abort the pass."""
        send = MagicMock(side_effect=ConnectionError("Lemonade down"))
        with pytest.raises(ConnectionError, match="Lemonade down"):
            distill_cluster(_cluster(), send)


class TestBuildDistillUserPrompt:
    """The per-cluster user message handed to the distiller."""

    def test_includes_goal_occurrences_and_numbered_tools(self):
        cluster = _cluster(
            goal="triage a ticket", n=3, tools=("query_documents", "read_file")
        )
        prompt = _build_distill_user_prompt(cluster)
        assert "triage a ticket" in prompt
        assert "3 sessions" in prompt
        assert "1. query_documents" in prompt
        assert "2. read_file" in prompt

    def test_other_phrasings_block_dedups_and_caps_at_five(self):
        # One seed goal plus seven distinct other phrasings in the same cluster.
        members = [_session("s0", "primary goal", ["a", "b", "c"])]
        members += [
            _session(f"s{i}", f"alt phrasing {i}", ["a", "b", "c"]) for i in range(1, 8)
        ]
        cluster = GoalCluster(goal="primary goal", members=tuple(members))

        prompt = _build_distill_user_prompt(cluster)

        assert "Other phrasings of the same goal" in prompt
        # The seed goal is never repeated inside the "other phrasings" bullets.
        assert prompt.count("- primary goal") == 0
        # At most five alternate phrasings are listed.
        assert prompt.count("- alt phrasing ") == 5

    def test_no_other_phrasings_block_when_goals_identical(self):
        # Every member shares the seed goal → no "other phrasings" section.
        prompt = _build_distill_user_prompt(_cluster(goal="same goal", n=3))
        assert "Other phrasings" not in prompt


# ===========================================================================
# reconcile_and_store — ADD / UPDATE / NOOP (never DELETE)
# ===========================================================================


class TestReconcileAndStore:
    def _candidate(self, name="triage-support-ticket"):
        return Skill(
            name=name,
            when_to_use="Triage an inbound support ticket end to end.",
            body="# Triage\n1. step\n## Edge cases\n- escalate",
            tools_required=["query_documents", "read_file"],
        )

    def test_add_when_name_is_new(self, store):
        cluster = _cluster(n=4)  # success_count 12, attempt_count 12
        res = reconcile_and_store(
            self._candidate(), cluster, store, embedding=_unit([1, 0, 0]).tobytes()
        )
        assert res.action == "add"
        assert res.skill_id and res.skill_id.startswith("proc_")
        rows = store.search_skills(name="triage-support-ticket")
        assert len(rows) == 1
        assert rows[0]["provenance"]["source"] == "synthesized"
        assert rows[0]["provenance"]["from_sessions"] == list(cluster.from_sessions)

    def test_noop_when_existing_dominates(self, store):
        # Existing row with a strong track record AND a matching trigger vector
        # (the new match key is meaning, so the prior must carry an embedding).
        vec = _unit([1, 0, 0]).tobytes()
        store.put_skill(
            name="triage-support-ticket",
            when_to_use="t",
            markdown_body="b",
            success_count=99,
            attempt_count=100,
            embedding=vec,
        )
        weak_cluster = _cluster(n=3)  # success_count 9 << 99
        res = reconcile_and_store(self._candidate(), weak_cluster, store, embedding=vec)
        assert res.action == "noop"
        assert res.skill_id is None
        # Still exactly one (enabled, non-superseded) row.
        assert len(store.search_skills(name="triage-support-ticket")) == 1

    def test_update_supersedes_lower_success_count(self, store):
        vec = _unit([1, 0, 0]).tobytes()
        old_id = store.put_skill(
            name="triage-support-ticket",
            when_to_use="old trigger",
            markdown_body="old body",
            success_count=2,
            attempt_count=2,
            embedding=vec,
        )
        strong_cluster = _cluster(n=5)  # success_count 15 > 2
        res = reconcile_and_store(
            self._candidate(), strong_cluster, store, embedding=vec
        )

        assert res.action == "update"
        assert res.superseded_id == old_id
        assert res.skill_id and res.skill_id != old_id

        # Default search hides the superseded row but it is KEPT, not deleted.
        visible = store.search_skills(name="triage-support-ticket")
        assert len(visible) == 1
        assert visible[0]["id"] == res.skill_id
        old_row = store.search_skills(
            skill_id=old_id, include_superseded=True, enabled_only=False
        )
        assert len(old_row) == 1
        assert old_row[0]["superseded_by"] == res.skill_id

    def test_matches_by_meaning_supersedes_under_name_drift(self, store):
        """AC #1/#2: a drifted name with the same meaning supersedes — not a 2nd ADD.

        The prior recipe is stored under one name; a later pass distills the same
        goal under a *different* name with an identical trigger vector (cosine
        1.0 >= tau, modelling the real fixed-vector embedder) and a stronger track
        record.  The match is by meaning, so it UPDATEs, and the surviving row
        must carry the *second* candidate's name AND body — pinning supersede
        direction so a wrong-way supersede (old row surviving) is caught.
        """
        v = _unit([1, 0, 0]).tobytes()
        old_id = store.put_skill(
            name="summarize-unread-emails",
            when_to_use="Summarize the user's unread emails.",
            markdown_body="# Old\n1. step",
            success_count=2,
            attempt_count=2,
            embedding=v,
        )
        drifted = Skill(
            name="summarize-my-unread-emails",  # drifted name, same goal
            when_to_use="Summarize my unread emails.",
            body="# New\n1. better step",
            tools_required=["list_emails", "summarize"],
        )
        strong_cluster = _cluster(n=5)  # success_count 15 > 2
        res = reconcile_and_store(drifted, strong_cluster, store, embedding=v)

        assert res.action == "update"
        assert res.superseded_id == old_id
        visible = store.search_skills()  # enabled, non-superseded
        assert len(visible) == 1
        assert visible[0]["id"] == res.skill_id
        assert visible[0]["name"] == "summarize-my-unread-emails"
        assert visible[0]["markdown_body"] == "# New\n1. better step"

    def test_distinct_meaning_adds_even_with_same_name(self, store):
        """The key is meaning, not name: same name + orthogonal vector -> ADD."""
        store.put_skill(
            name="triage-support-ticket",
            when_to_use="Triage a support ticket.",
            markdown_body="b",
            success_count=2,
            attempt_count=2,
            embedding=_unit([1, 0, 0]).tobytes(),
        )
        res = reconcile_and_store(
            self._candidate(name="triage-support-ticket"),  # identical name
            _cluster(n=5),
            store,
            embedding=_unit([0, 1, 0]).tobytes(),  # cosine 0 < tau -> different goal
        )
        assert res.action == "add"
        assert len(store.search_skills()) == 2

    def test_lower_success_same_meaning_noops(self, store):
        """Dominance is unchanged under the new key: drifted name + matching
        vector but a weaker cluster -> NOOP (existing row dominates)."""
        v = _unit([1, 0, 0]).tobytes()
        store.put_skill(
            name="summarize-unread-emails",
            when_to_use="Summarize unread emails.",
            markdown_body="b",
            success_count=50,
            attempt_count=50,
            embedding=v,
        )
        drifted = Skill(
            name="summarize-my-unread-emails",
            when_to_use="Summarize my unread emails.",
            body="weaker",
            tools_required=["list_emails"],
        )
        res = reconcile_and_store(drifted, _cluster(n=3), store, embedding=v)  # 9 < 50
        assert res.action == "noop"
        assert len(store.search_skills()) == 1

    def test_reconcile_queries_store_by_embedding_not_name(self, store):
        """Contract-shape: the match scan queries enabled / non-superseded WITH
        embeddings and NEVER by name — a name-keyed or embedding-less query would
        silently reintroduce #1818."""
        spy = MagicMock(wraps=store.search_skills)
        with patch.object(store, "search_skills", spy):
            reconcile_and_store(
                self._candidate(),
                _cluster(n=3),
                store,
                embedding=_unit([1, 0, 0]).tobytes(),
            )
        assert spy.call_count >= 1
        first = spy.call_args_list[0].kwargs
        assert first.get("enabled_only") is True
        assert first.get("include_superseded") is False
        assert first.get("with_embedding") is True
        assert all(call.kwargs.get("name") is None for call in spy.call_args_list)

    def test_dim_mismatch_prior_is_skipped(self, store):
        """A stored row whose embedding dim differs from the candidate is skipped
        (not fed to np.dot, which would raise) -> reconcile ADDs rather than
        crashing.  Defensive: guards the match if the embedder dim ever changes."""
        store.put_skill(
            name="summarize-unread-emails",
            when_to_use="x",
            markdown_body="b",
            success_count=2,
            attempt_count=2,
            embedding=_unit([1, 0, 0, 0]).tobytes(),  # 4-dim — wrong shape
        )
        res = reconcile_and_store(
            self._candidate(name="summarize-my-unread-emails"),
            _cluster(n=5),
            store,
            embedding=_unit([1, 0, 0]).tobytes(),  # 3-dim candidate
        )
        assert res.action == "add"
        assert len(store.search_skills()) == 2

    def test_scan_cap_saturation_warns_no_silent_cap(self, caplog):
        """Fail-loud (#1818): a saturated scan window logs a WARNING — the cap is
        never a silent drop.  Driven through a fake store so the test stays fast."""
        blob = _unit([1, 0, 0]).tobytes()
        rows = [
            {"id": f"proc_{i}", "name": f"p{i}", "embedding": blob, "success_count": 1}
            for i in range(_RECONCILE_SCAN_LIMIT)
        ]

        class _FakeStore:
            def search_skills(self, **kwargs):
                return rows

        with caplog.at_level(
            logging.WARNING, logger="gaia.agents.base.skill_synthesis"
        ):
            match = _nearest_enabled_procedure(_FakeStore(), blob, SIMILARITY_TAU)

        assert match is not None  # a row still matched (cosine 1.0 >= tau)
        assert "cap" in caplog.text.lower()

    def test_result_dataclass_shape(self):
        r = ReconcileResult(action="add", skill_id="proc_x")
        assert (r.action, r.skill_id, r.superseded_id) == ("add", "proc_x", None)
