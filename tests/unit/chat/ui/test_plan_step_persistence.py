# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A saved turn keeps its plan once, with the step it reached, and each call's plain line.

Drives the real ``_stream_chat_impl`` step capture and ``ChatDatabase`` round
trip; only the email sidecar plumbing is stubbed so a script can emit events.
"""

import asyncio
from types import SimpleNamespace

import gaia.ui._chat_helpers as chat_helpers_module
import gaia.ui.email_sidecar.daemon_client as daemon_client_module
import gaia.ui.email_sidecar.relay as relay_module
from gaia.ui._chat_helpers import _stream_chat_impl
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest
from gaia.ui.utils import message_to_response


class _FakeProxy:
    def init(self):
        return 200, {}


class _FakeHandle:
    api_version = "2.4"

    def proxy(self):
        return _FakeProxy()


async def _noop_retitle(**kwargs):
    return None


PLAN = [
    {"tool": "read_file", "tool_args": {"file_path": "a.txt"}},
    {"tool": "read_file", "tool_args": {"file_path": "b.txt"}},
]


def _run_turn(monkeypatch, script):
    def _fake_relay_query(handler, proxy, *, query, context, model_id=None, **kw):
        script(handler)
        handler._emit({"type": "answer", "content": "done"})

    monkeypatch.setattr(
        daemon_client_module, "acquire_handle", lambda agent_id="email": _FakeHandle()
    )
    monkeypatch.setattr(relay_module, "relay_query", _fake_relay_query)
    monkeypatch.setattr(
        chat_helpers_module, "_maybe_update_session_title", _noop_retitle
    )

    db = ChatDatabase(":memory:")
    session = db.create_session(agent_type="email")
    request = ChatRequest(
        session_id=session["id"], message="read both", stream=True, agent_type="email"
    )

    async def _drive():
        async for _ in _stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            pass

    asyncio.run(_drive())
    saved = [m for m in db.get_messages(session["id"]) if m["role"] == "assistant"]
    assert len(saved) == 1
    return message_to_response(saved[0]).agent_steps


def _call(handler, path):
    handler.print_tool_usage("read_file")
    handler.pretty_print_json({"file_path": path}, title="Arguments")
    handler.pretty_print_json({"status": "success", "content": "x"}, title="Result")
    handler.print_tool_complete()


def test_resent_plan_is_saved_once_at_the_step_it_reached(monkeypatch):
    def script(handler):
        handler.print_plan(PLAN, 0)
        _call(handler, "a.txt")
        handler.print_plan(PLAN, 1)
        _call(handler, "b.txt")

    steps = _run_turn(monkeypatch, script)

    plans = [s for s in steps if s.type == "plan"]
    assert len(plans) == 1
    assert plans[0].currentStep == 1
    assert plans[0].planNarrations == ["Reading file: a.txt", "Reading file: b.txt"]


def test_a_replan_is_saved_as_its_own_plan(monkeypatch):
    other = [{"tool": "write_file", "tool_args": {"file_path": "out.md"}}]

    def script(handler):
        handler.print_plan(PLAN, 0)
        _call(handler, "a.txt")
        handler.print_plan(other, 0)
        handler.print_plan(PLAN, 1)
        _call(handler, "b.txt")

    steps = _run_turn(monkeypatch, script)

    plans = [(s.planSteps[0], s.currentStep) for s in steps if s.type == "plan"]
    assert [step for step, _ in plans] == [
        "read_file — file_path='a.txt'",
        "write_file — file_path='out.md'",
        "read_file — file_path='a.txt'",
    ]
    assert [current for _, current in plans] == [0, 0, 1]


def test_each_saved_call_keeps_its_plain_line(monkeypatch):
    steps = _run_turn(monkeypatch, lambda handler: _call(handler, "notes.txt"))

    tools = [s for s in steps if s.type == "tool"]
    assert [t.narration for t in tools] == ["Reading file: notes.txt"]
