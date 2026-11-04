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
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gaia import cli
from gaia.agents.base.inferred_insights import (
    normalize_confidence,
    parse_inferred_insights,
)

ONE = {"content": "Primarily a Python developer", "confidence": 0.9, "domain": "work"}
TWO = {"content": "Uses VS Code daily", "confidence": 0.8, "domain": "technical"}
WHOLE = json.dumps([ONE, TWO], indent=2)
# Cut off inside the third object's string, like the replies in #3835.
CUT_OFF = WHOLE[:-2] + ',\n  {"content": "Plays strategy ga'


# ------------------------------------------------------------------- parser


def test_a_whole_array_parses():
    parsed = parse_inferred_insights(WHOLE)
    assert parsed.insights == [ONE, TWO]
    assert parsed.incomplete is False


def test_markdown_fences_are_stripped():
    parsed = parse_inferred_insights(f"```json\n{WHOLE}\n```")
    assert parsed.insights == [ONE, TWO]


def test_a_cut_off_array_keeps_the_complete_insights():
    with pytest.raises(json.JSONDecodeError):
        json.loads(CUT_OFF)

    parsed = parse_inferred_insights(CUT_OFF)

    assert parsed.insights == [ONE, TWO]
    assert parsed.incomplete is True


def test_a_reply_cut_off_after_a_comma_keeps_the_complete_insights():
    parsed = parse_inferred_insights(WHOLE[:-2] + ",")
    assert parsed.insights == [ONE, TWO]
    assert parsed.incomplete is True


def test_a_malformed_item_keeps_the_insights_before_it():
    # The bad value's end is unknown, so parsing cannot resume after it.
    reply = '[{"content": "a"}, {"content": oops}, {"content": "b"}]'
    parsed = parse_inferred_insights(reply)
    assert parsed.insights == [{"content": "a"}]
    assert parsed.incomplete is True


def test_text_after_a_whole_array_is_not_reported_as_cut_off():
    parsed = parse_inferred_insights(WHOLE + "\n\nLet me know if you need more!")
    assert parsed.insights == [ONE, TWO]
    assert parsed.incomplete is False


def test_items_without_content_are_dropped_and_reported():
    reply = json.dumps([ONE, {"content": "   "}, {"confidence": 1.0}, "text", TWO])
    parsed = parse_inferred_insights(reply)
    assert parsed.insights == [ONE, TWO]
    assert parsed.incomplete is True


def test_an_empty_array_is_no_insights_not_an_error():
    assert parse_inferred_insights("[]") == ([], False)


def test_an_array_with_no_usable_item_is_an_error():
    with pytest.raises(ValueError, match="none of the 3"):
        parse_inferred_insights('[{"content": ""}, {"confidence": 1}, 7]')


# --------------------------------------------------------- adversarial parser


@pytest.mark.parametrize(
    "content",
    [
        "Likes ] and } and [ in text",
        'Says "quoted" things',
        "Ends with a backslash \\",
        "Unicode été and an emoji \U0001f600",
        "```fenced inside```",
    ],
)
def test_brackets_quotes_and_escapes_inside_content_survive_a_cut_off(content):
    first = json.dumps({"content": content})
    reply = f'[{first}, {{"content": "cut'
    parsed = parse_inferred_insights(reply)
    assert parsed.insights == [{"content": content}]
    assert parsed.incomplete is True


def test_nested_values_inside_an_item_do_not_end_the_item_early():
    item = {"content": "a", "tags": [{"x": "]"}, ["}", "["]], "meta": {"k": "]"}}
    parsed = parse_inferred_insights(json.dumps([item])[:-1])
    assert parsed.insights == [item]
    assert parsed.incomplete is True


def test_fences_without_a_language_tag_are_stripped():
    assert parse_inferred_insights(f"```\n{WHOLE}\n```").insights == [ONE, TWO]


def test_a_cut_off_fenced_reply_keeps_the_complete_insights():
    parsed = parse_inferred_insights(f"```json\n{CUT_OFF}")
    assert parsed.insights == [ONE, TWO]
    assert parsed.incomplete is True


def test_a_leading_bom_does_not_lose_the_reply():
    assert parse_inferred_insights("\ufeff" + WHOLE).insights == [ONE, TWO]


def test_a_missing_comma_between_items_keeps_both():
    reply = json.dumps(ONE) + "\n" + json.dumps(TWO)
    assert parse_inferred_insights(f"[{reply}]").insights == [ONE, TWO]


def test_prose_brackets_before_the_array_fail_loudly_not_silently():
    # The first "[" belongs to prose, so nothing parses; that must be an error.
    with pytest.raises(ValueError):
        parse_inferred_insights("Here [as asked]: " + CUT_OFF)


@pytest.mark.parametrize(
    "reply",
    ['{"insights": [1]}', "42", '"just a string"', "null", "true"],
)
def test_whole_json_that_is_not_an_array_is_an_error(reply):
    with pytest.raises(ValueError, match="not an array"):
        parse_inferred_insights(reply)


def test_a_deeply_nested_reply_is_a_value_error_not_a_crash():
    with pytest.raises(ValueError, match="nested too deeply"):
        parse_inferred_insights("[" * 100_000 + "]" * 100_000)


def test_a_huge_cut_off_reply_keeps_every_complete_item():
    items = [{"content": f"insight {n}"} for n in range(20_000)]
    parsed = parse_inferred_insights(json.dumps(items)[:-30])
    assert parsed.incomplete is True
    assert parsed.insights == items[: len(parsed.insights)]
    assert len(parsed.insights) > 19_990


@pytest.mark.parametrize("reply", [b"[]", 0, ["[]"], {"content": "x"}])
def test_any_non_text_reply_is_an_error(reply):
    with pytest.raises(ValueError, match="not text"):
        parse_inferred_insights(reply)


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
    assert "incomplete or malformed" in out
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


def test_an_unusable_reply_is_not_written_to_the_log(run_infer, caplog):
    # `gaia diagnostics` bundles the log, and the reply can echo scanned data.
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SystemExit):
        run_infer('[{"content": "Visited internal.corp.example 212 tim')
    assert "internal.corp.example" not in caplog.text


def test_an_array_of_unusable_items_exits_1_and_stores_nothing(run_infer, capsys):
    with pytest.raises(SystemExit) as exc:
        run_infer('[{"text": "wrong key"}]')
    assert exc.value.code == 1
    assert "Nothing was stored" in capsys.readouterr().out


# ------------------------------------------------------------ confidence (#6637)


@pytest.mark.parametrize(
    "value, expected",
    [
        (0.8, 0.8),
        (1, 1.0),
        (1.7, 1.0),
        (-0.2, 0.0),
        ("0.6", 0.6),
        ("high", 0.9),
        ("HIGH ", 0.9),
        (" Medium", 0.7),
        ("low", 0.4),
    ],
)
def test_confidence_values_a_model_returns_are_normalised(value, expected):
    assert normalize_confidence(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    "value", ["very sure", "", None, [0.9], {"v": 1}, True, float("nan")]
)
def test_unusable_confidence_values_are_rejected(value):
    assert normalize_confidence(value) is None


def test_a_word_confidence_does_not_crash_the_review(run_infer, capsys):
    reply = json.dumps(
        [
            ONE,
            {"content": "Writes Rust on weekends", "confidence": "high"},
            {"content": "Likes jazz", "confidence": "very sure"},
            {"content": "Uses Linux", "confidence": None},
            TWO,
        ]
    )
    stored = run_infer(reply)

    assert stored == [ONE["content"], "Writes Rust on weekends", TWO["content"]]
    out = capsys.readouterr().out
    assert "[90%] Writes Rust on weekends" in out
    assert "'very sure' is not a number or low/medium/high: Likes jazz" in out
    assert "None is not a number or low/medium/high: Uses Linux" in out


def test_a_missing_confidence_still_defaults(run_infer, capsys):
    stored = run_infer(json.dumps([{"content": "Uses Vim"}]))
    assert stored == ["Uses Vim"]
    assert "[70%] Uses Vim" in capsys.readouterr().out
