# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Recorded turns replayed through GAIA's own answer checks, with no model.

Each file in ``tests/fixtures/answer_check_corpus/`` is one turn as it really
happened: the request, what the model said, what its tools returned. The
replay runs it through the real loop and asserts what GAIA added: a note, a
nudge back to the model, or nothing. Adding a case is adding a file; see the
README beside the cases.
"""

from __future__ import annotations

import inspect
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool

CORPUS = Path(__file__).resolve().parents[2] / "fixtures" / "answer_check_corpus"

CASE_KEYS = {
    "kind",
    "finding",
    "why",
    "request",
    "files",
    "model",
    "if_sent_back",
    "chunks",
    "question",
    "reply",
    "expect",
    "xfail",
}
EXPECT_KEYS = {
    "turn": {
        "model_calls",
        "answer_has",
        "answer_lacks",
        "model_told",
        "model_never_told",
    },
    "stream": {"shown_lacks", "shown_has"},
    "question": {"result_has", "result_is_not"},
}


def _load(path: Path) -> dict:
    case = json.loads(path.read_text(encoding="utf-8"))
    unknown = set(case) - CASE_KEYS
    if unknown:
        raise ValueError(f"{path.name}: unknown keys {sorted(unknown)}")
    kind = case.get("kind")
    if kind not in EXPECT_KEYS:
        raise ValueError(f"{path.name}: kind must be one of {sorted(EXPECT_KEYS)}")
    for key in ("finding", "why", "expect"):
        if not case.get(key):
            raise ValueError(f"{path.name}: '{key}' is required")
    unknown = set(case["expect"]) - EXPECT_KEYS[kind]
    if unknown:
        raise ValueError(f"{path.name}: unknown expect keys {sorted(unknown)}")
    return case


def _cases() -> list:
    return list(_iter_cases())


def _iter_cases():
    paths = sorted(CORPUS.glob("*.json"))
    if not paths:
        raise RuntimeError(f"no answer-check cases found in {CORPUS}")
    for path in paths:
        case = _load(path)
        marks = []
        if case.get("xfail"):
            marks.append(pytest.mark.xfail(reason=case["xfail"], strict=True))
        yield pytest.param(case, id=path.stem, marks=marks)


def _fill(value, workspace: str):
    """Put the replay's folder wherever the recording says ``{ws}``."""
    if isinstance(value, str):
        return value.replace("{ws}", workspace)
    if isinstance(value, list):
        return [_fill(item, workspace) for item in value]
    if isinstance(value, dict):
        return {
            _fill(key, workspace): _fill(item, workspace) for key, item in value.items()
        }
    return value


# ---------------------------------------------------------------------------
# kind "turn": the request, the model's replies and its tool results
# ---------------------------------------------------------------------------


def _recorded_tool(name: str, arg_names: set[str], pending: list):
    """A stand-in tool that returns what the real one returned in the recording."""

    def recorded(**tool_args):
        if not pending or pending[0]["tool"] != name:
            raise AssertionError(f"the replay called {name} out of recorded order")
        step = pending.pop(0)
        creates = step.get("creates") or {}
        for path, text in creates.items():
            # Evidence reads the file's modified time: keep it inside the call.
            time.sleep(0.05)
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        if creates:
            time.sleep(0.05)
        return step["result"]

    recorded.__name__ = name
    recorded.__doc__ = f"Recorded {name}."
    recorded.__signature__ = inspect.Signature(
        [
            inspect.Parameter(arg, inspect.Parameter.KEYWORD_ONLY, default=None)
            for arg in sorted(arg_names)
        ]
    )
    return recorded


def _replay_turn(case: dict, tmp_path: Path, monkeypatch) -> None:
    case = _fill(case, tmp_path.as_posix())
    monkeypatch.chdir(tmp_path)
    for path, text in (case.get("files") or {}).items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    steps = case["model"]
    calls = [step for step in steps if "tool" in step]
    pending = list(calls)
    arg_names: dict[str, set[str]] = {}
    for step in calls:
        arg_names.setdefault(step["tool"], set()).update(step.get("args") or {})

    class _ReplayAgent(Agent):
        def _get_system_prompt(self) -> str:
            return "test"

        def _register_tools(self) -> None:
            for name, names in arg_names.items():
                tool(_recorded_tool(name, names, pending), override=True)

        def _create_console(self):
            from gaia.agents.base.console import AgentConsole

            return AgentConsole()

    replies = []
    for step in steps:
        if "tool" in step:
            replies.append(
                json.dumps(
                    {
                        "thought": "",
                        "tool": step["tool"],
                        "tool_args": step.get("args") or {},
                    }
                )
            )
        else:
            replies.append(json.dumps({"thought": "", "answer": step["answer"]}))
    last_answer = [step["answer"] for step in steps if "answer" in step][-1]
    sent_back = json.dumps(
        {"thought": "", "answer": case.get("if_sent_back", last_answer)}
    )
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        response = MagicMock()
        response.text = replies.pop(0) if replies else sent_back
        response.stats = {}
        return response

    saved = dict(_TOOL_REGISTRY)
    try:
        with patch("gaia.agents.base.agent.AgentSDK"):
            agent = _ReplayAgent(silent_mode=True, skip_lemonade=True)
        agent.streaming = False
        agent._tool_requires_confirmation = lambda *_a, **_k: False
        agent.chat = MagicMock()
        agent.chat.send_messages = MagicMock(side_effect=_send)
        result = agent.process_query(case["request"], max_steps=12)
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)

    assert not pending, f"the replay never reached: {[s['tool'] for s in pending]}"
    answer = str(result["result"])
    told = "\n".join(
        str(message.get("content"))
        for message in sent[-1]
        if message.get("role") == "user" and message.get("content") != case["request"]
    )
    expect = case["expect"]
    if "model_calls" in expect:
        assert (
            len(sent) == expect["model_calls"]
        ), f"the model was called {len(sent)} times; GAIA told it:\n{told}"
    for text in expect.get("answer_has", []):
        assert text in answer, f"missing from the answer: {text!r}\n---\n{answer}"
    for text in expect.get("answer_lacks", []):
        assert text not in answer, f"in the answer: {text!r}\n---\n{answer}"
    for text in expect.get("model_told", []):
        assert text in told, f"GAIA never told the model: {text!r}\n---\n{told}"
    for text in expect.get("model_never_told", []):
        assert text not in told, f"GAIA told the model: {text!r}\n---\n{told}"


# ---------------------------------------------------------------------------
# kind "stream": what the provider streamed, and what the chat may show of it
# ---------------------------------------------------------------------------


def _replay_stream(case: dict) -> None:
    from gaia.chat.sdk import AgentConfig, AgentSDK

    with patch("gaia.chat.sdk.create_client") as create:
        client = MagicMock()
        client.chat.return_value = iter(case["chunks"])
        create.return_value = client
        sdk = AgentSDK(config=AgentConfig())
    sdk.get_stats = lambda: {}

    responses = list(sdk.send_messages_stream([{"role": "user", "content": "q"}]))
    # A streamed chunk is shown as it arrives; only the complete one is parsed.
    shown = "".join(r.text for r in responses if not r.is_complete)
    for text in case["expect"].get("shown_has", []):
        assert text in shown, f"not shown: {text!r}"
    for text in case["expect"].get("shown_lacks", []):
        assert text not in shown, f"shown to the user as answer text: {text!r}"


# ---------------------------------------------------------------------------
# kind "question": a question the agent asked, and the reply (or silence)
# ---------------------------------------------------------------------------


class _Console:
    def __init__(self, reply):
        self.reply = reply

    def request_user_input_blocking(self, default_if_no_response=None, **_kwargs):
        if self.reply is None:
            # Nobody answered: the window hands back the default, or the marker.
            if default_if_no_response is not None:
                return default_if_no_response
            return "__NO_RESPONSE__"
        return self.reply


def _replay_question(case: dict) -> None:
    # The unit job has no flagship package; test_gaia_agent.yml runs this.
    chat_agent = pytest.importorskip("gaia_agent.chat_agent")
    ChatAgent = chat_agent.ChatAgent

    stub = object.__new__(ChatAgent)
    stub.console = _Console(case.get("reply"))
    stub.observers = []
    saved = dict(_TOOL_REGISTRY)
    try:
        ChatAgent._register_loop_control_tools(stub)
        result = _TOOL_REGISTRY["request_user_input"]["function"](**case["question"])
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)

    for text in case["expect"].get("result_has", []):
        assert text in result, f"missing from what the model is told: {text!r}"
    for text in case["expect"].get("result_is_not", []):
        assert result != text, f"the model was handed {text!r} as the user's answer"


@pytest.mark.parametrize("case", _cases())
def test_recorded_turn(case, tmp_path, monkeypatch):
    if case["kind"] == "turn":
        _replay_turn(case, tmp_path, monkeypatch)
    elif case["kind"] == "stream":
        _replay_stream(case)
    else:
        _replay_question(case)


def test_every_case_file_is_collected():
    """A case saved under another extension would silently never run."""
    strays = [
        p.name
        for p in CORPUS.iterdir()
        if p.suffix != ".json" and p.name != "README.md"
    ]
    assert not strays, f"not a .json case or the README: {strays}"


def test_a_path_in_a_recording_is_portable():
    """A recording that keeps the tester's own folder only replays on their PC."""
    offenders = []
    for path in sorted(CORPUS.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        if any(mark in text for mark in (":\\\\", "/home/", "/Users/")):
            offenders.append(path.name)
    assert not offenders, f"use {{ws}} for the turn's folder: {offenders}"
