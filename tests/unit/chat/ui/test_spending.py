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


def test_local_turns_cost_nothing_and_are_not_recorded(db):
    _turn(db, model="Gemma-4-E4B-it-GGUF")
    assert spending.month_report(db)["turns"] == 0


def test_unpriced_turns_are_counted_apart_not_as_zero(db):
    _turn(db, model="amd.gpt-oss-120b")
    spending.record_turn(db, FLASH, {})  # no token counts reported
    report = spending.month_report(db)
    assert report["turns"] == 2
    assert report["unpriced_turns"] == 2
    assert report["total_usd"] == 0


def test_the_total_is_per_month(db):
    _turn(db)
    assert spending.month_report(db, month="1999-01")["turns"] == 0


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
