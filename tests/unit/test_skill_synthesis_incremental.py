# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Skill synthesis distils only what is new, and does it off the first turn.

Covers the incremental half of the procedural-memory driver (#887): the stored
watermark that bounds the DETECT window, the per-episode marks that stop an
undistillable cluster from being retried every session start, the per-pass cap
on distillation calls, and the background thread the maintenance pass now uses
so no LLM call sits in front of the user's first answer.

No live backend: the embedder is a deterministic local function and the chat
SDK is a recording stub, so every assertion is about what the driver *would*
send.
"""

import logging
import threading
import time
import zlib
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore
from gaia.agents.base.procedural_memory import ProceduralMemoryMixin

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _embed(text) -> np.ndarray:
    """A deterministic 768-dim unit vector per text.

    Identical goals land at cosine 1.0 (they cluster), different goals at ~0
    (they do not) — enough to drive CLUSTER without a live embedder.
    """
    rng = np.random.default_rng(zlib.crc32(str(text).encode("utf-8")))
    vec = rng.standard_normal(768).astype(np.float32)
    return vec / np.linalg.norm(vec)


def _distilled_md(name: str = "triage-support-ticket") -> str:
    """A valid intermediate distiller document."""
    return (
        f"---\n"
        f"name: {name}\n"
        f"when_to_use: Handle the recurring {name} request end to end.\n"
        f"tools_required: [tool_0, tool_1]\n"
        f"---\n\n"
        f"# {name}\n\n"
        f"1. Do the first step with `tool_0`.\n"
        f"2. Do the second step with `tool_1`.\n\n"
        f"## Edge cases\n"
        f"- Stop if the input is missing.\n"
    )


class _RecordingChat:
    """A chat SDK stub that records every distillation prompt it is sent."""

    def __init__(self, reply=None):
        self.prompts = []
        self._reply = reply if reply is not None else _distilled_md()

    def send_messages(self, messages, **_kwargs):
        self.prompts.append(messages[0]["content"])
        reply = self._reply
        if callable(reply):
            reply = reply(len(self.prompts))
        return MagicMock(text=reply)

    @property
    def calls(self) -> int:
        return len(self.prompts)


class _SynthesisHost(ProceduralMemoryMixin):
    """The mixin's host surface: a store, an embedder, a chat SDK.

    ``_proc_faiss_index`` stays None (the empty-index off-state), so these
    tests exercise the driver without requiring faiss.
    """

    def __init__(self, store, chat):
        self._memory_store = store
        self._proc_faiss_index = None
        self._proc_faiss_id_map = []
        self._memory_session_id = "live-session"
        self.chat = chat
        self._embed_text = _embed


def _seed_episode(store, session_id: str, goal: str, steps: int = 3) -> None:
    """One qualifying episode: a user goal turn plus successful tool calls."""
    store.store_turn(session_id, "user", goal)
    for i in range(steps):
        store.log_tool_call(session_id, f"tool_{i}", {"x": i}, "ok", True)


def _seed_cluster(store, prefix: str, goal: str, count: int = 3) -> None:
    """``count`` episodes of the same goal — enough to clear min_occurrences."""
    for n in range(count):
        _seed_episode(store, f"{prefix}{n}", goal)


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


@pytest.fixture(autouse=True)
def _default_settings():
    """Read thresholds from the defaults, never the developer's ~/.gaia."""
    with patch("gaia.agents.base.memory._load_memory_settings", return_value={}):
        yield


@pytest.fixture
def host(store):
    return _SynthesisHost(store, _RecordingChat())


# ---------------------------------------------------------------------------
# The watermark — what synthesis has already consumed
# ---------------------------------------------------------------------------


class TestWatermark:
    def test_a_new_user_with_no_history_records_nothing(self, host, store):
        """The cold state: an empty store costs no call and no watermark."""
        assert host._synthesize_skills()["consumed"] == 0
        assert host.chat.calls == 0
        assert store.get_synthesis_watermark() is None
        assert store.get_synthesis_marks() == {}

    def test_second_pass_over_unchanged_history_calls_no_model(self, host, store):
        """The measured bug: the same history re-distilled on every start."""
        _seed_cluster(store, "ticket_", "Triage an inbound support ticket")
        assert store.get_synthesis_watermark() is None

        first = host._synthesize_skills()

        assert first["stored"] == 1
        assert first["consumed"] == 3
        assert host.chat.calls == 1
        watermark = store.get_synthesis_watermark()
        assert watermark is not None

        second = host._synthesize_skills()

        assert host.chat.calls == 1, "a second pass re-distilled unchanged history"
        assert second == {
            "clusters": 0,
            "stored": 0,
            "skipped": 0,
            "consumed": 0,
            "capped": False,
        }
        assert store.get_synthesis_watermark() == watermark
        assert len(store.search_skills()) == 1

    def test_only_new_episodes_are_distilled(self, host, store):
        """After new activity the prompt carries the new goal, not the old one."""
        _seed_cluster(store, "old_", "Summarize the weekly sales report")
        host._synthesize_skills()
        assert host.chat.calls == 1
        assert "weekly sales report" in host.chat.prompts[0]

        _seed_cluster(store, "new_", "Rename the screenshots in my downloads")
        host.chat = _RecordingChat(_distilled_md("rename-screenshots"))

        result = host._synthesize_skills()

        assert result["stored"] == 1
        assert host.chat.calls == 1
        prompt = host.chat.prompts[0]
        assert "Rename the screenshots" in prompt
        assert "weekly sales report" not in prompt

    def test_watermark_never_moves_backwards(self, host, store):
        """An explicit older `since` cannot re-open consumed history."""
        _seed_cluster(store, "t_", "Triage an inbound support ticket")
        host._synthesize_skills()
        watermark = store.get_synthesis_watermark()

        host._synthesize_skills(since="1970-01-01T00:00:00+00:00")

        assert store.get_synthesis_watermark() == watermark

    def test_live_session_is_never_consumed(self, host, store):
        """The current session keeps running; consuming it would hide its tail."""
        _seed_episode(store, host._memory_session_id, "Do the live thing")
        _seed_episode(store, host._memory_session_id + "-b", "Do the live thing")

        host._synthesize_skills()

        assert host._memory_session_id not in store.get_synthesis_marks()


# ---------------------------------------------------------------------------
# Marks — an episode the distiller could not use is not retried
# ---------------------------------------------------------------------------


class TestUnusableEpisodes:
    @staticmethod
    def _seed_blocked_history(store):
        """A cluster that cannot be distilled, held below an unconsumed one.

        The two ``pending`` episodes never clear ``min_occurrences``, so the
        watermark cannot advance past them — which is exactly the case where
        only the marks stop the undistillable cluster from being retried.
        """
        _seed_episode(store, "pending_0", "A one-off goal nobody repeats")
        _seed_episode(store, "pending_1", "A one-off goal nobody repeats")
        _seed_cluster(store, "garbage_", "Reconcile the monthly ledger")

    def test_unusable_cluster_is_marked_not_retried_and_logged(
        self, host, store, caplog
    ):
        self._seed_blocked_history(store)
        host.chat = _RecordingChat("no frontmatter here, just prose")

        with caplog.at_level(logging.INFO, logger="gaia.agents.base.procedural_memory"):
            first = host._synthesize_skills()

        assert first["skipped"] == 1
        assert first["stored"] == 0
        assert "undistillable" in caplog.text
        marks = store.get_synthesis_marks()
        assert {m["outcome"] for m in marks.values()} == {"unusable"}
        assert len(marks) == 3
        # The unconsumed one-off episodes still hold the watermark back.
        assert store.get_synthesis_watermark() is None

        host.chat = _RecordingChat()
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="gaia.agents.base.procedural_memory"):
            second = host._synthesize_skills()

        assert host.chat.calls == 0, "an undistillable cluster was retried"
        assert second["clusters"] == 0
        assert "still skipped" in caplog.text

    def test_force_retries_a_previously_unusable_cluster(self, host, store):
        self._seed_blocked_history(store)
        host.chat = _RecordingChat("no frontmatter here, just prose")
        host._synthesize_skills()

        host.chat = _RecordingChat(_distilled_md("reconcile-monthly-ledger"))
        forced = host._synthesize_skills(force=True)

        assert host.chat.calls == 1
        assert forced["stored"] == 1
        assert store.search_skills()[0]["name"] == "reconcile-monthly-ledger"

    def test_reset_synthesis_progress_reopens_everything(self, host, store):
        _seed_cluster(store, "t_", "Triage an inbound support ticket")
        host._synthesize_skills()
        assert store.get_synthesis_watermark() is not None

        cleared = store.reset_synthesis_progress()

        assert cleared == {"marks_cleared": 3, "watermark_cleared": True}
        assert store.get_synthesis_marks() == {}
        assert store.get_synthesis_watermark() is None

        host.chat = _RecordingChat()
        host._synthesize_skills()
        assert host.chat.calls == 1

    def test_infra_failure_leaves_the_cluster_unmarked(self, host, store):
        """Lemonade being down is not "undistillable" — the next pass retries."""
        _seed_cluster(store, "t_", "Triage an inbound support ticket")
        chat = MagicMock()
        chat.send_messages.side_effect = ConnectionError("Lemonade down")
        host.chat = chat

        host._synthesize_skills()

        assert store.get_synthesis_marks() == {}
        assert store.get_synthesis_watermark() is None

        host.chat = _RecordingChat()
        assert host._synthesize_skills()["stored"] == 1


# ---------------------------------------------------------------------------
# The per-pass call cap
# ---------------------------------------------------------------------------


class TestCallCap:
    def test_cap_stops_the_pass_and_the_rest_is_picked_up_next_time(self, host, store):
        """Cluster order is by goal text, so a/b/c is also the seeding order."""
        _seed_cluster(store, "a_", "a goal that recurs often")
        _seed_cluster(store, "b_", "b goal that recurs often")
        _seed_cluster(store, "c_", "c goal that recurs often")

        first = host._synthesize_skills()

        assert first["capped"] is True
        assert host.chat.calls == 2
        assert first["consumed"] == 6
        # The watermark stops below the untouched c_ episodes, not above them.
        watermark = store.get_synthesis_watermark()
        b_last = max(
            s["last_at"] for s in store.iter_sessions() if s["session_id"][0] == "b"
        )
        c_first = min(
            s["started_at"] for s in store.iter_sessions() if s["session_id"][0] == "c"
        )
        assert watermark == b_last
        assert watermark < c_first

        host.chat = _RecordingChat(_distilled_md("c-goal"))
        second = host._synthesize_skills()

        assert host.chat.calls == 1
        assert second["stored"] == 1
        assert "c goal that recurs often" in host.chat.prompts[0]
        assert {row["name"] for row in store.search_skills()} == {
            "triage-support-ticket",
            "c-goal",
        }

    def test_cap_is_configurable(self, host, store):
        _seed_cluster(store, "a_", "a goal that recurs often")
        _seed_cluster(store, "b_", "b goal that recurs often")
        _seed_cluster(store, "c_", "c goal that recurs often")

        with patch(
            "gaia.agents.base.memory._load_memory_settings",
            return_value={"skill_synthesis": {"max_distill_calls_per_pass": 1}},
        ):
            result = host._synthesize_skills()

        assert host.chat.calls == 1
        assert result["capped"] is True


# ---------------------------------------------------------------------------
# A store written by an older build
# ---------------------------------------------------------------------------


class TestOldStore:
    def test_v4_store_migrates_and_synthesizes(self, tmp_path):
        """A DB from before the marks table loads, migrates, and distils once."""
        db_path = tmp_path / "memory.db"
        old = MemoryStore(db_path=db_path)
        _seed_cluster(old, "t_", "Triage an inbound support ticket")
        old._conn.execute("DROP TABLE synthesis_marks")
        old._conn.execute("UPDATE schema_version SET version = 4")
        old._conn.commit()
        old.close()

        store = MemoryStore(db_path=db_path)
        try:
            version = store._conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()[0]
            assert version == 5
            assert store.get_synthesis_watermark() is None
            assert store.get_synthesis_marks() == {}
            assert len(store.iter_sessions()) == 3  # history survived

            host = _SynthesisHost(store, _RecordingChat())
            assert host._synthesize_skills()["stored"] == 1
            assert host.chat.calls == 1
            assert store.get_synthesis_watermark() is not None
            assert host._synthesize_skills()["clusters"] == 0
            assert host.chat.calls == 1
        finally:
            store.close()


# ---------------------------------------------------------------------------
# Off the first turn
# ---------------------------------------------------------------------------


def _mock_embedder():
    mock = MagicMock()
    mock.embed.return_value = [_embed("fixed").tolist()]
    return mock


class _FakeAgent:
    """Minimal Agent stand-in — just what MemoryMixin hooks into."""

    def __init__(self):
        self._system_prompt_cache = None
        self._registered_tools = {}

    def process_query(self, user_input, **_kwargs):
        return {"result": f"Response to: {user_input}"}

    def _execute_tool(self, tool_name, tool_args):
        return {"status": "ok", "tool": tool_name}


class TestBackgroundExecution:
    @pytest.fixture
    def agent(self, tmp_path):
        class _Agent(MemoryMixin, _FakeAgent):
            pass

        host = _Agent()
        with (
            patch.object(MemoryMixin, "_get_embedder", return_value=_mock_embedder()),
            patch.object(MemoryMixin, "_embed_text", return_value=_embed("fixed")),
            patch.object(MemoryMixin, "_backfill_embeddings", return_value=0),
            patch.object(MemoryMixin, "_rebuild_faiss_index", return_value=None),
        ):
            host.init_memory(db_path=tmp_path / "memory.db", context="global")
        host._embed_text = _embed
        return host

    def test_first_query_does_not_wait_for_distillation(self, agent):
        """Session start returns while the pass is still in the model call."""
        _seed_cluster(agent._memory_store, "t_", "Triage an inbound support ticket")
        released = threading.Event()
        entered = threading.Event()

        def _blocking_send(messages, **_kwargs):
            entered.set()
            released.wait(timeout=30)
            return MagicMock(text=_distilled_md())

        chat = MagicMock()
        chat.send_messages.side_effect = _blocking_send
        agent.chat = chat

        try:
            started = time.monotonic()
            agent.process_query("what is on my plate today?")
            elapsed = time.monotonic() - started

            assert elapsed < 2.0, f"first query blocked for {elapsed:.1f}s"
            assert entered.wait(timeout=10), "synthesis never reached the model"
            assert agent.wait_for_skill_synthesis(timeout=0.1) is False
        finally:
            released.set()

        assert agent.wait_for_skill_synthesis(timeout=30) is True
        assert agent._skill_synthesis_error is None
        assert agent._skill_synthesis_result["stored"] == 1
        assert len(agent._memory_store.search_skills()) == 1

    def test_background_failure_is_logged_and_kept(self, agent, caplog):
        _seed_cluster(agent._memory_store, "t_", "Triage an inbound support ticket")
        agent.chat = MagicMock()

        with patch.object(
            agent,
            "_synthesize_skills",
            side_effect=RuntimeError("Embedding failed: Lemonade unreachable"),
        ):
            with caplog.at_level(
                logging.ERROR, logger="gaia.agents.base.procedural_memory"
            ):
                agent.start_skill_synthesis()
                assert agent.wait_for_skill_synthesis(timeout=30) is True

        assert "background skill synthesis failed" in caplog.text
        assert isinstance(agent._skill_synthesis_error, RuntimeError)

    def test_a_second_start_does_not_open_a_second_pass(self, agent):
        _seed_cluster(agent._memory_store, "t_", "Triage an inbound support ticket")
        released = threading.Event()
        entered = threading.Event()

        def _blocking_send(messages, **_kwargs):
            entered.set()
            released.wait(timeout=30)
            return MagicMock(text=_distilled_md())

        chat = MagicMock()
        chat.send_messages.side_effect = _blocking_send
        agent.chat = chat

        try:
            first = agent.start_skill_synthesis()
            assert entered.wait(timeout=10)
            assert agent.start_skill_synthesis() is first
        finally:
            released.set()

        assert agent.wait_for_skill_synthesis(timeout=30) is True
        assert chat.send_messages.call_count == 1

    def test_no_store_starts_nothing(self, agent):
        agent._memory_store = None
        assert agent.start_skill_synthesis() is None
        assert agent.wait_for_skill_synthesis() is True
