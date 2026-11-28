# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A relayed turn on a cloud model with no working key ends in GAIA's words.

Fast and light (gaiacpp) passed Lemonade's raw 404 through to the chat, where
Full-featured explained the missing key. The relay now checks the key before
the turn and stops with the same sentence the app uses for a cloud model that
isn't connected.
"""

import pytest

pytest.importorskip("fastapi")

CLOUD_MODEL = "fireworks.deepseek-v4p1-flash"


class _SSE:
    def __init__(self):
        self.events = []

    def _emit(self, event):
        self.events.append(event)


def test_a_cloud_model_without_a_working_key_stops_the_turn(monkeypatch):
    from gaia.ui._chat_helpers import _replay_cloud_key_for_relay

    monkeypatch.setattr(
        "gaia.llm.cloud_keys.reconnect_provider", lambda provider: False
    )
    sse = _SSE()

    assert _replay_cloud_key_for_relay(sse, CLOUD_MODEL) is False

    errors = [e for e in sse.events if e["type"] == "agent_error"]
    assert len(errors) == 1
    assert "isn't connected, so GAIA can't answer" in errors[0]["content"]
    assert "Reconnect Fireworks AI" in errors[0]["content"]
    assert "404" not in errors[0]["content"]
    assert errors[0]["failure"] == "model_unavailable"


def test_a_cloud_model_with_a_working_key_goes_ahead(monkeypatch):
    from gaia.ui._chat_helpers import _replay_cloud_key_for_relay

    monkeypatch.setattr("gaia.llm.cloud_keys.reconnect_provider", lambda provider: True)
    sse = _SSE()

    assert _replay_cloud_key_for_relay(sse, CLOUD_MODEL) is True
    assert sse.events == []


def test_a_local_model_is_not_checked(monkeypatch):
    from gaia.ui._chat_helpers import _replay_cloud_key_for_relay

    def unexpected(provider):
        raise AssertionError("a local model has no cloud key to check")

    monkeypatch.setattr("gaia.llm.cloud_keys.reconnect_provider", unexpected)
    assert _replay_cloud_key_for_relay(_SSE(), "Qwen3.6-35B-A3B-GGUF") is True


def test_both_modes_say_the_same_thing(monkeypatch):
    from gaia.ui._chat_helpers import (
        _agent_ui_wording,
        _missing_local_model_text,
        _replay_cloud_key_for_relay,
    )

    monkeypatch.setattr(
        "gaia.llm.cloud_keys.reconnect_provider", lambda provider: False
    )
    monkeypatch.setattr("gaia.llm.local_only.cloud_blocked", lambda: False)
    sse = _SSE()
    _replay_cloud_key_for_relay(sse, CLOUD_MODEL)

    in_process = _missing_local_model_text({"model": {"id": CLOUD_MODEL}})
    assert sse.events[0]["content"] == _agent_ui_wording(in_process)


def test_a_model_that_cant_be_used_names_no_agent_to_switch_to(monkeypatch):
    from gaia.daemon.sidecars import fetch
    from gaia.ui._chat_helpers import (
        FLAGSHIP_AGENT_ID,
        MODEL_UNAVAILABLE,
        _failed_turn_program,
    )

    monkeypatch.setattr(fetch, "saved_flagship", lambda: fetch.NATIVE_FLAGSHIP)

    assert _failed_turn_program(FLAGSHIP_AGENT_ID, MODEL_UNAVAILABLE) is None
    assert _failed_turn_program(FLAGSHIP_AGENT_ID) == fetch.NATIVE_FLAGSHIP


def test_a_failed_turns_cause_survives_a_reload(tmp_path):
    from gaia.ui.database import ChatDatabase
    from gaia.ui.utils import message_to_response

    db = ChatDatabase(str(tmp_path / "chat.db"))
    sid = db.create_session(title="t")["id"]
    failed_id = db.add_message(
        sid,
        "assistant",
        "",
        failed=True,
        agent_program="gaiacpp",
        failure="model_unavailable",
    )
    answered_id = db.add_message(
        sid, "assistant", "hi", agent_program="gaiacpp", failure="model_unavailable"
    )

    rows = {m["id"]: m for m in db.get_messages(sid)}
    assert message_to_response(rows[failed_id]).failure == "model_unavailable"
    # Only a failed turn has a cause.
    assert message_to_response(rows[answered_id]).failure is None
