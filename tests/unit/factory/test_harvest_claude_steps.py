# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for the two optional Claude Code steps (#3962).

These never invoke the real CLI. What they pin down is the contract that makes
the steps safe to add to an otherwise deterministic, offline pipeline: every
failure is terminal and named, and nothing partial is ever written.
"""

import json

import pytest

from gaia.factory.harvest import classify, synthesize
from gaia.factory.harvest.claude_cli import extract_json, resolve_claude

# ---------------------------------------------------------------- claude_cli


def test_missing_binary_names_the_manual_path(monkeypatch):
    """No Claude Code must not be a dead end — the work is doable by hand."""
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.delenv("GAIA_CLAUDE_BIN", raising=False)
    with pytest.raises(SystemExit) as err:
        resolve_claude()
    msg = str(err.value)
    assert "not found" in msg and "SKILL.md" in msg


def test_binary_override_is_honoured(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: f"/opt/{name}")
    monkeypatch.setenv("GAIA_CLAUDE_BIN", "my-claude")
    assert resolve_claude() == "/opt/my-claude"


@pytest.mark.parametrize(
    "reply,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Sure!\n```\n{"a": 1}\n```\nHope that helps', {"a": 1}),
        ('Here it is: {"a": 1}', {"a": 1}),
    ],
)
def test_json_is_recovered_from_chatty_replies(reply, expected):
    assert extract_json(reply) == expected


def test_unparseable_reply_is_an_error_not_an_empty_result():
    with pytest.raises(SystemExit) as err:
        extract_json("I could not do that.", what="labels")
    assert "Could not parse JSON" in str(err.value)


# ------------------------------------------------------------------ classify


def intents(*pairs):
    return [{"session_id": f"{p}-uuid", "goal": g} for p, g in pairs]


def test_batch_must_be_labelled_exactly():
    """A dropped session would silently shrink the labelled population."""
    batch = intents(("aaaaaaaa", "fix CI"), ("bbbbbbbb", "review PR"))
    with pytest.raises(SystemExit) as err:
        classify.validate({"aaaaaaaa": "ci_debug"}, batch)
    assert "unlabelled" in str(err.value)


def test_invented_session_ids_are_rejected():
    batch = intents(("aaaaaaaa", "fix CI"))
    with pytest.raises(SystemExit) as err:
        classify.validate({"aaaaaaaa": "ci_debug", "zzzzzzzz": "research"}, batch)
    assert "invented" in str(err.value)


def test_taxonomy_is_closed():
    """Free-form tags make per-use-case tables incomparable between batches."""
    batch = intents(("aaaaaaaa", "fix CI"))
    with pytest.raises(SystemExit) as err:
        classify.validate({"aaaaaaaa": "vibes"}, batch)
    assert "not in the taxonomy" in str(err.value)


def test_valid_batch_returns_prefix_to_tag():
    batch = intents(("aaaaaaaa", "fix CI"), ("bbbbbbbb", "review PR"))
    got = classify.validate({"aaaaaaaa": "ci_debug", "bbbbbbbb": "code_review"}, batch)
    assert got == {"aaaaaaaa": "ci_debug", "bbbbbbbb": "code_review"}


def test_non_object_reply_is_rejected():
    with pytest.raises(SystemExit) as err:
        classify.validate(["ci_debug"], intents(("aaaaaaaa", "x")))
    assert "JSON object" in str(err.value)


def test_sessions_without_an_instruction_are_labelled_locally():
    """Unclassifiable is a fact about the transcript, not a model judgement."""
    askable, fixed = classify.split_unclassifiable(
        intents(("aaaaaaaa", "fix CI"), ("bbbbbbbb", ""), ("cccccccc", "   "))
    )
    assert [r["session_id"][:8] for r in askable] == ["aaaaaaaa"]
    assert fixed == {
        "bbbbbbbb": classify.NO_INSTRUCTION,
        "cccccccc": classify.NO_INSTRUCTION,
    }


def test_prompt_carries_the_opening_instruction_not_the_title():
    batch = [{"session_id": "aaaaaaaa-x", "goal": "fix the CI", "title": "Fixed CI"}]
    prompt = classify.build_prompt(batch)
    assert "fix the CI" in prompt and "Fixed CI" not in prompt


def test_missing_intents_points_at_scan(tmp_path):
    with pytest.raises(SystemExit) as err:
        classify.load_intents(tmp_path)
    assert "harvest.scan" in str(err.value)


# ---------------------------------------------------------------- synthesize


def test_synthesis_requires_the_evidence_first(tmp_path):
    with pytest.raises(SystemExit) as err:
        synthesize.gather(tmp_path)
    assert "harvest.report" in str(err.value)


def test_synthesis_uses_whatever_reports_exist(tmp_path):
    (tmp_path / "tables.md").write_text("## Corpus")
    (tmp_path / "savings.md").write_text("## Savings")
    evidence, found = synthesize.gather(tmp_path)
    assert found == ["tables.md", "savings.md"]
    assert "## Corpus" in evidence and "## Savings" in evidence


def test_honesty_rules_reach_the_prompt():
    """These are the contract, not advice — they must actually be sent."""
    prompt = " ".join(
        synthesize.PROMPT.format(
            focus=synthesize.FOCUS, honesty=synthesize.HONESTY, evidence="x"
        ).split()
    ).lower()
    assert "never present a tool-failure rate as a task-failure rate" in prompt
    assert "no ground truth for task success" in prompt
    assert "api-equivalent, not money spent" in prompt
    assert "do not estimate, extrapolate" in prompt


def test_classify_writes_nothing_when_a_batch_fails(tmp_path, monkeypatch):
    """A partial labels.txt is the failure #3934 makes report refuse to render."""
    cache = tmp_path
    rows = [
        {"session_id": f"{p}-uuid", "goal": "work"} for p in ("aaaaaaaa", "bbbbbbbb")
    ]
    (cache / "intents.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
    )
    monkeypatch.setattr(
        classify, "run_claude", lambda *a, **k: '{"aaaaaaaa": "ci_debug"}'
    )
    monkeypatch.setattr("sys.argv", ["classify", "--cache", str(cache), "--batch", "2"])
    with pytest.raises(SystemExit):
        classify.main()
    assert not (cache / "labels.txt").exists()


def _one_session_cache(cache):
    (cache / "intents.jsonl").write_text(
        json.dumps({"session_id": "aaaaaaaa-uuid", "goal": "work"}), encoding="utf-8"
    )


@pytest.mark.parametrize(
    "argv_tail, expected",
    [
        (["--out", "MISSING/labels.txt"], "does not exist"),
        (["--batch", "0"], "--batch must be at least 1"),
    ],
)
def test_bad_arguments_fail_before_any_model_call(
    tmp_path, monkeypatch, argv_tail, expected
):
    """Every batch is model spend and the write is last — check the args first."""
    _one_session_cache(tmp_path)
    called = []
    monkeypatch.setattr(
        classify, "run_claude", lambda *a, **k: called.append(1) or "{}"
    )
    tail = [a.replace("MISSING", str(tmp_path / "nope")) for a in argv_tail]
    monkeypatch.setattr("sys.argv", ["classify", "--cache", str(tmp_path), *tail])
    with pytest.raises(SystemExit) as err:
        classify.main()
    assert expected in str(err.value)
    assert not called
