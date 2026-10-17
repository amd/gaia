# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

""" "Retry with…" asks another local model from the same evidence; keeping it continues the chat.

Also covers ``GET /api/chat/context/{id}``, the meter's source between turns.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from gaia.agents.base import history
from gaia.ui import _chat_helpers as helpers
from gaia.ui import answer_retry
from gaia.ui.database import ChatDatabase
from gaia.ui.routers import chat

pytestmark = pytest.mark.asyncio

CALL = {
    "id": "c1",
    "type": "function",
    "function": {"name": "query_documents", "arguments": "{}"},
}
TURN = [
    {"role": "user", "content": "What does the report say?"},
    {"role": "assistant", "content": "", "tool_calls": [CALL]},
    {
        "role": "tool",
        "tool_call_id": "c1",
        "name": "query_documents",
        "content": "Revenue rose 4%.",
    },
    {"role": "assistant", "content": "Revenue rose 4%."},
]
RETRIED = {
    "content": "The report says revenue grew 4%.",
    "stats": {
        "model": "Other-GGUF",
        "seconds": 12.5,
        "load_seconds": 9.0,
        "input_tokens": 300,
        "output_tokens": 9,
        "tokens_per_second": 40.0,
        "time_to_first_token": 0.2,
    },
}


@pytest.fixture
def db():
    database = ChatDatabase(":memory:")
    yield database
    database.close()


@pytest.fixture(autouse=True)
def _clean():
    answer_retry._pending.clear()
    helpers._agent_cache.clear()
    yield
    answer_retry._pending.clear()
    helpers._agent_cache.clear()


def _http():
    state = SimpleNamespace(session_locks={}, chat_semaphore=asyncio.Semaphore(1))
    return SimpleNamespace(app=SimpleNamespace(state=state))


def _chat(db, *, recorded=True):
    session = db.create_session(model="M-GGUF", agent_type="gaia")
    db.add_message(session["id"], "user", "hello")
    db.add_message(
        session["id"],
        "assistant",
        "hi",
        model_messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
    )
    db.add_message(session["id"], "user", TURN[0]["content"])
    answer_id = db.add_message(
        session["id"],
        "assistant",
        TURN[-1]["content"],
        model_messages=TURN if recorded else None,
        inference_stats={
            "tokens_per_second": 20.0,
            "time_to_first_token": 0.4,
            "input_tokens": 800,
            "output_tokens": 5,
        },
    )
    return session["id"], answer_id


async def _retry(db, sid, mid, model="Other-GGUF"):
    with (
        patch.object(answer_retry, "answer_with", return_value=RETRIED) as asked,
        patch("gaia.llm.lemonade_client.resolve_ctx_size", return_value=32768),
    ):
        result = await chat.retry_with(
            chat.RetryWithRequest(session_id=sid, message_id=mid, model=model),
            _http(),
            db,
        )
    return result, asked


async def test_asks_the_other_model_from_the_same_evidence_without_its_answer(db):
    sid, mid = _chat(db)

    result, asked = await _retry(db, sid, mid)

    assert result["content"] == RETRIED["content"]
    model, messages = asked.call_args.args
    assert model == "Other-GGUF"
    assert messages[0]["role"] == "system"
    text = [m["content"] for m in messages]
    assert "hello" in text and "hi" in text  # earlier turns come along
    assert any("Revenue rose 4%." in t and "Recorded tool result" in t for t in text)
    # The original final answer is not shown to the model asked to replace it.
    assert messages[-1]["content"].startswith("[Recorded tool result")
    # Nothing in the chat changes until the user keeps an answer.
    assert db.get_message_record(sid, mid)["content"] == "Revenue rose 4%."


async def test_keeping_the_retry_continues_the_chat_from_it(db):
    sid, mid = _chat(db)
    await _retry(db, sid, mid)

    kept = await chat.choose_retry(
        chat.RetryChoiceRequest(session_id=sid, message_id=mid, keep="retry"), db
    )

    assert kept["kept"] == "retry"
    record = db.get_message_record(sid, mid)
    assert record["content"] == RETRIED["content"]
    assert record["inference_stats"]["model"] == "Other-GGUF"
    # The next turn's history replays the kept answer with the original evidence.
    turns = history.transcript_turns(db.get_context_messages(sid))
    assert turns[-1][:-1] == TURN[:-1]
    assert turns[-1][-1] == {"role": "assistant", "content": RETRIED["content"]}
    listed = db.get_messages(sid)[-1]
    assert listed["id"] == mid and listed["content"] == RETRIED["content"]


async def test_keeping_the_original_changes_nothing(db):
    sid, mid = _chat(db)
    await _retry(db, sid, mid)

    kept = await chat.choose_retry(
        chat.RetryChoiceRequest(session_id=sid, message_id=mid, keep="original"), db
    )

    assert kept == {"kept": "original"}
    assert db.get_message_record(sid, mid)["content"] == "Revenue rose 4%."
    with pytest.raises(HTTPException) as exc:
        await chat.choose_retry(
            chat.RetryChoiceRequest(session_id=sid, message_id=mid, keep="retry"), db
        )
    assert exc.value.status_code == 404


async def test_a_text_only_turn_is_retried_from_its_question(db):
    sid, mid = _chat(db, recorded=False)

    _, asked = await _retry(db, sid, mid)
    messages = asked.call_args.args[1]
    assert messages[-1] == {"role": "user", "content": TURN[0]["content"]}

    await chat.choose_retry(
        chat.RetryChoiceRequest(session_id=sid, message_id=mid, keep="retry"), db
    )
    assert db.get_message_record(sid, mid)["model_messages"] is None


async def test_only_the_latest_answer_can_be_retried(db):
    sid, mid = _chat(db)
    db.add_message(sid, "user", "another question")
    with pytest.raises(HTTPException) as exc:
        await _retry(db, sid, mid)
    assert exc.value.status_code == 409


async def test_a_cloud_model_is_refused(db):
    sid, mid = _chat(db)
    with patch(
        "gaia.llm.lemonade_client.cloud_model_provider", return_value="fireworks"
    ):
        with pytest.raises(HTTPException) as exc:
            await _retry(db, sid, mid, model="fireworks.some-model")
    assert exc.value.status_code == 400


async def test_a_turn_too_long_for_the_other_model_says_so(db):
    sid, mid = _chat(db)
    with (
        patch("gaia.llm.lemonade_client.resolve_ctx_size", return_value=4096),
        patch.object(answer_retry, "answer_with") as asked,
    ):
        with pytest.raises(HTTPException) as exc:
            await chat.retry_with(
                chat.RetryWithRequest(
                    session_id=sid, message_id=mid, model="Small-GGUF"
                ),
                _http(),
                db,
            )
    assert exc.value.status_code == 413
    assert "window" in exc.value.detail
    asked.assert_not_called()


async def test_retry_waits_for_a_running_turn(db):
    sid, mid = _chat(db)
    http = _http()
    lock = http.app.state.session_locks.setdefault(sid, asyncio.Lock())
    await lock.acquire()
    try:
        with pytest.raises(HTTPException) as exc:
            await chat.retry_with(
                chat.RetryWithRequest(
                    session_id=sid, message_id=mid, model="Other-GGUF"
                ),
                http,
                db,
            )
    finally:
        lock.release()
    assert exc.value.status_code == 409


async def test_context_is_unavailable_until_an_agent_has_answered(db):
    sid, _ = _chat(db)
    result = await chat.get_context_usage(sid, db)
    assert result["available"] is False
    assert result["reason"]


async def test_context_measures_the_chat_with_its_agent(db, monkeypatch):
    sid, _ = _chat(db)
    monkeypatch.setattr(history, "context_window", lambda _agent: 32768)
    agent = SimpleNamespace(
        model_id="M-GGUF", system_prompt="You are GAIA.", _openai_tools=[]
    )
    helpers._store_agent(sid, "M-GGUF", [], agent, "gaia")
    from gaia.ui import turn_insights

    monkeypatch.setattr(turn_insights, "_loaded_chat_model", lambda *_: "Other-GGUF")

    result = await chat.get_context_usage(sid, db)

    assert result["available"] is True
    assert result["window"] == 32768
    assert result["turns_in_view"] == 2
    assert result["documents"] > 0
    assert result["tokenizer"]["exact"] is False
    assert sum(c["tokens"] for c in result["categories"]) == result["used"]


async def test_context_for_an_unknown_chat_is_a_404(db):
    with pytest.raises(HTTPException) as exc:
        await chat.get_context_usage("missing", db)
    assert exc.value.status_code == 404


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code != 200:
            import httpx

            raise httpx.HTTPStatusError("bad", request=None, response=None)


def _ask(post_body, stats_status=200, loaded=("other-gguf",)):
    sent = {}

    def post(url, json, headers, timeout):
        sent.update(url=url, body=json)
        return _Resp(200, post_body)

    def get(url, headers, timeout):
        if url.endswith("/health"):
            models = [{"model_name": m, "type": "llm"} for m in loaded]
            return _Resp(200, {"all_models_loaded": models})
        stats = {"tokens_per_second": 41.26, "time_to_first_token": 0.31}
        return _Resp(stats_status, stats)

    with (
        patch("httpx.post", side_effect=post),
        patch("httpx.get", side_effect=get),
        patch.object(helpers, "_maybe_load_expected_model") as load,
        patch(
            "gaia.llm.lemonade_manager.LemonadeManager.get_base_url",
            return_value="http://lem/api/v1",
        ),
    ):
        result = answer_retry.answer_with(
            "Other-GGUF", [{"role": "user", "content": "q"}]
        )
    return result, sent, load


async def test_answer_with_loads_the_model_then_asks_it():
    result, sent, load = _ask(
        {
            "choices": [{"message": {"content": " An answer. "}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        }
    )
    load.assert_called_once_with("Other-GGUF")
    assert sent["url"] == "http://lem/api/v1/chat/completions"
    assert sent["body"]["model"] == "Other-GGUF"
    assert sent["body"]["messages"] == [{"role": "user", "content": "q"}]
    assert sent["body"]["stream"] is False
    assert result["content"] == "An answer."
    assert result["stats"]["tokens_per_second"] == 41.3
    assert result["stats"]["output_tokens"] == 3


async def test_an_empty_answer_is_an_error_not_a_blank_bubble():
    with pytest.raises(answer_retry.RetryError) as exc:
        _ask({"choices": [{"message": {"content": ""}}]})
    assert exc.value.status == 503
    assert "empty answer" in str(exc.value)


async def test_a_model_that_failed_to_load_is_an_error_not_a_smaller_window():
    with pytest.raises(answer_retry.RetryError) as exc:
        _ask({"choices": [{"message": {"content": "x"}}]}, loaded=("someone-else",))
    assert exc.value.status == 503
    assert "could not be loaded" in str(exc.value)


async def test_retry_waits_while_another_chat_is_answering(db):
    sid, mid = _chat(db)
    http = _http()
    await http.app.state.chat_semaphore.acquire()
    try:
        with patch.object(answer_retry, "answer_with") as asked:
            with pytest.raises(HTTPException) as exc:
                await chat.retry_with(
                    chat.RetryWithRequest(
                        session_id=sid, message_id=mid, model="Other-GGUF"
                    ),
                    http,
                    db,
                )
    finally:
        http.app.state.chat_semaphore.release()
    assert exc.value.status_code == 409
    assert "another chat" in exc.value.detail
    asked.assert_not_called()
