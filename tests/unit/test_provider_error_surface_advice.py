# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A provider that rejects the key mid-turn gets advice for the user's front-end.

Measured in the Agent UI on rc16 (#5186): a hosted model answered HTTP 403
right after a successful ``write_file``. The turn ended telling the user to
"Run /provider in the TUI" and "verify LEMONADE_API_KEY", then added "I can't
confirm this is done" although the file was saved with the right content.
"""

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import gaia.ui._chat_helpers as helpers
from gaia.agents.base.agent import Agent
from gaia.agents.base.console import AgentConsole
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.cli import GaiaCliClient
from gaia.llm.lemonade_client import LemonadeClientError, _cloud_request_error
from gaia.llm.providers.lemonade import (
    LemonadeAuthRejectedError,
    classify_lemonade_exception,
)
from gaia.llm.surface_hints import (
    AGENT_UI_PROVIDER_AUTH_HINT,
    AGENT_UI_PROVIDER_SETTINGS,
    CLI_MODEL_SWITCH_HINT,
    CLI_PROVIDER_AUTH_HINT,
    TUI_MODEL_SWITCH_HINT,
    TUI_PROVIDER_AUTH_HINT,
    TUI_PROVIDER_SETTINGS,
    surface_wording,
)
from gaia.ui._chat_helpers import _agent_ui_wording, _classify_chat_exception
from gaia.ui.database import ChatDatabase
from gaia.ui.models import ChatRequest


def _wrapped(error: BaseException) -> RuntimeError:
    """How AgentSDK hands a client error to the agent loop."""
    try:
        raise error
    except Exception as e:
        try:
            raise RuntimeError(f"Error in send_messages: {e}") from e
        except RuntimeError as wrapped:
            return wrapped


def _tui_only(text: str) -> list:
    """TUI-only advice still present in *text*."""
    return [s for s in ("TUI", "/provider", "/model") if s in text]


# --- the wording itself -------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_auth_rejection_names_each_surfaces_own_fix(status):
    tui = str(_cloud_request_error(status, "amd"))
    assert tui.startswith(f"Cloud authentication or access was denied (HTTP {status})")
    assert tui.endswith(TUI_PROVIDER_AUTH_HINT)

    ui = surface_wording(tui, "agent_ui")
    assert ui.startswith(f"Cloud authentication or access was denied (HTTP {status})")
    assert ui.endswith(AGENT_UI_PROVIDER_AUTH_HINT)
    assert "Settings > Model" in ui
    assert not _tui_only(ui), ui
    # A hosted-provider 403 is not a Lemonade key problem; the UI doesn't say so.
    assert "LEMONADE_API_KEY" not in ui

    cli = surface_wording(tui, "cli")
    assert cli.endswith(CLI_PROVIDER_AUTH_HINT)
    assert "run the command again" in cli


@pytest.mark.parametrize("status", [429, 412, None, 500])
def test_other_cloud_failures_point_at_the_surfaces_settings(status):
    tui = str(_cloud_request_error(status, "fireworks"))
    assert TUI_PROVIDER_SETTINGS in tui
    ui = surface_wording(tui, "agent_ui")
    assert AGENT_UI_PROVIDER_SETTINGS in ui and not _tui_only(ui), ui
    cli = surface_wording(tui, "cli")
    assert TUI_PROVIDER_SETTINGS not in cli and "Settings > Model" in cli


def test_the_tui_keeps_its_own_wording_and_rewording_is_idempotent():
    for status in (401, 403, 404, 429, 412, None):
        tui = str(_cloud_request_error(status, "amd"))
        assert surface_wording(tui, "tui") == tui
        for surface in ("agent_ui", "cli"):
            once = surface_wording(tui, surface)
            assert surface_wording(once, surface) == once


def test_an_unknown_surface_fails_loudly():
    with pytest.raises(ValueError, match="Unknown front-end 'webui'"):
        surface_wording("anything", "webui")


@pytest.mark.parametrize("text", ["", "All done.", "Use /provider in the TUI later"])
def test_text_without_tui_advice_is_untouched(text):
    for surface in ("agent_ui", "cli"):
        assert surface_wording(text, surface) == text


def test_the_model_switch_hint_has_a_cli_form():
    assert surface_wording(TUI_MODEL_SWITCH_HINT, "cli") == CLI_MODEL_SWITCH_HINT
    assert "TUI" not in CLI_MODEL_SWITCH_HINT


# --- classification: 401 vs 403 vs 429 ---------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_401_and_403_classify_as_auth_with_the_tui_text_intact(status):
    """Classification runs before any rewording, on the client's own text."""
    for exc in (
        _wrapped(_cloud_request_error(status)),
        RuntimeError(f"Error in send_messages: {_cloud_request_error(status)}"),
    ):
        err = _classify_chat_exception(exc)
        assert isinstance(err, LemonadeAuthRejectedError)
        assert err.retryable is False
        assert err.user_message.endswith(TUI_PROVIDER_AUTH_HINT)


def test_a_429_is_never_called_an_auth_rejection():
    err = classify_lemonade_exception(_wrapped(_cloud_request_error(429)))
    assert not isinstance(err, LemonadeAuthRejectedError)
    flat = classify_lemonade_exception(
        RuntimeError(f"Error in send_messages: {_cloud_request_error(429)}")
    )
    assert not isinstance(flat, LemonadeAuthRejectedError)


def test_an_agent_ui_reworded_message_is_not_reclassified_as_tui_text():
    """The Agent UI copy drops LEMONADE_API_KEY; it must not fake an auth match
    for unrelated text that merely mentions Settings > Model."""
    assert (
        classify_lemonade_exception(RuntimeError(AGENT_UI_PROVIDER_AUTH_HINT)) is None
    )


def test_the_default_auth_message_uses_the_shared_hint():
    msg = LemonadeAuthRejectedError().user_message
    assert msg.endswith(TUI_PROVIDER_AUTH_HINT)
    assert not _tui_only(_agent_ui_wording(msg))


# --- the agent loop: a mid-turn rejection after work finished -----------------


class _WriterAgent(Agent):
    def _get_system_prompt(self):
        return "Use the tools."

    def _register_tools(self):
        agent = self

        @tool
        def write_file(file_path: str, content: str) -> dict:
            """Write a file."""
            if agent.write_fails:
                return {"status": "error", "error": "disk full"}
            Path(file_path).write_text(content, encoding="utf-8")
            return {"status": "success", "file_path": file_path}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = _WriterAgent(silent_mode=True, skip_lemonade=True)
    agent.write_fails = False
    agent.streaming = False
    agent._tool_requires_confirmation = lambda *a, **kw: False
    agent.console = MagicMock()
    agent.console.cancelled = None
    yield agent
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


QUERY = "Create one.txt containing the word one. Use write_file."


def _write_then(agent, tmp_path, error, before_error=None):
    """Script: one successful write_file call, then *error* on the next call."""
    target = str(tmp_path / "one.txt")
    turns = [
        {"tool": "write_file", "tool_args": {"file_path": target, "content": "one"}},
        error,
    ]

    def reply():
        step = turns.pop(0)
        if isinstance(step, BaseException):
            if before_error:
                before_error()
            raise step
        return json.dumps(step)

    def send(*_a, **_kw):
        return MagicMock(text=reply(), stats={}, finish_reason="stop")

    def stream(*_a, **_kw):
        yield SimpleNamespace(
            is_complete=True, text=reply(), stats={}, finish_reason="stop"
        )

    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = send
    agent.chat.send_messages_stream.side_effect = stream
    return target


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("status", [401, 403])
def test_finished_work_is_reported_saved_not_unconfirmed(
    agent, tmp_path, status, streaming
):
    agent.streaming = streaming
    target = _write_then(agent, tmp_path, _wrapped(_cloud_request_error(status, "amd")))
    result = agent.process_query(QUERY)
    answer = result["result"]

    assert Path(target).read_text(encoding="utf-8") == "one"
    assert answer.startswith(
        f"Cloud authentication or access was denied (HTTP {status})"
    ), answer
    assert "can't confirm" not in answer and "unfinished" not in answer, answer
    assert f"Saved before the error: `{target}`" in answer
    assert "temporary" not in answer
    assert result["status"] == "failed", "the turn still ended on an error"
    # The flagship sidecar serves the TUI: the loop keeps the TUI's advice.
    assert TUI_PROVIDER_AUTH_HINT in answer


def test_a_429_after_finished_work_still_reports_the_save(agent, tmp_path):
    target = _write_then(agent, tmp_path, _wrapped(_cloud_request_error(429, "amd")))
    answer = agent.process_query(QUERY)["result"]
    assert "HTTP 429" in answer
    assert "Cloud authentication" not in answer
    assert "can't confirm" not in answer
    assert f"Saved before the error: `{target}`" in answer


def test_a_failed_write_is_still_reported_unfinished(agent, tmp_path):
    """Adversarial: only work a tool reported done is called saved."""
    agent.write_fails = True
    target = _write_then(agent, tmp_path, _wrapped(_cloud_request_error(403, "amd")))
    answer = agent.process_query(QUERY)["result"]
    assert not Path(target).exists()
    assert "Saved before the error" not in answer
    assert "I can't confirm this is done" in answer
    assert "No successful write to `one.txt`" in answer


def test_a_file_gone_before_the_error_is_not_called_saved(agent, tmp_path):
    """Adversarial: a write the tool reported, then the file vanished."""
    target = str(tmp_path / "one.txt")
    _write_then(
        agent,
        tmp_path,
        _wrapped(_cloud_request_error(403, "amd")),
        before_error=lambda: Path(target).unlink(),
    )
    answer = agent.process_query(QUERY)["result"]
    assert "Saved before the error" not in answer


def test_an_error_before_any_tool_ran_adds_no_saved_note(agent):
    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = _wrapped(_cloud_request_error(403, "amd"))
    answer = agent.process_query(QUERY)["result"]
    assert answer.startswith("Cloud authentication or access was denied (HTTP 403)")
    assert "Saved before the error" not in answer
    assert agent.chat.send_messages.call_count == 1, "a 403 must not be retried"


def test_a_model_answer_still_needs_its_readback(agent, tmp_path):
    """Adversarial: with no error, a claimed save is still checked as before."""
    target = str(tmp_path / "one.txt")
    turns = [
        {"tool": "write_file", "tool_args": {"file_path": target, "content": "one"}},
        {"answer": "Saved one.txt."},
        {"answer": "Saved one.txt."},
        {"answer": "Saved one.txt."},
    ]
    agent.chat = MagicMock()
    agent.chat.send_messages.side_effect = lambda *a, **k: MagicMock(
        text=json.dumps(turns.pop(0)) if turns else json.dumps({"answer": "x"}),
        stats={},
        finish_reason="stop",
    )
    answer = agent.process_query(QUERY, max_steps=4)["result"]
    assert "Saved before the error" not in answer


# --- the Agent UI shows its own advice ----------------------------------------


class _RejectedAgent:
    """Cached-agent stand-in whose turn ends the way the real loop's does."""

    def __init__(self, raise_instead=False):
        self.indexed_files = set()
        self.conversation_history = []
        self.rag = None
        self.path_validator = None
        self._cancel_event = threading.Event()
        self.console = None
        self.raise_instead = raise_instead

    def _register_tools(self):
        pass

    def process_query(self, _message, **_kwargs):
        error = _cloud_request_error(403, "amd")
        if self.raise_instead:
            raise _wrapped(error)
        msg = f"{error}\n\nSaved before the error: `C:/work/one.txt`."
        if self.console is not None:
            self.console.print_final_answer(msg)
        return {"result": msg, "status": "failed"}


def _ui_chat(monkeypatch, stand_in):
    monkeypatch.setattr(helpers, "_agent_registry", None)
    monkeypatch.setattr(helpers, "_maybe_load_expected_model", lambda *a, **k: None)
    helpers._agent_cache.clear()
    db = ChatDatabase(":memory:")
    session = db.create_session(model="M-GGUF", agent_type="chat")
    helpers._store_agent(session["id"], "M-GGUF", [], stand_in, "chat")
    return db, db.get_session(session["id"])


def _drain(db, session):
    request = ChatRequest(session_id=session["id"], message="Hello", stream=True)
    events = []

    async def drain():
        async for chunk in helpers._stream_chat_impl(
            SimpleNamespace(handler=None), db, session, request
        ):
            for line in chunk.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))

    asyncio.run(drain())
    return events


@pytest.mark.parametrize("raise_instead", [False, True])
def test_the_agent_ui_stream_names_settings_not_the_tui(monkeypatch, raise_instead):
    db, session = _ui_chat(monkeypatch, _RejectedAgent(raise_instead))
    try:
        events = _drain(db, session)
        shown = [
            e["content"]
            for e in events
            if e.get("type") in ("answer", "agent_error", "error", "done")
            and e.get("content")
        ]
        assert shown, events
        for text in shown:
            assert not _tui_only(text), text
            assert "LEMONADE_API_KEY" not in text
        assert any(AGENT_UI_PROVIDER_AUTH_HINT in t for t in shown), shown
        stored = db.get_messages(session["id"])[-1]["content"]
        assert AGENT_UI_PROVIDER_AUTH_HINT in stored and not _tui_only(stored)
        if not raise_instead:
            assert "Saved before the error" in stored
    finally:
        helpers._agent_cache.clear()
        db.close()


def test_the_agent_ui_non_streaming_reply_names_settings(monkeypatch):
    db, session = _ui_chat(monkeypatch, _RejectedAgent())
    try:
        request = ChatRequest(session_id=session["id"], message="Hello", stream=False)
        reply = asyncio.run(helpers._get_chat_response(db, session, request))
        assert AGENT_UI_PROVIDER_AUTH_HINT in reply and not _tui_only(reply)
    finally:
        helpers._agent_cache.clear()
        db.close()


# --- the CLI shows its own advice ---------------------------------------------


def test_the_cli_console_rewords_the_answer_and_the_error(capsys):
    console = AgentConsole()
    tui = str(_cloud_request_error(403, "amd"))
    console.print_final_answer(tui)
    console.print_error(tui)
    out = " ".join(capsys.readouterr().out.split())
    assert "Run /provider in the TUI to reconnect" not in out
    assert "run the command again" in out


def test_gaia_chat_cli_error_names_both_front_ends(capsys):
    client = GaiaCliClient.__new__(GaiaCliClient)
    client.log = MagicMock()
    with patch("gaia.chat.sdk.AgentSDK") as sdk:
        sdk.return_value.send_stream.side_effect = _wrapped(
            _cloud_request_error(403, "amd")
        )
        with pytest.raises(SystemExit):
            client.chat(message="hi")
    out = capsys.readouterr().out
    assert "HTTP 403" in out
    assert TUI_PROVIDER_AUTH_HINT not in out
    assert out.count(CLI_PROVIDER_AUTH_HINT) == 1, "the remedy is printed once"


def test_gaia_llm_cli_stream_error_names_both_front_ends(capsys):
    client = GaiaCliClient.__new__(GaiaCliClient)
    client.log = MagicMock()
    client.model = "amd.model"
    client.max_tokens = 16
    client.llm_client = MagicMock()
    client.llm_client.generate.side_effect = LemonadeClientError(
        str(_cloud_request_error(401, "amd"))
    )

    async def collect():
        return [chunk async for chunk in client.send_message("hi")]

    chunks = asyncio.run(collect())
    assert chunks and chunks[-1].count(CLI_PROVIDER_AUTH_HINT) == 1
    assert TUI_PROVIDER_AUTH_HINT not in chunks[-1]


def test_a_cli_error_with_a_different_cause_keeps_its_details():
    from gaia.cli import _cli_error_text

    text = _cli_error_text(RuntimeError("Error: model 'Qwen3-0.6B-GGUF' was not found"))
    assert "Details: Error: model 'Qwen3-0.6B-GGUF' was not found" in text
