# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Earlier turns a relayed chat sends with ``/query``, sized to the model's window.

The relay used to send the last 5 exchanges with every message cut to 2,000
characters, and cut user messages with no marker. A 17,067-character log pasted
on turn 1 reached turn 2 as its first 2,000 characters, so the model told the
user their log "stops at batch 34". These tests drive the real relay into a
fake sidecar and assert the ``/query`` body it receives.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gaia.agents.base import history
from gaia.llm.lemonade_client import GPU_CTX_SIZE, LemonadeClientError
from gaia.llm.providers.claude import CLAUDE_CTX_SIZE
from gaia.ui import _chat_helpers as helpers
from gaia.ui.email_sidecar import daemon_client
from gaia.ui.email_sidecar.profiles import EMAIL_PROFILE, GAIA_PROFILE
from gaia.ui.models import ChatRequest
from gaia.ui.sse_handler import SSEOutputHandler

LOCAL_MODEL = "Qwen3.6-35B-A3B-GGUF"
ANSWER = "The door code on line 211 is 7351-ORCA."


def _server_log() -> str:
    lines = []
    for n in range(1, 301):
        if n == 211:
            lines.append(f"{n:03d} INFO door code set to 7351-ORCA")
        else:
            lines.append(
                f"{n:03d} INFO worker-{n % 7} processed batch {n} in {n * 3} ms ok"
            )
    text = "\n".join(lines)
    return text + "x" * (17_067 - len(text))


LOG = _server_log()


class _Sidecar:
    """A sidecar that records each ``/query`` body it receives."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.bodies: list = []

    def init(self):
        return 200, {}

    def set_bypass(self, _session_id, _on):
        pass

    def query_stream(self, body, read_timeout, on_response):
        self.bodies.append(body)
        yield {"type": "final", "answer": "ok"}

    def cancel_query(self, _rid):
        pass


@pytest.fixture(autouse=True)
def _fresh_cut_state(monkeypatch):
    monkeypatch.setattr(helpers, "_history_cuts", {})
    monkeypatch.setattr(helpers, "_relay_clips", {})


def _window(monkeypatch, tokens: int):
    monkeypatch.setattr(
        "gaia.llm.lemonade_client.resolve_ctx_size", lambda **_kw: tokens
    )


def _relay(
    monkeypatch,
    pairs,
    *,
    profile=GAIA_PROFILE,
    api_version="2.12",
    model=LOCAL_MODEL,
    session_id=None,
    message="In that same log, which worker processed batch 250?",
):
    sidecar = _Sidecar(profile.agent_id)
    handle = SimpleNamespace(api_version=api_version, proxy=lambda: sidecar)
    monkeypatch.setattr(daemon_client, "acquire_handle", lambda _id: handle)
    handler = SSEOutputHandler()
    helpers._dispatch_sidecar_query(
        handler,
        ChatRequest(session_id="s1", message=message, agent_type=profile.agent_id),
        pairs,
        model,
        profile.agent_id,
        session_id=session_id,
    )
    events = []
    while not handler.event_queue.empty():
        events.append(handler.event_queue.get_nowait())
    cuts = [
        e for e in events if isinstance(e, dict) and e.get("status") == "history_cut"
    ]
    return sidecar.bodies, cuts, events


def test_a_long_paste_reaches_the_next_relayed_turn_whole(monkeypatch):
    _window(monkeypatch, 65536)

    bodies, cuts, _ = _relay(monkeypatch, [(LOG, ANSWER)])

    assert len(bodies) == 1
    assert bodies[0]["context"] == [
        {"role": "user", "content": LOG},
        {"role": "assistant", "content": ANSWER},
    ]
    assert "processed batch 250 " in bodies[0]["context"][0]["content"]
    assert cuts == []


def test_more_than_five_exchanges_are_sent_when_they_fit(monkeypatch):
    _window(monkeypatch, 65536)
    pairs = [(f"q{i}", f"a{i}") for i in range(8)]

    bodies, cuts, _ = _relay(monkeypatch, pairs)

    assert [m["content"] for m in bodies[0]["context"]] == [
        text for pair in pairs for text in pair
    ]
    assert cuts == []


def test_a_paste_too_big_for_the_window_is_cut_with_a_marker_and_announced(
    monkeypatch,
):
    _window(monkeypatch, 12288)

    bodies, cuts, _ = _relay(monkeypatch, [(LOG, ANSWER)])

    user, assistant = bodies[0]["context"]
    assert assistant == {"role": "assistant", "content": ANSWER}
    assert user["role"] == "user"
    kept, marker = user["content"].split("\n\n[GAIA cut the last ")
    assert LOG.startswith(kept) and 0 < len(kept) < len(LOG)
    assert marker.startswith(f"{len(LOG) - len(kept):,} of this message's 17,067")
    budget = history.relayed_history_budget(
        LOCAL_MODEL, "In that same log, which worker processed batch 250?", cloud=False
    )
    assert history.turn_cost(bodies[0]["context"]) <= budget
    assert len(cuts) == 1
    assert "17,067 characters" in cuts[0]["message"]
    assert cuts[0]["tokens"] > 0


def test_a_long_answer_is_cut_with_a_marker_too(monkeypatch):
    _window(monkeypatch, 12288)
    long_answer = "Here is the summary. " + "y" * 30_000

    bodies, cuts, _ = _relay(monkeypatch, [("summarise it", long_answer)])

    user, assistant = bodies[0]["context"]
    assert user == {"role": "user", "content": "summarise it"}
    assert assistant["content"].startswith("Here is the summary. ")
    assert "characters to fit the model's context window" in assistant["content"]
    assert len(cuts) == 1


def test_older_turns_leave_the_view_newest_first_and_the_chat_is_told(monkeypatch):
    _window(monkeypatch, 12288)
    pairs = [(f"question {i} " + "z" * 3000, f"answer {i}") for i in range(10)]

    bodies, cuts, _ = _relay(monkeypatch, pairs)

    sent = [m["content"] for m in bodies[0]["context"] if m["role"] == "assistant"]
    assert sent and sent[-1] == "answer 9"
    assert sent == [f"answer {i}" for i in range(10 - len(sent), 10)]
    assert len(sent) < 10
    assert len(cuts) == 1
    assert "out of the model's view" in cuts[0]["message"]
    assert cuts[0]["turns"] == 10 - len(sent)


def test_a_cut_message_already_out_of_view_is_not_announced_as_cut(monkeypatch):
    _window(monkeypatch, 12288)
    pairs = [(LOG, ANSWER)] + [
        (f"question {i} " + "z" * 3000, f"answer {i}") for i in range(10)
    ]

    bodies, cuts, _ = _relay(monkeypatch, pairs)

    assert ANSWER not in [m["content"] for m in bodies[0]["context"]]
    assert len(cuts) == 1
    assert "out of the model's view" in cuts[0]["message"]
    assert "too long" not in cuts[0]["message"]


def test_a_stable_cut_is_announced_once_per_chat(monkeypatch):
    _window(monkeypatch, 12288)

    _, first, _ = _relay(monkeypatch, [(LOG, ANSWER)], session_id="chat-1")
    _, second, _ = _relay(
        monkeypatch, [(LOG, ANSWER), ("thanks", "welcome")], session_id="chat-1"
    )

    assert len(first) == 1
    assert second == []


def test_the_email_sidecar_gets_the_same_history(monkeypatch):
    _window(monkeypatch, 65536)
    mail = "Forwarded thread:\n" + "From: a@example.com\n" * 900

    bodies, cuts, _ = _relay(
        monkeypatch,
        [(mail, "Two senders.")],
        profile=EMAIL_PROFILE,
        api_version="2.4",
        message="who else wrote?",
    )

    assert bodies[0]["query"] == "who else wrote?"
    assert bodies[0]["context"] == [
        {"role": "user", "content": mail},
        {"role": "assistant", "content": "Two senders."},
    ]
    assert cuts == []


def test_a_window_that_cannot_be_read_stops_the_turn_loudly(monkeypatch):
    def _broken(**_kw):
        raise LemonadeClientError("GAIA_CTX_SIZE must be a positive integer")

    monkeypatch.setattr("gaia.llm.lemonade_client.resolve_ctx_size", _broken)

    bodies, _, events = _relay(monkeypatch, [(LOG, ANSWER)])

    assert bodies == []
    errors = [e for e in events if isinstance(e, dict) and e["type"] == "agent_error"]
    assert len(errors) == 1
    assert "GAIA_CTX_SIZE" in errors[0]["content"]


def test_relayed_budget_follows_the_in_process_window_policy(monkeypatch):
    monkeypatch.setattr(history, "count_tokens", lambda _text: 10)
    _window(monkeypatch, 65536)

    assert history.relayed_history_budget(LOCAL_MODEL, "q", cloud=False) == 32768
    assert (
        history.relayed_history_budget("claude-opus-4", "q", cloud=False)
        == CLAUDE_CTX_SIZE // 2
    )
    # Cloud: GAIA's 64K admission budget, with the reply outside it.
    assert history.relayed_history_budget("fireworks.qwen3", "q", cloud=True) == min(
        GPU_CTX_SIZE // 2, GPU_CTX_SIZE - 10 - 2048
    )
    _window(monkeypatch, 16384)
    assert (
        history.relayed_history_budget(LOCAL_MODEL, "q", cloud=False)
        == 16384 - 10 - history.DEFAULT_REPLY_TOKENS - 2048
    )
