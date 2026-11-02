# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""An approved ``edit_file`` shows its diff in the Agent UI and the TUI (#3710).

The real ``edit_file`` tool runs against a real file with the real Agent-UI
handler attached; the handler's events go through the real canonical
translator, and the test asserts the ``tool_result`` both clients draw from.
"""

import asyncio
import importlib
import json
import queue
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.security import PathValidator
from gaia.ui import _chat_helpers as helpers
from gaia.ui import sse_handler as sse_handler_module
from gaia.ui.database import ChatDatabase
from gaia.ui.email_sidecar.relay import _dispatch_one
from gaia.ui.models import ChatRequest
from gaia.ui.run_manager import RunManager
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator


@pytest.fixture
def file_tools(tmp_path):
    module = importlib.import_module("gaia.agents.tools.file_io_tools")
    mixin = module.FileIOToolsMixin()
    mixin.path_validator = PathValidator()
    mixin.path_validator.allowed_paths.add(tmp_path.resolve())

    saved = dict(_TOOL_REGISTRY)
    try:
        mixin.register_file_io_tools()

        def get(name):
            return _TOOL_REGISTRY[name]["function"]

        get.mixin = mixin
        yield get
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


def _wire(handler):
    translator = CanonicalTranslator(
        run_id=None,
        agent_id="probe",
        render_tool_map=SSEOutputHandler._RENDER_TOOL_TO_LANG,
    )
    out = []
    while True:
        try:
            event = handler.event_queue.get_nowait()
        except queue.Empty:
            return out
        if event is not None:
            out.extend(translator.translate(event))


def _run_call(handler, tool_name, args, body):
    """The console calls the agent loop makes around one tool call."""
    handler.print_tool_usage(tool_name)
    handler.pretty_print_json(args, "Arguments")
    result = body()
    handler.pretty_print_json(result, "Result")
    handler.print_tool_complete()
    return result


def _results(wire):
    return [e for e in wire if e["type"] == "tool_result"]


def _edit(file_tools, tmp_path, before, old, new, name="note.txt"):
    path = tmp_path / name
    path.write_text(before, encoding="utf-8")
    file_tools("read_file")(str(path))  # edit_file refuses an unread file
    handler = SSEOutputHandler()
    file_tools.mixin.console = handler
    result = _run_call(
        handler,
        "edit_file",
        {"file_path": str(path), "old_content": old, "new_content": new},
        lambda: file_tools("edit_file")(str(path), old, new),
    )
    return path, result, _wire(handler)


class TestApprovedEditShowsTheDiff:
    def test_edit_result_carries_a_diff_card(self, file_tools, tmp_path):
        path, result, wire = _edit(file_tools, tmp_path, "alpha\n", "alpha", "beta")

        assert path.read_text(encoding="utf-8") == "beta\n"
        assert result["status"] == "success"
        results = _results(wire)
        assert len(results) == 1, wire
        card = results[0]
        assert card["tool"] == "edit_file"
        assert card["render"] == "diff"
        assert card["data"]["title"] == "note.txt"
        assert "-alpha" in card["data"]["unified"]
        assert "+beta" in card["data"]["unified"]
        assert "note" not in card["data"]

    def test_each_diff_line_is_terminated_once(self, file_tools, tmp_path):
        _, result, wire = _edit(
            file_tools, tmp_path, "debug = False\nport = 4200\n", "False", "True"
        )
        unified = _results(wire)[0]["data"]["unified"]
        assert "\n\n" not in unified
        assert unified.splitlines()[-3:] == [
            "-debug = False",
            "+debug = True",
            " port = 4200",
        ]
        assert result["diff"] == unified

    def test_line_ending_only_edit_still_draws_a_card(self, file_tools, tmp_path):
        path = tmp_path / "crlf.txt"
        path.write_bytes(b"a\r\nb\n")
        file_tools("read_file")(str(path))
        handler = SSEOutputHandler()
        file_tools.mixin.console = handler
        _run_call(
            handler,
            "edit_file",
            {},
            lambda: file_tools("edit_file")(str(path), "a\r\n", "a\n"),
        )
        assert path.read_bytes() == b"a\nb\n"
        (result,) = _results(_wire(handler))
        assert result["render"] == "diff"
        assert "\r" not in result["data"]["unified"]

    def test_card_matches_the_diff_card_schema(self, file_tools, tmp_path):
        # DiffCard / renderDiff accept {title?, badge?, note?, unified: str} only.
        _, _, wire = _edit(file_tools, tmp_path, "a\nb\n", "b", "c")
        data = _results(wire)[0]["data"]
        assert set(data) <= {"title", "badge", "note", "unified"}
        assert isinstance(data["unified"], str)
        assert all(isinstance(data[k], str) for k in data)

    def test_no_op_edit_draws_no_card(self, file_tools, tmp_path):
        _, result, wire = _edit(file_tools, tmp_path, "same\n", "same", "same")
        assert result["status"] == "success"
        assert "render" not in _results(wire)[0]


class TestDiffCardDoesNotLeak:
    def test_card_belongs_only_to_the_call_that_made_it(self, tmp_path):
        handler = SSEOutputHandler()
        _run_call(
            handler,
            "edit_file",
            {},
            lambda: (handler.print_diff("+x", "a.txt"), {"status": "success"})[1],
        )
        _run_call(handler, "read_file", {}, lambda: {"status": "success"})
        first, second = _results(_wire(handler))
        assert first["render"] == "diff"
        assert "render" not in second
        assert second["tool"] == "read_file"

    def test_a_call_that_never_reports_drops_its_card(self):
        # A diff drawn by a call whose result was skipped must not attach to
        # the next tool's result.
        handler = SSEOutputHandler()
        handler.print_tool_usage("edit_file")
        handler.print_diff("+x", "a.txt")
        handler.print_tool_complete()
        _run_call(handler, "list_files", {}, lambda: {"status": "success"})
        rendered = [r for r in _results(_wire(handler)) if "render" in r]
        assert rendered == []

    def test_render_map_card_wins_over_a_stray_diff(self):
        handler = SSEOutputHandler()
        handler.print_tool_usage("get_thread")
        handler.pretty_print_json({}, "Arguments")
        handler.print_diff("+x", "a.txt")
        table = {"kind": "table", "columns": ["a"], "rows": [["1"]]}
        handler.pretty_print_json({"ok": True, "data": table}, "Result")
        handler.print_tool_complete()
        (result,) = _results(_wire(handler))
        assert result["render"] == "table"
        assert result["data"] == table


class TestLargeDiffIsBounded:
    def test_long_diff_is_capped_with_a_note(self, monkeypatch):
        monkeypatch.setattr(sse_handler_module, "_DIFF_CARD_MAX_LINES", 5)
        handler = SSEOutputHandler()
        diff = "\n".join(f"+line{i}" for i in range(12))
        _run_call(
            handler,
            "edit_file",
            {},
            lambda: (handler.print_diff(diff, "big.txt"), {"status": "success"})[1],
        )
        data = _results(_wire(handler))[0]["data"]
        assert data["unified"].splitlines() == [f"+line{i}" for i in range(5)]
        assert data["note"] == "Showing the first 5 of 12 diff lines."

    def test_diff_at_the_cap_is_sent_whole(self, monkeypatch):
        monkeypatch.setattr(sse_handler_module, "_DIFF_CARD_MAX_LINES", 5)
        handler = SSEOutputHandler()
        diff = "\n".join(f"+line{i}" for i in range(5))
        _run_call(
            handler,
            "edit_file",
            {},
            lambda: (handler.print_diff(diff, "f.txt"), {"status": "success"})[1],
        )
        data = _results(_wire(handler))[0]["data"]
        assert data["unified"] == diff
        assert "note" not in data

    def test_one_huge_line_is_capped_by_characters(self, monkeypatch):
        monkeypatch.setattr(sse_handler_module, "_DIFF_CARD_MAX_CHARS", 50)
        handler = SSEOutputHandler()
        diff = "-" + "a" * 200 + "\n+" + "b" * 200
        _run_call(
            handler,
            "edit_file",
            {},
            lambda: (handler.print_diff(diff, "min.js"), {"status": "success"})[1],
        )
        data = _results(_wire(handler))[0]["data"]
        assert data["unified"] == diff[:50]
        assert data["note"] == "Showing the first 50 characters of the diff."


class TestTranslatorRenderKey:
    def test_event_render_is_forwarded(self):
        translator = CanonicalTranslator(run_id=None, agent_id="probe")
        translator.translate({"type": "tool_start", "tool": "edit_file"})
        translator.translate({"type": "tool_args", "tool": "edit_file", "args": {}})
        out = translator.translate(
            {
                "type": "tool_result",
                "render": "diff",
                "result_data": {"unified": "+x"},
            }
        )
        (result,) = [e for e in out if e["type"] == "tool_result"]
        assert result["render"] == "diff"
        assert result["data"] == {"unified": "+x"}

    @pytest.mark.parametrize("bad", ["", None, 7, ["diff"]])
    def test_a_non_string_or_empty_render_is_not_forwarded(self, bad):
        translator = CanonicalTranslator(run_id=None, agent_id="probe")
        translator.translate({"type": "tool_start", "tool": "read_file"})
        translator.translate({"type": "tool_args", "tool": "read_file", "args": {}})
        out = translator.translate({"type": "tool_result", "render": bad})
        (result,) = [e for e in out if e["type"] == "tool_result"]
        assert "render" not in result


# --- The diff card on the Agent UI's own /chat/send stream ----------------
#
# An in-process agent's handler events reach ChatView as they are, without the
# canonical translator, so the card must already sit under ``data`` there.

#: The in-process event the webui test feeds through ChatView and RenderCard.
_WEBUI_EVENT_FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "src/gaia/apps/webui/src/components/__tests__/fixtures"
    / "inprocess-edit-diff-tool-result.json"
)


class _EditingAgent:
    """Cached agent that edits one file through the real tool, then answers."""

    def __init__(self, edit, answer, relayed=False):
        self.model_id = "M-GGUF"
        self.conversation_history = []
        self.indexed_files = set()
        self._cancel_event = None
        self.console = None
        self._session_id = None
        self._edit = edit
        self._answer = answer
        self._relayed = relayed

    def _register_tools(self):
        pass

    def process_query(self, message):
        handler = helpers._active_sse_handlers.get(self._session_id)
        if self._relayed:
            # A sidecar runs the tool under its own handler and sends the
            # canonical events, which the relay re-emits on this turn's handler.
            sidecar = SSEOutputHandler()
            self._edit(sidecar)
            for event in _wire(sidecar):
                _dispatch_one(handler, event)
        else:
            self._edit(handler)
        return {
            "result": self._answer,
            "model_messages": [
                {"role": "user", "content": message},
                {"role": "assistant", "content": self._answer},
            ],
        }


@pytest.fixture
def chat_runtime():
    db = ChatDatabase(":memory:")
    manager = RunManager()
    helpers._agent_cache.clear()
    with (
        patch("gaia.ui.run_manager.run_manager", manager),
        patch.object(helpers, "_agent_registry", None),
        patch.object(helpers, "_maybe_load_expected_model"),
        patch.object(helpers, "_maybe_update_session_title", AsyncMock()),
    ):
        yield db
    helpers._agent_cache.clear()
    db.close()


@pytest.fixture
def edit_on(file_tools, tmp_path):
    """Return ``edit(handler)``: a real approved edit of note.txt on *handler*."""
    path = tmp_path / "note.txt"
    path.write_text("alpha\n", encoding="utf-8")
    file_tools("read_file")(str(path))

    def edit(handler):
        file_tools.mixin.console = handler
        _run_call(
            handler,
            "edit_file",
            {"file_path": str(path), "old_content": "alpha", "new_content": "beta"},
            lambda: file_tools("edit_file")(str(path), "alpha", "beta"),
        )

    edit.path = path
    return edit


async def _send(db, agent):
    session = db.create_session(model="M-GGUF", agent_type="chat")
    agent._session_id = session["id"]
    helpers._store_agent(session["id"], "M-GGUF", [], agent, "chat")
    request = ChatRequest(session_id=session["id"], message="Fix it", stream=True)
    events = []

    async def consume():
        async for chunk in helpers._stream_chat_response(db, session, request):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: ") :]))

    await asyncio.wait_for(consume(), timeout=15)
    return session["id"], events


def _of(events, kind):
    return [e for e in events if e.get("type") == kind]


@pytest.mark.asyncio
class TestDiffCardOnTheChatStream:
    async def test_in_process_event_carries_the_card_under_data(
        self, chat_runtime, edit_on
    ):
        _, events = await _send(chat_runtime, _EditingAgent(edit_on, "Done."))

        (result,) = _of(events, "tool_result")
        assert result["render"] == "diff"
        assert result["data"]["title"] == "note.txt"
        assert "-alpha" in result["data"]["unified"]
        assert "+beta" in result["data"]["unified"]
        assert result["data"] == result["result_data"]

    async def test_webui_fixture_is_the_real_in_process_event(
        self, chat_runtime, edit_on
    ):
        _, events = await _send(chat_runtime, _EditingAgent(edit_on, "Done."))

        (result,) = _of(events, "tool_result")
        fixture = json.loads(_WEBUI_EVENT_FIXTURE.read_text(encoding="utf-8"))
        assert set(result) == set(fixture)
        for key in ("type", "render", "data", "result_data", "success"):
            assert result[key] == fixture[key], key

    @pytest.mark.parametrize("relayed", [False, True], ids=["in_process", "relay"])
    async def test_card_persists_with_its_data(self, chat_runtime, edit_on, relayed):
        sid, _ = await _send(
            chat_runtime, _EditingAgent(edit_on, "Done.", relayed=relayed)
        )

        (step,) = chat_runtime.get_messages(sid)[-1]["agent_steps"]
        assert step["render"] == "diff"
        assert "+beta" in step["data"]["unified"]

    @pytest.mark.parametrize("relayed", [False, True], ids=["in_process", "relay"])
    async def test_edit_then_no_answer_still_says_no_answer_came(
        self, chat_runtime, edit_on, relayed
    ):
        # The diff records the edit; it does not answer the user.
        sid, events = await _send(
            chat_runtime, _EditingAgent(edit_on, "", relayed=relayed)
        )

        assert edit_on.path.read_text(encoding="utf-8") == "beta\n"
        [done] = _of(events, "done")
        assert done["content"] == helpers._NO_ANSWER_AFTER_TOOLS_MSG
        assert done["failed"] is True
        saved = chat_runtime.get_messages(sid)[-1]
        assert saved["content"] == helpers._NO_ANSWER_AFTER_TOOLS_MSG
        assert saved["agent_steps"][0]["render"] == "diff"
        assert chat_runtime.get_session(sid)["last_run_status"] == "failed"
