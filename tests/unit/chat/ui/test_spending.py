# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Cloud prices before a model is picked, this month's spending, and the limit (#5258).

Prices come from the one rate card the turn-cost estimate reads
(``gaia.eval.bench.metering``), so these tests check the routes against it
rather than against copied numbers.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gaia.eval.bench.metering import RATES, price
from gaia.ui import spending
from gaia.ui.database import ChatDatabase
from gaia.ui.routers import spending as spending_router

FLASH = "fireworks.glm-5p3-flash"


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    yield database
    database.close()


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(spending_router.router)
    app.state.db = db
    return TestClient(app, headers={"X-Gaia-UI": "1"})


def _turn(db, model=FLASH, inp=100_000, out=20_000):
    spending.record_turn(db, model, {"input_tokens": inp, "output_tokens": out})


# ── Prices ────────────────────────────────────────────────────────────────


def test_prices_come_from_the_shared_rate_card():
    card = spending.price_card(FLASH)
    uncached, cached, output = RATES["glm-5p3-flash"]
    assert card["input_per_million"] == uncached
    assert card["cached_input_per_million"] == cached
    assert card["output_per_million"] == output


@pytest.mark.parametrize(
    "model", ["fireworks.not-a-real-model", "amd.gpt-oss-120b", "Gemma-4-E4B-it-GGUF"]
)
def test_an_unknown_price_is_none_never_zero(model):
    assert spending.price_card(model) is None
    assert spending.turn_cost(model, 1000, 1000) is None


def test_prices_before_connecting(client):
    body = client.get("/api/spending/prices/fireworks").json()
    names = [m["name"] for m in body["models"]]
    assert names[:3] == ["deepseek-v4p1-flash", "glm-5p3-flash", "deepseek-v4-pro-0813"]
    # A retired dated snapshot priced only for old comparisons is not offered.
    assert "deepseek-v4-flash-0731" not in names
    assert all(m["price"]["output_per_million"] > 0 for m in body["models"])
    assert "list price" in body["source"]


def test_gateway_prices_are_unknown_and_say_why(client):
    body = client.get("/api/spending/prices/amd").json()
    assert body["models"] == []
    assert "organization" in body["note"]


def test_unknown_provider_is_404(client):
    assert client.get("/api/spending/prices/local").status_code == 404


# ── Monthly total ─────────────────────────────────────────────────────────


def test_a_cloud_turn_is_recorded_at_list_price(db):
    _turn(db)
    report = spending.month_report(db)
    assert report["turns"] == 1
    assert report["total_usd"] == pytest.approx(
        price(FLASH, 100_000, 0, 20_000), abs=1e-4
    )
    assert report["by_model"][0]["model"] == FLASH


def test_the_month_prices_cached_input_as_each_answer_does(db):
    """The composer's "today" and Settings' month differed ninefold on DeepSeek:
    one priced cached input at the cached rate, the other at full price."""
    model = "fireworks.deepseek-v4p1-flash"
    stats = {"input_tokens": 100_000, "cached_tokens": 90_000, "output_tokens": 2_000}
    spending.record_turn(db, model, stats)
    assert spending.month_report(db)["total_usd"] == pytest.approx(
        price(model, 100_000, 90_000, 2_000), abs=1e-4
    )


@pytest.mark.parametrize("cached", [-5_000, 150_000, True, "90000", None])
def test_a_cached_count_that_cannot_fit_the_input_is_priced_as_uncached(db, cached):
    """A bad count from a relay must not lower, or turn negative, the month."""
    model = "fireworks.deepseek-v4p1-flash"
    stats = {"input_tokens": 100_000, "cached_tokens": cached, "output_tokens": 2_000}
    spending.record_turn(db, model, stats)
    assert spending.month_report(db)["total_usd"] == pytest.approx(
        price(model, 100_000, 0, 2_000), abs=1e-4
    )


def test_all_input_cached_is_priced_at_the_cached_rate(db):
    model = "fireworks.deepseek-v4p1-flash"
    stats = {"input_tokens": 100_000, "cached_tokens": 100_000, "output_tokens": 0}
    spending.record_turn(db, model, stats)
    assert spending.month_report(db)["total_usd"] == pytest.approx(
        price(model, 100_000, 100_000, 0), abs=1e-6
    )


def test_the_turn_stats_keep_the_cached_tokens_the_month_is_priced_on():
    from gaia.ui._chat_helpers import _turn_inference_stats

    stats = _turn_inference_stats(
        None, {"tokens": 2_000, "input_tokens": 100_000, "cached_tokens": 90_000}
    )
    assert stats["cached_tokens"] == 90_000


def test_local_turns_cost_nothing_and_are_not_recorded(db):
    _turn(db, model="Gemma-4-E4B-it-GGUF")
    assert spending.month_report(db)["turns"] == 0


def test_unpriced_turns_are_counted_apart_not_as_zero(db):
    _turn(db, model="amd.gpt-oss-120b")
    report = spending.month_report(db)
    assert report["turns"] == 1
    assert report["unpriced_turns"] == 1
    assert report["unreported_turns"] == 0
    assert report["total_usd"] == 0


def test_a_priced_turn_with_no_usage_is_unreported_not_unpriced(db):
    # A stopped answer: the stream ended before Fireworks reported its tokens.
    spending.record_turn(db, FLASH, {})
    report = spending.month_report(db)
    assert report["turns"] == 1
    assert report["unpriced_turns"] == 0
    assert report["unreported_turns"] == 1
    assert report["by_model"][0]["unreported_turns"] == 1
    assert report["total_usd"] == 0


def test_the_total_is_per_month(db):
    _turn(db)
    assert spending.month_report(db, month="1999-01")["turns"] == 0


# ── This chat and today ───────────────────────────────────────────────────

# 08:00 UTC is local midnight for a viewer at UTC-8.
SINCE = "2026-11-05T08:00:00.000Z"


def _answer(db, session_id, cost, at, remote=True):
    summary = {"cost": cost, "model": {"remote": remote, "name": "DeepSeek"}}
    msg_id = db.add_message(session_id, "assistant", "a", turn_summary=summary)
    db._conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (at, msg_id))
    db._conn.commit()
    return msg_id


def _estimate(usd):
    return {"kind": "estimate", "usd": usd}


def _billed(db, usd, at):
    """A turn in the month's ledger at *at*, as SQLite's UTC time writes it."""
    db.add_cloud_spend("2026-11", FLASH, "fireworks", 1, 1, usd)
    db._conn.execute(
        "UPDATE cloud_spend SET created_at = ? WHERE id = "
        "(SELECT MAX(id) FROM cloud_spend)",
        (at,),
    )
    db._conn.commit()


def test_chat_totals_come_from_each_answers_cost_and_today_from_the_bill(db):
    chat = db.create_session(title="a")["id"]
    other = db.create_session(title="b")["id"]
    first = _answer(db, chat, _estimate(0.01), "2026-11-05T09:00:00+00:00")
    _answer(db, chat, _estimate(0.02), "2026-11-05T10:00:00+00:00")
    _answer(db, other, _estimate(0.5), "2026-11-05T11:00:00+00:00")
    # Yesterday evening locally: in this chat's total, not today's.
    _answer(db, chat, _estimate(1.0), "2026-11-05T07:59:00+00:00")
    for usd, at in ((0.01, "09:00"), (0.02, "10:00"), (0.5, "11:00"), (1.0, "07:59")):
        _billed(db, usd, f"2026-11-05 {at}:00")
    report = spending.chat_spend(db, chat, FLASH, SINCE)
    assert report["paid"] is True
    assert report["chat"]["usd"] == pytest.approx(1.03)
    assert report["chat"]["answers"] == 3
    assert report["today"]["usd"] == pytest.approx(0.53)
    assert report["today"]["answers"] == 3
    assert report["today"]["chats"] == 2
    items = report["chat"]["items"]
    assert [i["usd"] for i in items] == [1.0, 0.01, 0.02]
    assert items[1]["message_id"] == first


def test_local_answers_are_left_out_and_a_local_model_is_not_paid(db):
    chat = db.create_session(title="a")["id"]
    _answer(db, chat, {"kind": "local"}, "2026-11-05T09:00:00+00:00", remote=False)
    report = spending.chat_spend(db, chat, "Gemma-4-E4B-it-GGUF", SINCE)
    assert report["paid"] is False
    assert report["chat"] == {
        "usd": 0,
        "answers": 0,
        "unpriced_answers": 0,
        "items": [],
    }
    assert report["today"]["answers"] == 0


def test_a_stopped_cloud_answer_is_counted_apart_not_as_zero(db):
    chat = db.create_session(title="a")["id"]
    _answer(db, chat, {"kind": "unreported"}, "2026-11-05T09:00:00+00:00")
    _answer(db, chat, _estimate(0.02), "2026-11-05T09:01:00+00:00")
    report = spending.chat_spend(db, chat, FLASH, SINCE)
    assert report["chat"]["usd"] == pytest.approx(0.02)
    assert report["chat"]["answers"] == 2
    assert report["chat"]["unpriced_answers"] == 1
    assert report["chat"]["items"][0]["usd"] is None


def test_a_gateway_model_has_no_price_so_is_not_paid(db):
    chat = db.create_session(title="a")["id"]
    assert spending.chat_spend(db, chat, "amd.gpt-oss-120b", SINCE)["paid"] is False


def test_old_sqlite_timestamps_are_read_as_utc(db):
    chat = db.create_session(title="a")["id"]
    _answer(db, chat, _estimate(0.04), "2026-11-05 08:30:00")
    _answer(db, chat, _estimate(0.08), "2026-11-05 07:30:00")
    assert spending.chat_spend(db, chat, FLASH, SINCE)["chat"]["answers"] == 2
    _billed(db, 0.04, "2026-11-05 08:30:00")
    _billed(db, 0.08, "2026-11-05 07:30:00")
    assert spending.chat_spend(db, chat, FLASH, SINCE)["today"]["usd"] == pytest.approx(
        0.04
    )


def test_today_counts_answers_edited_away_or_retried_as_the_month_does(db):
    """Settings' month said $0.0055 while the composer's today said $0.0045."""
    chat = db.create_session(title="a")["id"]
    kept = _answer(db, chat, _estimate(0.03), "2026-11-05T09:00:00+00:00")
    replaced = _answer(db, chat, _estimate(0.02), "2026-11-05T09:01:00+00:00")
    _billed(db, 0.03, "2026-11-05 09:00:00")
    _billed(db, 0.02, "2026-11-05 09:01:00")
    db._conn.execute("DELETE FROM messages WHERE id = ?", (replaced,))
    db._conn.commit()
    report = spending.chat_spend(db, chat, FLASH, SINCE)
    month = spending.month_report(db, "2026-11")
    assert report["today"]["usd"] == pytest.approx(month["total_usd"])
    assert report["today"]["answers"] == 2
    assert [i["message_id"] for i in report["chat"]["items"]] == [kept]


def test_answers_are_listed_by_time_across_timestamp_formats(db):
    chat = db.create_session(title="a")["id"]
    # As text, the space in the old format sorts before the newer "T" row.
    _answer(db, chat, _estimate(0.02), "2026-11-05T09:00:00+00:00")
    _answer(db, chat, _estimate(0.01), "2026-11-05 10:00:00")
    items = spending.chat_spend(db, chat, FLASH, SINCE)["chat"]["items"]
    assert [i["usd"] for i in items] == [0.02, 0.01]


def test_chat_spending_route(client, db):
    chat = db.create_session(title="a")["id"]
    _answer(db, chat, _estimate(0.03), "2026-11-05T09:00:00+00:00")
    body = client.get(
        f"/api/spending/chat/{chat}", params={"since": SINCE, "model": FLASH}
    ).json()
    assert body["chat"]["usd"] == pytest.approx(0.03)
    assert body["today"]["since"] == "2026-11-05T08:00:00+00:00"
    assert (
        client.get("/api/spending/chat/nope", params={"since": SINCE}).status_code
        == 404
    )
    bad = client.get(f"/api/spending/chat/{chat}", params={"since": "yesterday"})
    assert bad.status_code == 422
    assert "ISO timestamp" in bad.json()["detail"]


def test_an_unreadable_answer_cost_fails_loudly(client, db):
    chat = db.create_session(title="a")["id"]
    msg = _answer(db, chat, _estimate(0.03), "2026-11-05T09:00:00+00:00")
    db._conn.execute("UPDATE messages SET turn_summary = '{' WHERE id = ?", (msg,))
    db._conn.commit()
    resp = client.get(f"/api/spending/chat/{chat}", params={"since": SINCE})
    assert resp.status_code == 500
    assert f"Message {msg}" in resp.json()["detail"]


# ── Limit ─────────────────────────────────────────────────────────────────


def test_limit_states(client, db):
    assert client.get("/api/spending").json()["state"] == "no_limit"
    cost = price(FLASH, 100_000, 0, 20_000)
    _turn(db)
    body = client.put("/api/spending/limit", json={"limit_usd": cost / 0.9}).json()
    assert body["state"] == "near"
    assert (
        client.put("/api/spending/limit", json={"limit_usd": 100}).json()["state"]
        == "ok"
    )
    assert (
        client.put("/api/spending/limit", json={"limit_usd": None}).json()["limit_usd"]
        is None
    )


def test_a_limit_must_be_positive(client):
    resp = client.put("/api/spending/limit", json={"limit_usd": 0})
    assert resp.status_code == 422
    assert "more than $0" in resp.json()["detail"]


def test_a_cloud_turn_is_refused_once_the_limit_is_reached(db):
    spending.set_limit(db, 0.01)
    _turn(db, inp=1_000_000, out=100_000)
    refusal = spending.limit_refusal(db, FLASH)
    assert "reaches your $0.01 monthly limit" in refusal
    assert "stopped before sending this to Fireworks AI" in refusal
    assert "Settings › Model" in refusal
    # A model on this PC is never stopped by the cloud limit.
    assert spending.limit_refusal(db, "Gemma-4-E4B-it-GGUF") is None


def test_allowing_the_month_lets_cloud_turns_go_on(client, db):
    spending.set_limit(db, 0.01)
    _turn(db, inp=1_000_000, out=100_000)
    assert client.get("/api/spending").json()["state"] == "reached"
    body = client.post("/api/spending/allow-month").json()
    assert body["state"] == "allowed_over"
    assert spending.limit_refusal(db, FLASH) is None


def test_a_corrupt_saved_limit_fails_loudly(client, db):
    db.set_setting(spending.LIMIT_SETTING, "lots")
    resp = client.get("/api/spending")
    assert resp.status_code == 500
    assert "not a number" in resp.json()["detail"]


# ── The chat path stops before a cloud turn ───────────────────────────────


def test_streaming_chat_refuses_a_cloud_turn_over_the_limit(db, monkeypatch):
    """The stream ends with the refusal before any agent is built."""
    import asyncio

    from gaia.ui import _chat_helpers
    from gaia.ui.models import ChatRequest

    spending.set_limit(db, 0.01)
    _turn(db, inp=1_000_000, out=100_000)
    db.set_setting("custom_model", FLASH)
    session = db.create_session(title="t", agent_type="gaia")
    # The check must not depend on which agents this machine has registered.
    monkeypatch.setattr(_chat_helpers, "_agent_registry", None)

    def no_agent(*_a, **_k):
        raise AssertionError("an agent was built for a refused turn")

    monkeypatch.setattr(_chat_helpers, "_get_cached_agent", no_agent)

    async def collect():
        out = []
        request = ChatRequest(session_id=session["id"], message="hi", stream=True)
        run = SimpleNamespace(handler=None)
        async for chunk in _chat_helpers._stream_chat_impl(run, db, session, request):
            out.append(chunk)
        return out

    chunks = asyncio.run(collect())
    # Nothing new recorded: the turn never reached the provider.
    assert len(db.cloud_spend_for_month(spending.current_month())) == 1
    events = [
        json.loads(block[len("data: ") :])
        for chunk in chunks
        for block in chunk.split("\n\n")
        if block.startswith("data: ")
    ]
    # An answer the user can read and keep, not an error with a bug-report link.
    assert not [e for e in events if e.get("type") == "error"]
    answers = [e for e in events if e.get("type") == "answer"]
    assert answers and "monthly limit" in answers[-1]["content"]
    done = [e for e in events if e.get("type") == "done"]
    assert done and done[-1]["content"] == answers[-1]["content"]
    saved = db.get_messages(session["id"])
    assert saved[-1]["role"] == "assistant"
    assert "monthly limit" in saved[-1]["content"]


def test_non_streaming_chat_refuses_a_cloud_turn_over_the_limit(db, monkeypatch):
    import asyncio

    from gaia.ui import _chat_helpers
    from gaia.ui.models import ChatRequest

    spending.set_limit(db, 0.01)
    _turn(db, inp=1_000_000, out=100_000)
    db.set_setting("custom_model", FLASH)
    session = db.create_session(title="t", agent_type="gaia")
    monkeypatch.setattr(_chat_helpers, "_agent_registry", None)
    monkeypatch.setattr(
        _chat_helpers,
        "_get_cached_agent",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("agent built")),
    )
    request = ChatRequest(session_id=session["id"], message="hi", stream=False)
    answer = asyncio.run(_chat_helpers._get_chat_response(db, session, request))
    assert "monthly limit" in answer


def test_cloud_model_lists_carry_their_price(monkeypatch):
    from gaia.ui.routers import providers

    models = [
        {"id": FLASH, "recipe": "cloud", "labels": ["chat"]},
        {"id": "fireworks.brand-new", "recipe": "cloud", "labels": ["chat"]},
    ]
    monkeypatch.setattr(
        providers,
        "_lemonade",
        lambda method, path, payload=None: (200, {"data": models}),
    )
    by_id = {m["id"]: m for m in providers._list_models("fireworks")}
    assert by_id[FLASH]["price"] == spending.price_card(FLASH)
    assert by_id["fireworks.brand-new"]["price"] is None


class _CloudAgent:
    """A cached agent stand-in whose turn reports token totals, as process_query does."""

    def __init__(self, result):
        self._result = result
        self.indexed_files = set()
        self.conversation_history = []
        self.rag = None
        self.console = None
        self._cancel_event = __import__("threading").Event()

    def _register_tools(self):
        pass

    def process_query(self, _message, **_kwargs):
        return self._result


@pytest.mark.parametrize(
    "result,priced",
    [
        ({"result": "4", "input_tokens": 1_000_000, "output_tokens": 100_000}, True),
        # No counts: still recorded, as a turn whose usage went unreported, never as $0.
        ("4", False),
    ],
)
def test_non_streaming_cloud_turns_are_recorded(db, monkeypatch, result, priced):
    import asyncio

    from gaia.ui import _chat_helpers
    from gaia.ui.models import ChatRequest

    monkeypatch.setattr(_chat_helpers, "_agent_registry", None)
    monkeypatch.setattr(
        _chat_helpers, "_maybe_load_expected_model", lambda *a, **k: None
    )
    _chat_helpers._agent_cache.clear()
    db.set_setting("custom_model", FLASH)
    session = db.create_session(title="t", model=FLASH, agent_type="chat")
    _chat_helpers._store_agent(session["id"], FLASH, [], _CloudAgent(result), "chat")
    try:
        request = ChatRequest(session_id=session["id"], message="2+2?", stream=False)
        answer = asyncio.run(_chat_helpers._get_chat_response(db, session, request))
    finally:
        _chat_helpers._agent_cache.clear()
    assert "4" in answer
    report = spending.month_report(db)
    assert report["turns"] == 1
    if priced:
        assert report["total_usd"] == pytest.approx(
            price(FLASH, 1_000_000, 0, 100_000), abs=1e-4
        )
    else:
        assert report["unreported_turns"] == 1
