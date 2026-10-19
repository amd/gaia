# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Memory stays current as the conversation moves on.

Found by the memory benchmark (#4727), each on a real ``MemoryStore`` through
the real pipeline:

- Background lookups counted as "uses": every extraction bumped the
  confidence of up to 40 candidate memories, so old facts out-ranked new ones
  in the system prompt (a user who moved still "lived" in Calgary).
- Extraction dropped ``due_at``: a dated plan lost its date, and rewording a
  dated reminder replaced it with an undated copy.
- Consolidation re-filed facts from old chats: an untagged copy of a fact the
  user later corrected survived the correction ("Ada is 6" next to "Ada is 7").
"""

from __future__ import annotations

import json
import zlib
from datetime import datetime, timedelta
from typing import List
from unittest.mock import MagicMock

import numpy as np
import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore, content_stems


def _iso(days_ago: float = 0) -> str:
    return (datetime.now().astimezone() - timedelta(days=days_ago)).isoformat()


def _confidence(store: MemoryStore, kid: str) -> float:
    return store._conn.execute(
        "SELECT confidence FROM knowledge WHERE id = ?", (kid,)
    ).fetchone()[0]


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


class _HashEmbedder:
    """Deterministic offline embedder: word stems hashed into a vector."""

    MODEL = "test-hash"
    DIM = 384

    def embed(self, texts, model=None):
        out = []
        for text in texts:
            vec = np.zeros(self.DIM, dtype=np.float32)
            for stem in content_stems(text):
                h = zlib.crc32(stem.encode("utf-8"))
                vec[h % self.DIM] += 1.0 if (h >> 16) & 1 else -1.0
            if not vec.any():
                vec[0] = 1.0
            out.append(vec.tolist())
        return out


class _Host(MemoryMixin):
    """A chat agent's memory on its own database, with the model mocked."""

    def __init__(self, db_path, chat):
        self.chat = chat
        self._test_embedder = _HashEmbedder()
        self._faiss_index = None
        self._faiss_id_map = []
        self.init_memory(db_path=db_path, context="global", embedding_model="test-hash")
        assert self._memory_store is not None, self._memory_unavailable_detail
        self.register_memory_tools()

    def _get_embedder(self):
        return self._test_embedder

    def init_system_context(self, force: bool = False):
        return {"stored": 0}

    def start_skill_synthesis(self, *, force: bool = False):
        return None

    def close(self) -> None:
        assert self.wait_for_memory_extraction(timeout=30)
        self._memory_store.close()


def _host(tmp_path, replies: List[str]) -> _Host:
    replies_iter = iter(replies)
    chat = MagicMock()
    chat.send_messages.side_effect = lambda *a, **k: MagicMock(
        text=next(replies_iter), finish_reason="stop"
    )
    return _Host(tmp_path / "host.db", chat)


class TestOnlyARealRecallCountsAsAUse:
    def test_a_search_can_look_without_counting(self, store):
        kid = store.store(
            category="fact", content="User lives in Calgary", confidence=0.4
        )

        store.search("Calgary", count_as_use=False)
        assert _confidence(store, kid) == pytest.approx(0.4)

        store.search("Calgary")
        assert _confidence(store, kid) == pytest.approx(0.42)

    def test_only_the_results_returned_count_not_the_candidates(self, tmp_path):
        host = _host(tmp_path, [])
        store = host._memory_store
        facts = [
            "User walks the river trail with their dog every morning",
            "User's sister kayaks on the river in summer",
            "User proposed to their partner by the river at sunset",
            "River flooding closed the user's commute route last spring",
            "User photographs herons along the river",
            "User's book club meets in a cafe overlooking the river",
            "User once lost a phone in the river while fishing",
            "User trains for triathlons with open-water river swims",
            "User volunteers for the annual river cleanup",
            "User's grandfather built a cabin upstream on the river",
        ]
        ids = [store.store(category="fact", content=f, confidence=0.4) for f in facts]
        assert len(set(ids)) == len(facts)
        host._backfill_embeddings()
        host._rebuild_faiss_index()

        returned = host._hybrid_search("river", top_k=2)

        bumped = [kid for kid in ids if _confidence(store, kid) > 0.4]
        assert sorted(bumped) == sorted(r["id"] for r in returned)
        host.close()

    def test_extraction_lookups_do_not_count(self, tmp_path):
        host = _host(tmp_path, ["[]"] * 3)
        store = host._memory_store
        kid = store.store(
            category="fact",
            content="User lives in Calgary near the river",
            confidence=0.4,
        )
        host._backfill_embeddings()
        host._rebuild_faiss_index()

        for text in (
            "I went for a walk by the river in Calgary today",
            "Calgary river paths are icy this morning",
            "Thinking about the river again today in Calgary",
        ):
            host._after_process_query(text, "Nice.")
            assert host.wait_for_memory_extraction(timeout=10)

        assert _confidence(store, kid) == pytest.approx(0.4)
        host.close()


class TestExtractionKeepsDates:
    def _run(self, host, user, ops):
        host.chat.send_messages.side_effect = lambda *a, **k: MagicMock(
            text=json.dumps(ops), finish_reason="stop"
        )
        host._after_process_query(user, "Okay.")
        assert host.wait_for_memory_extraction(timeout=10)

    def _due(self, store, pattern):
        return store._conn.execute(
            "SELECT due_at FROM knowledge WHERE superseded_by IS NULL AND content LIKE ?",
            (f"%{pattern}%",),
        ).fetchone()[0]

    def test_an_extracted_plan_keeps_its_date(self, tmp_path):
        host = _host(tmp_path, [])
        self._run(
            host,
            "I'm pitching to Lumen Studio on April 16 at 1pm",
            [
                {
                    "op": "add",
                    "category": "reminder",
                    "grounded": "user",
                    "content": "User is pitching to Lumen Studio on April 16 at 1pm",
                    "due_at": "2026-04-16T13:00",
                }
            ],
        )
        assert self._due(host._memory_store, "Lumen").startswith("2026-04-16T13:00")
        host.close()

    def test_rewording_a_reminder_keeps_its_date_and_moving_it_changes_it(
        self, tmp_path
    ):
        host = _host(tmp_path, [])
        store = host._memory_store
        kid = store.store(
            category="reminder",
            content="User's dentist appointment is April 22",
            due_at="2026-04-22T15:00",
        )
        self._run(
            host,
            "The dentist is at the Oak Street clinic, by the way",
            [
                {
                    "op": "update",
                    "knowledge_id": kid,
                    "grounded": "user",
                    "content": "User's dentist appointment is April 22 at the Oak Street clinic",
                }
            ],
        )
        assert self._due(store, "Oak Street").startswith("2026-04-22T15:00")

        [new_id] = [
            r[0]
            for r in store._conn.execute(
                "SELECT id FROM knowledge WHERE superseded_by IS NULL AND content LIKE '%Oak%'"
            )
        ]
        self._run(
            host,
            "The dentist moved my appointment to April 27 at 2pm",
            [
                {
                    "op": "update",
                    "knowledge_id": new_id,
                    "grounded": "user",
                    "content": "User's dentist appointment is April 27 at 2pm at the Oak Street clinic",
                    "due_at": "2026-04-27T14:00",
                }
            ],
        )
        assert self._due(store, "April 27").startswith("2026-04-27T14:00")
        host.close()


class TestConsolidationDoesNotRefileOldFacts:
    def _old_chat(self, store, session, days_ago, user_line):
        for i in range(6):
            store.store_turn(
                session,
                "user" if i % 2 == 0 else "assistant",
                user_line if i % 2 == 0 else "Got it.",
            )
        with store._lock:
            store._conn.execute(
                "UPDATE conversations SET timestamp = ? WHERE session_id = ?",
                (_iso(days_ago), session),
            )
            store._conn.commit()

    def _consolidate(self, host, knowledge):
        host.chat.send_messages.side_effect = lambda *a, **k: MagicMock(
            text=json.dumps({"summary": "", "knowledge": knowledge}),
            finish_reason="stop",
        )
        return host.consolidate_old_sessions()

    def test_an_untagged_copy_of_a_current_fact_is_not_filed(self, tmp_path):
        host = _host(tmp_path, [])
        store = host._memory_store
        store.store(
            category="fact", content="User's daughter Ada is 6", entity="person:ada"
        )
        self._old_chat(store, "s-old", 30, "My daughter Ada is 6 and loves drawing")

        result = self._consolidate(
            host, [{"category": "fact", "content": "User's daughter Ada is 6"}]
        )

        assert result["extracted_items"] == 0
        assert (
            store._conn.execute(
                "SELECT COUNT(*) FROM knowledge WHERE content LIKE '%Ada is 6%'"
            ).fetchone()[0]
            == 1
        )
        host.close()

    def test_a_value_changed_after_the_chat_is_not_filed(self, tmp_path):
        host = _host(tmp_path, [])
        store = host._memory_store
        old = store.store(category="fact", content="User lives in Calgary")
        self._old_chat(store, "s-old", 30, "I live in Calgary and love the mountains")
        new = store.store(
            category="fact", content="User lives in Toronto near High Park"
        )
        store.update(old, superseded_by=new)  # the move, after that chat

        result = self._consolidate(
            host, [{"category": "fact", "content": "User lives in Calgary"}]
        )

        assert result["extracted_items"] == 0
        rows = [
            r[0]
            for r in store._conn.execute(
                "SELECT content FROM knowledge WHERE superseded_by IS NULL AND category = 'fact'"
            )
        ]
        assert rows == ["User lives in Toronto near High Park"]
        host.close()

    def test_a_new_fact_from_an_old_chat_is_still_filed(self, tmp_path):
        host = _host(tmp_path, [])
        store = host._memory_store
        self._old_chat(store, "s-old", 30, "I keep bees on the roof of my building")

        result = self._consolidate(
            host, [{"category": "fact", "content": "User keeps bees on their roof"}]
        )

        assert result["extracted_items"] == 1
        host.close()


class TestReviewedEdges:
    def test_an_explicit_recall_counts_a_use_without_confirming(self, tmp_path):
        host = _host(tmp_path, [])
        store = host._memory_store
        kid = store.store(
            category="fact", content="User keeps bees on the roof", confidence=0.4
        )
        before = store.get_item(kid)
        host._backfill_embeddings()
        host._rebuild_faiss_index()

        host._hybrid_search("bees roof", top_k=3)

        after = store.get_item(kid)
        assert after["use_count"] == before["use_count"] + 1
        assert after["last_used"] != before["last_used"]
        assert (
            after["updated_at"] == before["updated_at"]
        ), "a lookup is not a confirmation"
        host.close()

    @pytest.mark.parametrize(
        "current, new",
        [
            ("User lives in Calgary", "User's sister lives in Calgary"),
            ("User's manager is Dan", "User's manager is Priya"),
            ("User's daughter Emma is 7", "User's daughter Emma is 8"),
            ("User likes coffee", "User no longer drinks coffee"),
            ("User is an engineer at Google", "User is an engineer at Shopify"),
        ],
    )
    def test_a_new_fact_close_to_a_current_one_is_not_mistaken_for_it(
        self, store, current, new
    ):
        store.store(category="fact", content=current)
        assert not store.is_current(new, "fact")

    def test_turning_a_reminder_into_a_fact_drops_its_date(self, tmp_path):
        host = _host(tmp_path, [])
        store = host._memory_store
        kid = store.store(
            category="reminder",
            content="User's dentist checkup is April 22",
            due_at="2026-04-22T15:00",
        )
        host.chat.send_messages.side_effect = lambda *a, **k: MagicMock(
            text=json.dumps(
                [
                    {
                        "op": "update",
                        "knowledge_id": kid,
                        "grounded": "user",
                        "category": "fact",
                        "content": "User had a dentist checkup with no cavities",
                    }
                ]
            ),
            finish_reason="stop",
        )
        host._after_process_query("Went to the dentist, no cavities at all", "Great.")
        assert host.wait_for_memory_extraction(timeout=10)

        row = store._conn.execute(
            "SELECT category, due_at FROM knowledge WHERE superseded_by IS NULL"
        ).fetchone()
        assert row == ("fact", None)
        assert store.get_upcoming(within_days=365) == []
        host.close()

    def test_a_bad_date_keeps_the_memory_and_says_so(self, tmp_path, caplog):
        host = _host(tmp_path, [])
        host.chat.send_messages.side_effect = lambda *a, **k: MagicMock(
            text=json.dumps(
                [
                    {
                        "op": "add",
                        "category": "reminder",
                        "grounded": "user",
                        "content": "User's sister's wedding is next Saturday",
                        "due_at": "next Saturday",
                    }
                ]
            ),
            finish_reason="stop",
        )
        with caplog.at_level("WARNING", logger="gaia.agents.base.memory"):
            host._after_process_query(
                "My sister's wedding is next Saturday!", "Exciting!"
            )
            assert host.wait_for_memory_extraction(timeout=10)

        rows = host._memory_store._conn.execute(
            "SELECT content, due_at FROM knowledge"
        ).fetchall()
        assert rows == [("User's sister's wedding is next Saturday", None)]
        assert any("not an ISO date" in r.getMessage() for r in caplog.records)
        host.close()


class TestLastConfirmedMeansConfirmed:
    """``updated_at`` is shown to the agent as "last confirmed" and drives
    newest-first ordering; upkeep must not move it on every idle memory."""

    def _age(self, store, kid, field, days):
        with store._lock:
            store._conn.execute(
                f"UPDATE knowledge SET {field} = ? WHERE id = ?", (_iso(days), kid)
            )
            store._conn.commit()

    def test_decay_does_not_touch_last_confirmed_and_runs_once_a_month(self, store):
        kid = store.store(
            category="fact", content="User keeps bees on the roof", confidence=0.5
        )
        for field in ("last_used", "updated_at"):
            self._age(store, kid, field, 60)
        confirmed = store.get_item(kid)["updated_at"]

        assert store.apply_confidence_decay() == 1
        assert store.apply_confidence_decay() == 0, "decayed again the same day"

        item = store.get_item(kid)
        assert item["updated_at"] == confirmed
        assert item["confidence"] == pytest.approx(0.45)

    def test_a_reconciliation_nudge_is_not_a_confirmation(self, store):
        kid = store.store(
            category="fact", content="User keeps bees on the roof", confidence=0.5
        )
        self._age(store, kid, "updated_at", 60)
        confirmed = store.get_item(kid)["updated_at"]

        store.update_confidence(kid, 0.05)
        assert store.get_item(kid)["updated_at"] == confirmed

        store.update_confidence(kid, 0.1, confirmed=True)
        assert store.get_item(kid)["updated_at"] != confirmed

    def test_a_v6_database_gains_decayed_at(self, tmp_path):
        import sqlite3

        path = tmp_path / "v6.db"
        MemoryStore(db_path=path).close()
        conn = sqlite3.connect(path)
        conn.execute("ALTER TABLE knowledge DROP COLUMN decayed_at")
        conn.execute("UPDATE schema_version SET version = 6")
        conn.commit()
        conn.close()

        db = MemoryStore(db_path=path)
        try:
            cols = [r[1] for r in db._conn.execute("PRAGMA table_info(knowledge)")]
            assert "decayed_at" in cols
            assert (
                db._conn.execute("SELECT version FROM schema_version").fetchone()[0]
                == 7
            )
        finally:
            db.close()
