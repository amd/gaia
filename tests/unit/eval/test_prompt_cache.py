# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The driver's fixed instructions form one prefix every scenario can share.

The prompt cache only serves a prefix that is byte-for-byte identical, and
before this every scenario put its own YAML in the middle of the rubric, so
each one re-wrote ~33K tokens to the cache (57% of a sweep's Claude spend,
#5493). These pin that the prefix carries nothing scenario-specific, that the
runner sends it as a system-prompt file, and that the re-judge harness used to
prove the move kept verdicts compares like with like.
"""

import json
from types import SimpleNamespace

import pytest

from gaia.eval import rejudge, runner
from gaia.eval.runner import build_scenario_prompt, build_scenario_prompt_parts


def _scenario(sid, persona="casual_user"):
    return {
        "id": sid,
        "category": "rag_quality",
        "persona": persona,
        "setup": {"index_documents": [{"path": f"eval/corpus/{sid}.md"}]},
        "turns": [
            {"turn": 1, "objective": f"ask about {sid}", "success_criteria": "ok"}
        ],
    }


MANIFEST = {"documents": [{"filename": "a.md", "facts": ["x"]}]}


def test_the_prefix_is_identical_for_every_scenario():
    one, msg_one = build_scenario_prompt_parts(
        _scenario("alpha_unique"), MANIFEST, "http://localhost:4200", agent_type="gaia"
    )
    two, msg_two = build_scenario_prompt_parts(
        _scenario("bravo_unique", "power_user"),
        MANIFEST,
        "http://localhost:4200",
        agent_type="gaia",
    )
    assert one == two
    assert "alpha_unique" not in one and "bravo_unique" not in one
    assert "alpha_unique" in msg_one and "bravo_unique" in msg_two


def test_the_prefix_carries_the_whole_rubric_and_task():
    prefix, message = build_scenario_prompt_parts(
        _scenario("alpha"), MANIFEST, "http://localhost:4200", agent_type="gaia"
    )
    for section in (
        "## SCORING RULES AND RUBRIC",
        "## PER-TURN JUDGE INSTRUCTIONS",
        "## SCENARIO-LEVEL JUDGE INSTRUCTIONS",
        "## CORPUS MANIFEST (ground truth)",
        "## DOCUMENT PATHS",
        "## YOUR TASK",
        "### Phase 6: Return result",
        'agent_type="gaia"',
    ):
        assert section in prefix
        assert section not in message
    assert message.startswith("## SCENARIO\n```yaml\n")


def test_the_whole_prompt_is_prefix_then_scenario():
    args = (_scenario("alpha"), MANIFEST, "http://localhost:4200")
    prefix, message = build_scenario_prompt_parts(*args)
    assert build_scenario_prompt(*args) == f"{prefix}\n{message}"


def _launch(tmp_path, monkeypatch, scenario):
    seen = {}

    def driver(cmd, *, input, timeout, cwd):  # pylint: disable=redefined-builtin
        seen["cmd"], seen["input"] = cmd, input
        payload = {
            "structured_output": {
                "scenario_id": scenario["id"],
                "status": "PASS",
                "overall_score": 9,
                "turns": [],
            }
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(runner, "_run_driver", driver)
    runner.run_scenario_subprocess(
        tmp_path / "s.yaml",
        scenario,
        tmp_path,
        "http://localhost:4200",
        "claude-sonnet-4-6",
        "1.00",
        30,
    )
    return seen


def test_the_runner_sends_the_prefix_as_a_system_prompt_file(tmp_path, monkeypatch):
    first = _launch(tmp_path, monkeypatch, _scenario("alpha"))
    cmd = first["cmd"]
    path = cmd[cmd.index("--append-system-prompt-file") + 1]
    assert "--exclude-dynamic-system-prompt-sections" in cmd
    prefix = open(path, encoding="utf-8").read()
    assert "## SCORING RULES AND RUBRIC" in prefix
    # Only the scenario goes over stdin.
    assert "## SCORING RULES AND RUBRIC" not in first["input"]
    assert "alpha" in first["input"]
    # Windows caps a command line at 32K characters; the prefix alone is larger.
    assert len(" ".join(cmd)) < 32_000 < len(prefix) + len(" ".join(cmd))

    second = _launch(tmp_path, monkeypatch, _scenario("bravo"))
    again = second["cmd"][second["cmd"].index("--append-system-prompt-file") + 1]
    assert again == path
    assert open(again, encoding="utf-8").read() == prefix


# --- the re-judge harness ----------------------------------------------------


RECORDED = {
    "scenario_id": "alpha",
    "category": "rag_quality",
    "status": "PASS",
    "overall_score": 9.4,
    "turns": [
        {
            "turn": 1,
            "user_message": "What is in the file?",
            "agent_response": "It lists three facts.",
            "agent_tools": ["query_documents"],
            "scores": {"correctness": 10},
            "reasoning": "ORIGINAL-JUDGE-REASONING",
            "overall_score": 9.4,
        }
    ],
}


def test_the_transcript_never_shows_the_original_verdict():
    text = rejudge.transcript_section(RECORDED)
    assert "It lists three facts." in text and "query_documents" in text
    assert "ORIGINAL-JUDGE-REASONING" not in text
    assert "9.4" not in text


def test_both_layouts_carry_the_same_sections():
    legacy_sys, legacy_user = rejudge.build_judge_prompt(
        "legacy", _scenario("alpha"), RECORDED, MANIFEST
    )
    cached_sys, cached_user = rejudge.build_judge_prompt(
        "cached", _scenario("alpha"), RECORDED, MANIFEST
    )
    assert legacy_sys is None
    cached_all = f"{cached_sys}\n{cached_user}"
    for section in (
        "## SCORING RULES AND RUBRIC",
        "## SCENARIO\n",
        "## CORPUS MANIFEST",
        "## YOUR TASK",
        "### Turn 1\nUser:",
    ):
        assert legacy_user.count(section) == 1 == cached_all.count(section)
    # Only the scenario and its transcript are outside the shared prefix.
    assert "## SCENARIO\n" not in cached_sys
    assert "### Turn 1" not in cached_sys
    other_sys, _ = rejudge.build_judge_prompt(
        "cached", _scenario("bravo"), RECORDED | {"turns": []}, MANIFEST
    )
    assert other_sys == cached_sys


def test_parallel_judges_always_read_the_whole_prefix(tmp_path, monkeypatch):
    """The prefix is written before the pool starts, never by a racing call."""
    import yaml

    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    results = []
    for n in range(6):
        sid = f"s{n}"
        (scenarios / f"{sid}.yaml").write_text(
            yaml.dump(_scenario(sid)), encoding="utf-8"
        )
        results.append(RECORDED | {"scenario_id": sid, "category": f"c{n % 3}"})
    artifacts = tmp_path / "artifacts" / "lane" / "cat"
    artifacts.mkdir(parents=True)
    (artifacts / "scorecard.json").write_text(
        json.dumps({"scenarios": results}), encoding="utf-8"
    )
    monkeypatch.setattr(runner, "SCENARIOS_DIR", scenarios)
    monkeypatch.setattr(runner, "USER_SCENARIOS_DIR", tmp_path / "absent")
    monkeypatch.setattr(rejudge, "_load_merged_manifest", lambda: MANIFEST)

    seen = []

    def fake_claude(cmd, *, input, **_kwargs):  # pylint: disable=redefined-builtin
        prefix = open(
            cmd[cmd.index("--append-system-prompt-file") + 1], encoding="utf-8"
        ).read()
        sid = input.split("id: ", 1)[1].split()[0]
        seen.append(prefix)
        verdict = {
            "scenario_id": sid,
            "status": "PASS",
            "overall_score": 9,
            "turns": [{"turn": 1, "scores": {k: 9 for k in runner._SCORE_WEIGHTS}}],
        }
        payload = {"structured_output": verdict, "usage": {}, "num_turns": 1}
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(rejudge.subprocess, "run", fake_claude)
    out = tmp_path / "out"
    code = rejudge.main(
        [
            "--artifacts",
            str(tmp_path / "artifacts"),
            "--out",
            str(out),
            "--model",
            "m",
            "--sample",
            "6",
            "--arms",
            "cached",
            "--workers",
            "3",
        ]
    )
    assert code == 0
    expected, _ = rejudge.build_judge_prompt(
        "cached", _scenario("s0"), RECORDED, MANIFEST
    )
    assert len(seen) == 6 and all(prefix == expected for prefix in seen)


def test_a_judge_call_without_its_prefix_is_refused():
    with pytest.raises(ValueError, match="system prefix written first"):
        rejudge.judge_once("cached", _scenario("a"), RECORDED, MANIFEST, "m", None, 5)


def test_an_unknown_layout_is_refused():
    with pytest.raises(ValueError, match="unknown layout"):
        rejudge.build_judge_prompt("fancy", _scenario("a"), RECORDED, MANIFEST)


def test_the_sample_spreads_across_categories_and_is_repeatable():
    results = {
        f"{cat}_{n}": {"category": cat} for cat in ("a", "b", "c") for n in range(5)
    }
    sample = rejudge.pick_sample(results, 6, seed=0)
    assert sorted(results[s]["category"] for s in sample) == [
        "a",
        "a",
        "b",
        "b",
        "c",
        "c",
    ]
    assert sample == rejudge.pick_sample(results, 6, seed=0)
    assert len(rejudge.pick_sample(results, 99, seed=0)) == 15


def test_agreement_counts_flips_both_ways_and_score_shift():
    a = {
        "s1": {"status": "PASS", "overall_score": 8.0},
        "s2": {"status": "PASS", "overall_score": 7.0},
        "s3": {"status": "FAIL", "overall_score": 4.0},
        "s4": {"status": "FAIL", "overall_score": 3.0, "error": None},
    }
    b = {
        "s1": {"status": "PASS", "overall_score": 9.0},
        "s2": {"status": "FAIL", "overall_score": 5.0},
        "s3": {"status": "PASS", "overall_score": 6.5},
        "s5": {"status": "PASS", "overall_score": 9.0},
    }
    pair = rejudge._pair(a, b)
    assert pair["scenarios"] == 3
    assert pair["agree"] == 1
    assert (pair["pass_to_fail"], pair["fail_to_pass"]) == (1, 1)
    assert pair["mean_score_delta"] == pytest.approx((1.0 - 2.0 + 2.5) / 3, abs=1e-3)


def test_summary_sums_tokens_per_arm():
    calls = [
        {
            "arm": "1:legacy",
            "scenario_id": "s1",
            "status": "PASS",
            "overall_score": 8,
            "usage": {"cache_creation_input_tokens": 30, "calls": 2},
        },
        {
            "arm": "2:cached",
            "scenario_id": "s1",
            "status": "PASS",
            "overall_score": 8,
            "usage": {"cache_read_input_tokens": 25, "calls": 2},
        },
    ]
    report = rejudge.summarize(calls, ["legacy", "cached"])
    assert report["arms"]["1:legacy"]["usage"]["cache_creation_input_tokens"] == 30
    assert report["arms"]["2:cached"]["usage"]["cache_read_input_tokens"] == 25
    assert report["pairs"]["1:legacy vs 2:cached"]["agree"] == 1
