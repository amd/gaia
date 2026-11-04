# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``gaia memory bootstrap --infer`` against the replies local models really give.

Local models regularly stop part-way through the JSON array of insights. Before
this, that stored nothing, printed the cut-off reply to the terminal and exited
0, so neither a user nor a script could tell the run had failed. Now the
insights that arrived whole are kept, and a reply with none exits 1 without
echoing the reply.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia import cli
from gaia.agents.base.inferred_insights import parse_inferred_insights

ONE = {"content": "Primarily a Python developer", "confidence": 0.9, "domain": "work"}
TWO = {"content": "Uses VS Code daily", "confidence": 0.8, "domain": "technical"}
WHOLE = json.dumps([ONE, TWO], indent=2)
# Cut off inside the third object's string, like the replies in #3835.
CUT_OFF = WHOLE[:-2] + ',\n  {"content": "Plays strategy ga'


# ------------------------------------------------------------------- parser


def test_a_whole_array_parses():
    parsed = parse_inferred_insights(WHOLE)
    assert parsed.insights == [ONE, TWO]
    assert parsed.truncated is False


def test_markdown_fences_are_stripped():
    parsed = parse_inferred_insights(f"```json\n{WHOLE}\n```")
    assert parsed.insights == [ONE, TWO]


def test_a_cut_off_array_keeps_the_complete_insights():
    with pytest.raises(json.JSONDecodeError):
        json.loads(CUT_OFF)

    parsed = parse_inferred_insights(CUT_OFF)

    assert parsed.insights == [ONE, TWO]
    assert parsed.truncated is True


def test_a_reply_cut_off_after_a_comma_keeps_the_complete_insights():
    parsed = parse_inferred_insights(WHOLE[:-2] + ",")
    assert parsed.insights == [ONE, TWO]
    assert parsed.truncated is True


def test_text_after_a_whole_array_is_not_reported_as_cut_off():
    parsed = parse_inferred_insights(WHOLE + "\n\nLet me know if you need more!")
    assert parsed.insights == [ONE, TWO]
    assert parsed.truncated is False


def test_items_without_content_are_dropped():
    reply = json.dumps([ONE, {"content": "   "}, {"confidence": 1.0}, "text", TWO])
    assert parse_inferred_insights(reply).insights == [ONE, TWO]


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "Sorry, I can't help with that.",
        '[{"content": "never finish',
        '{"content": "an object, not an array"}',
    ],
    ids=["empty", "prose", "cut-off-first-item", "not-an-array"],
)
def test_a_reply_with_no_complete_insight_is_an_error(reply):
    with pytest.raises(ValueError) as exc:
        parse_inferred_insights(reply)
    assert "never finish" not in str(exc.value), "the reply must not be echoed"


def test_a_non_text_reply_is_an_error():
    with pytest.raises(ValueError):
        parse_inferred_insights(None)


# ---------------------------------------------------------------------- CLI


class _FakeStore:
    instances = []

    def __init__(self, *args, **kwargs):
        self.stored = []
        _FakeStore.instances.append(self)

    def get_by_category(self, *args, **kwargs):
        return []

    def delete_by_source(self, source):
        pass

    def store(self, **kwargs):
        self.stored.append(kwargs["content"])
        return "id"

    def close(self):
        pass


@pytest.fixture
def run_infer(monkeypatch):
    """Run the real ``--infer`` path with *reply* as the LLM's answer.

    Only the edges are faked: the LLM, the system scan, the memory store and
    the prompts (decline browser history, then approve every insight).
    """
    import gaia.agents.base.discovery as discovery
    import gaia.agents.base.memory_store as memory_store

    _FakeStore.instances = []
    monkeypatch.setattr(memory_store, "MemoryStore", _FakeStore)
    monkeypatch.setattr(discovery, "SystemDiscovery", lambda: MagicMock())
    monkeypatch.setattr(
        cli,
        "_collect_signal",
        lambda name, _scan: (
            [{"content": "Installed app: VS Code"}] if name == "installed_apps" else []
        ),
    )
    answers = iter(["n"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers, "y"))

    def _run(reply):
        if isinstance(reply, Exception):

            def chat(*_a, **_k):
                raise reply

        else:

            def chat(*_a, **_k):
                return reply

        monkeypatch.setattr(cli, "create_client", lambda: SimpleNamespace(chat=chat))
        cli._handle_memory_bootstrap(
            SimpleNamespace(
                reset_system=False,
                system=False,
                reset=False,
                chat_only=False,
                discover=False,
                infer=True,
            )
        )
        return [s for inst in _FakeStore.instances for s in inst.stored]

    return _run


def test_a_cut_off_reply_stores_the_complete_insights(run_infer, capsys):
    stored = run_infer(CUT_OFF)

    assert stored == [ONE["content"], TWO["content"]]
    out = capsys.readouterr().out
    assert "cut off" in out
    assert "Plays strategy" not in out, "the raw reply must not be printed"


def test_an_unusable_reply_exits_1_without_dumping_it(run_infer, capsys):
    with pytest.raises(SystemExit) as exc:
        run_infer('[{"content": "Raw scanned detail that never fini')

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Nothing was stored" in out
    assert "Raw scanned detail" not in out


def test_a_failed_llm_call_exits_1(run_infer, capsys):
    with pytest.raises(SystemExit) as exc:
        run_infer(ConnectionError("connection refused"))

    assert exc.value.code == 1
    assert "LLM call failed" in capsys.readouterr().out


def test_a_whole_reply_still_stores_everything(run_infer):
    assert run_infer(WHOLE) == [ONE["content"], TWO["content"]]
