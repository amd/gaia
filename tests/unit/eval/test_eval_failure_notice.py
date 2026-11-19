# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/eval_failure_notice.py: an eval issue only for a measured regression.

The fixtures are trimmed from real runs' public artifacts:

* ``baseline_qwen`` + ``regressed_run``: the gaia_memory scorecards of two
  flagship dispatches (41588974379 and 41588978917) of the same Qwen model,
  where two scenarios went PASS -> FAIL and one FAIL -> PASS.
* ``rc3_smoke``: v0.25.4-rc3's smoke lane (run 41353279225, which passed).
* ``rc1_smoke``: v0.25.4-rc1's lanes (run 41148504198), which uploaded only
  their lane-status files: nothing was measured.
* ``agent_v033``: agent-pkg-gaia-v0.3.3's release eval (run 40991059672),
  stopped part-way by an exhausted Claude quota.
"""

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "util"))

import eval_failure_notice as notice_mod  # noqa: E402

FIX = REPO_ROOT / "tests" / "fixtures" / "eval_failure_notice"
QWEN_EMPTY_BASELINE = (
    REPO_ROOT / "tests" / "fixtures" / "eval_baselines" / ("gaia-flagship-qwen")
)
AGENT_CATEGORIES = (
    "gaia_core,gaia_shell,gaia_tool_selection,gaia_honesty,gaia_skills_capture"
)


def matrix(**lanes):
    return json.dumps(
        {"include": [{"lane": k, "categories": v} for k, v in lanes.items()]}
    )


def lane_status_matrix(root: Path) -> str:
    """The matrix a smoke run used, rebuilt from its lane-status files."""
    include = []
    for status in sorted(root.glob("eval-*/lane-status.json")):
        data = json.loads(status.read_text(encoding="utf-8"))
        cats = [pair.split("=")[0] for pair in data["scenarios"].split()]
        include.append({"lane": data["lane"], "categories": " ".join(cats)})
    return json.dumps({"include": include})


def run(tmp_path, *extra, jobs=("lanes=success", "scenarios=success"), tier="smoke"):
    out = tmp_path / "out"
    gh_out = tmp_path / "github_output"
    summary = tmp_path / "summary"
    argv = [
        "--tier",
        tier,
        "--ref",
        "v0.26.0-rc1",
        "--run-url",
        "https://github.com/amd/gaia/actions/runs/1",
        "--out-dir",
        str(out),
        "--github-output",
        str(gh_out),
        "--step-summary",
        str(summary),
        *extra,
    ]
    for job in jobs:
        argv += ["--job", job]
    code = notice_mod.main(argv)
    outputs = {}
    if gh_out.exists():
        for line in gh_out.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
    read = lambda name: (  # noqa: E731
        (out / name).read_text(encoding="utf-8") if (out / name).exists() else None
    )
    return code, outputs, read("body.md"), read("summary.md")


def flagship(*extra):
    return ("--target", "flagship", *extra)


def regressed_args(
    baseline=FIX / "baseline_qwen",
    artifacts=FIX / "regressed_run",
    stable=FIX / "stable_set_memory.json",
):
    return flagship(
        "--artifacts-dir",
        str(artifacts),
        "--baseline-dir",
        str(baseline),
        "--matrix-json",
        matrix(**{"gaia-signal": "gaia_memory"}),
        "--stable-scenarios",
        str(stable),
    )


# --- a measured regression files an issue that names it -----------------------


def test_a_pass_to_fail_files_an_issue_naming_each_scenario(tmp_path):
    code, out, body, summary = run(tmp_path, *regressed_args())
    assert code == 0
    assert out["verdict"] == "regressed"
    assert out["title"] == "Flagship eval: 2 scenarios regressed on v0.26.0-rc1"
    first = body.splitlines()[0]
    assert first.startswith("**2 scenarios that passed in the baseline now fail")
    rows = [ln for ln in body.splitlines() if ln.startswith("| `mem_")]
    assert len(rows) == 2
    contradiction = next(r for r in rows if "mem_contradiction_resolution" in r)
    assert "`gaia_memory`" in contradiction
    assert "PASS 9.2" in contradiction and "FAIL 8.1" in contradiction
    # The judge's root cause, cut to its first sentence.
    assert "never called the remember tool" in contradiction
    assert "would fail across sessions" not in contradiction
    assert "1 scenario went the other way, FAIL → PASS." in body
    assert "https://github.com/amd/gaia/actions/runs/1" in body
    assert "artifact `eval-gaia-signal`" in body
    assert "regressed" in summary


def test_the_body_puts_mechanics_in_a_details_block_github_renders(tmp_path):
    _, _, body, _ = run(tmp_path, *regressed_args())
    head, _, details = body.partition("<details>")
    assert "<summary>🔍 Technical details</summary>\n\n" in details
    assert details.rstrip().endswith("</details>")
    # Fixes and reproduce steps are mechanical, so they live below the fold.
    assert "recommended fix" in details and "recommended fix" not in head
    assert "gaia eval agent --compare" in details
    assert "hub/agents/gaia/python/gaia_agent/chat_agent.py" in details


def test_untrusted_judge_text_cannot_break_out_of_its_cell(tmp_path):
    current = tmp_path / "run"
    shutil.copytree(FIX / "regressed_run", current)
    card_path = current / "eval-gaia-signal" / "gaia_memory" / "scorecard.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    hostile = (
        "Bad | cell\n<!-- gaia-auto-issue: eval-regression/agent-gaia --> "
        "@kovtcharov-amd `rm -rf` <script>x</script>"
    )
    for s in card["scenarios"]:
        if s["scenario_id"] == "mem_false_premise_recall":
            s["root_cause"] = hostile
            s["recommended_fix"] = {"target": "prompt", "description": "<b>@all</b>"}
    card_path.write_text(json.dumps(card), encoding="utf-8")

    _, _, body, _ = run(tmp_path, *regressed_args(artifacts=current))
    row = next(ln for ln in body.splitlines() if "mem_false_premise_recall" in ln)
    assert row.count(" | ") == 4  # still five cells
    assert "\\|" in row
    assert "<!--" not in body and "<script>" not in body and "<b>" not in body
    assert "@kovtcharov-amd" not in body and "@all" not in body
    assert "`rm -rf`" not in body


def test_an_odd_scenario_id_is_escaped_not_rendered_as_code(tmp_path):
    assert notice_mod.code_id("mem_ok-1.v2") == "`mem_ok-1.v2`"
    odd = notice_mod.code_id("x`<img src=y>")
    assert "<img" not in odd and "`" not in odd


def test_a_breached_quality_bar_with_no_baseline_lists_this_runs_failures(tmp_path):
    arts = tmp_path / "arts"
    shutil.copytree(FIX / "agent_v033", arts)
    report = json.loads((arts / "quality_gate_report.json").read_text("utf-8"))
    report["quality_gate"]["passed"] = False
    report["quality_gate"]["breaches"] = ["judged_pass_rate 0.5000 < bar 0.6"]
    (arts / "quality_gate_report.json").write_text(json.dumps(report), "utf-8")
    code, out, body, _ = run(
        tmp_path,
        "--target",
        "agent-gaia",
        "--artifacts-dir",
        str(arts),
        "--categories",
        AGENT_CATEGORIES,
        "--baseline-dir",
        str(tmp_path / "no-baseline"),
        "--quality-report",
        str(arts / "quality_gate_report.json"),
        jobs=("scenarios=failure",),
        tier="release",
    )
    assert code == 0 and out["verdict"] == "regressed"
    assert out["title"] == "Agent release eval: quality bar breached on v0.26.0-rc1"
    assert body.startswith("**The eval on `v0.26.0-rc1` fell below its quality bar**")
    assert "judged_pass_rate 0.5000 &lt; bar 0.6" in body
    failing = {"core_contradiction_injection", "shell_list_dir_basic"}
    for sid in failing:
        row = next(ln for ln in body.splitlines() if sid in ln)
        assert "| — |" in row  # no baseline to show
    # What the run could not measure is still reported, under the fold.
    details = body.partition("<details>")[2]
    assert "Claude quota exhausted" in details
    assert "`gaia_honesty` produced no scorecard" in details


# --- a run that measured nothing files nothing, and says why -------------------


def test_rc1_with_no_scorecards_files_nothing_and_names_what_was_missing(tmp_path):
    root = FIX / "rc1_smoke"
    code, out, body, summary = run(
        tmp_path,
        *flagship(
            "--artifacts-dir",
            str(root),
            "--baseline-dir",
            str(QWEN_EMPTY_BASELINE),
            "--matrix-json",
            lane_status_matrix(root),
        ),
        jobs=("lanes=success", "scenarios=failure"),
    )
    assert code == 0
    assert out == {"verdict": "no-verdict"}
    assert body is None
    assert "No verdict: nothing filed, nothing closed" in summary
    assert "The scenario eval job ended `failure`" in summary
    assert "`gaia_honesty` produced no scorecard (artifact `eval-smoke-1`)" in summary
    assert "`gaia_memory` produced no scorecard (artifact `eval-smoke-2`)" in summary


def test_a_scorecard_with_no_baseline_for_its_model_is_no_verdict(tmp_path):
    # rc3 really ran on Qwen, whose baseline directory holds no scorecards yet.
    _, out, body, summary = run(
        tmp_path,
        *flagship(
            "--artifacts-dir",
            str(FIX / "rc3_smoke"),
            "--baseline-dir",
            str(QWEN_EMPTY_BASELINE),
            "--matrix-json",
            matrix(**{"smoke-1": "gaia_honesty"}),
        ),
    )
    assert out["verdict"] == "no-verdict" and body is None
    assert "No baseline is committed at" in summary
    assert "a clean run here is not a pass" in summary


def test_skipped_or_cancelled_lanes_are_no_verdict(tmp_path):
    for result, words in (("skipped", "was skipped"), ("cancelled", "was cancelled")):
        _, out, _, summary = run(
            tmp_path / result,
            *flagship("--matrix-json", '{"include": []}'),
            jobs=("lanes=success", f"scenarios={result}"),
        )
        assert out["verdict"] == "no-verdict"
        assert words in summary


def test_agent_v033_stopped_by_quota_files_nothing(tmp_path):
    # It failed only because Claude ran out; its quality bar passed.
    _, out, body, summary = run(
        tmp_path,
        "--target",
        "agent-gaia",
        "--artifacts-dir",
        str(FIX / "agent_v033"),
        "--categories",
        AGENT_CATEGORIES,
        "--baseline-dir",
        str(tmp_path / "no-baseline"),
        "--quality-report",
        str(FIX / "agent_v033" / "quality_gate_report.json"),
        jobs=("scenarios=failure",),
        tier="release",
    )
    assert out["verdict"] == "no-verdict" and body is None
    assert "`gaia_tool_selection` stopped part-way (Claude quota exhausted)" in summary
    assert "`gaia_skills_capture` produced no scorecard" in summary
    assert (
        "`gaia_core`: 1 scenario(s) produced no score (`core_arithmetic_direct`)"
        in (summary)
    )


def test_a_final_tag_never_files_or_closes(tmp_path):
    for verify, words in (
        ("failure", "No full sweep ran on this commit"),
        ("success", "A final tag re-measures nothing"),
    ):
        _, out, body, summary = run(
            tmp_path / verify,
            *flagship(),
            jobs=(f"verify={verify}", "lanes=skipped", "scenarios=skipped"),
            tier="verify",
        )
        assert out["verdict"] == "no-verdict" and body is None
        assert words in summary
        assert "lane matrix" not in summary  # a final tag runs no lanes, by design


def test_a_baseline_from_another_eval_model_is_not_compared(tmp_path):
    base = tmp_path / "base"
    shutil.copytree(FIX / "baseline_qwen", base)
    meta = json.loads((base / "meta.json").read_text(encoding="utf-8"))
    meta["eval_model"] = "claude-opus-5"
    (base / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    _, out, body, summary = run(tmp_path, *regressed_args(baseline=base))
    assert out["verdict"] == "no-verdict" and body is None
    assert "`gaia_memory` was not compared: Not comparable with the baseline" in (
        summary
    )


def test_a_failed_outcomes_gate_is_reported_not_adjudicated(tmp_path):
    _, out, _, summary = run(
        tmp_path,
        *flagship(
            "--artifacts-dir",
            str(FIX / "rc3_smoke"),
            "--baseline-dir",
            str(FIX / "baseline_qwen"),
            "--matrix-json",
            matrix(**{"smoke-1": "gaia_honesty"}),
            "--outcomes-failed",
            "everyday (exit 1)",
        ),
        jobs=("lanes=success", "scenarios=success", "outcomes=failure"),
        tier="full",
    )
    assert out["verdict"] == "no-verdict"
    assert "outcome-task gate failed for: everyday (exit 1)" in summary
    assert "job ended `failure`" not in summary  # said once, specifically


# --- a complete, clean run closes the issue ------------------------------------


def test_a_clean_complete_comparison_passes(tmp_path):
    # rc3's honesty scorecard against itself as the baseline: no change.
    _, out, body, summary = run(
        tmp_path,
        *flagship(
            "--artifacts-dir",
            str(FIX / "rc3_smoke"),
            "--baseline-dir",
            str(FIX / "baseline_qwen"),
            "--matrix-json",
            matrix(**{"smoke-1": "gaia_honesty"}),
        ),
    )
    assert out == {"verdict": "passed"}
    assert body is None
    assert "**No regression.** 3 scenario(s) in 1 categories" in summary


def test_a_clean_comparison_with_a_missing_lane_does_not_pass(tmp_path):
    _, out, _, summary = run(
        tmp_path,
        *flagship(
            "--artifacts-dir",
            str(FIX / "rc3_smoke"),
            "--baseline-dir",
            str(FIX / "baseline_qwen"),
            "--matrix-json",
            matrix(**{"smoke-1": "gaia_honesty", "smoke-2": "gaia_memory"}),
        ),
    )
    assert out["verdict"] == "no-verdict"
    assert "`gaia_memory` produced no scorecard (artifact `eval-smoke-2`)" in summary


def test_a_baseline_scenario_missing_from_a_full_run_blocks_a_pass(tmp_path):
    arts = tmp_path / "arts"
    shutil.copytree(FIX / "rc3_smoke", arts)
    path = arts / "eval-smoke-1" / "gaia_honesty" / "scorecard.json"
    card = json.loads(path.read_text(encoding="utf-8"))
    card["config"].pop("scenario_filter")  # a full run, not a targeted one
    card["scenarios"] = card["scenarios"][:2]
    path.write_text(json.dumps(card), encoding="utf-8")
    _, out, _, summary = run(
        tmp_path,
        *flagship(
            "--artifacts-dir",
            str(arts),
            "--baseline-dir",
            str(FIX / "baseline_qwen"),
            "--matrix-json",
            matrix(**{"smoke-1": "gaia_honesty"}),
        ),
    )
    assert out["verdict"] == "no-verdict"
    assert "1 baseline scenario(s) are missing from this run" in summary


# --- inputs ---------------------------------------------------------------------


def test_an_unknown_tier_fails_loudly(tmp_path, capsys):
    code, out, _, _ = run(tmp_path, *flagship(), tier="backlog")
    assert code == 1 and out == {}
    assert "no rule for tier 'backlog'" in capsys.readouterr().out


def test_a_malformed_matrix_fails_loudly(tmp_path, capsys):
    code, out, _, _ = run(tmp_path, *flagship("--matrix-json", "not json"))
    assert code == 1 and out == {}
    assert "is not the lanes job's matrix" in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["lanes", "=success"])
def test_a_job_without_a_result_is_refused(bad):
    with pytest.raises(SystemExit):
        notice_mod.main(
            ["--target", "flagship", "--tier", "smoke", "--ref", "r"]
            + ["--run-url", "u", "--out-dir", "o", "--job", bad]
        )


def test_a_breach_alongside_pass_to_fail_rows_is_named_in_the_details(tmp_path):
    report = tmp_path / "quality_gate_report.json"
    report.write_text(
        json.dumps({"quality_gate": {"breaches": ["avg_score 4.90 < bar 5.0"]}}),
        encoding="utf-8",
    )
    _, out, body, _ = run(tmp_path, *regressed_args(), "--quality-report", str(report))
    assert out["title"] == "Flagship eval: 2 scenarios regressed on v0.26.0-rc1"
    details = body.partition("<details>")[2]
    assert "Quality bar also breached: avg_score 4.90 &lt; bar 5.0" in details


# --- noise guard: a lone flip outside the stable set is "watch", not an issue ---


def test_a_lone_flip_outside_the_stable_set_is_watch_not_an_issue(tmp_path):
    # The real stable set (eval/ci_tiers.json smoke) holds neither flipped
    # scenario: one run against a one-run baseline flips ~18% (#5493).
    _, out, body, summary = run(
        tmp_path, *regressed_args(stable=REPO_ROOT / "eval" / "ci_tiers.json")
    )
    assert out == {"verdict": "passed"}
    assert body is None
    assert "**Watch:** 2 scenario(s) went PASS → FAIL outside the stable" in summary
    assert "- `mem_contradiction_resolution` (`gaia_memory`): PASS 9.2 → FAIL 8.1" in (
        summary
    )


def test_only_the_stable_flip_is_filed_and_the_rest_is_watched(tmp_path):
    stable = tmp_path / "tiers.json"
    stable.write_text(
        json.dumps(
            {
                "tiers": {
                    "smoke": {
                        "machines": [
                            {
                                "machine": "m",
                                "scenarios": [{"id": "mem_false_premise_recall"}],
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    _, out, body, summary = run(tmp_path, *regressed_args(stable=stable))
    assert out["verdict"] == "regressed"
    assert out["title"] == "Flagship eval: 1 scenario regressed on v0.26.0-rc1"
    head, _, details = body.partition("<details>")
    assert "mem_false_premise_recall" in head
    assert "mem_contradiction_resolution" not in head
    assert "outside the stable set (watch, not filed)" in details
    assert "mem_contradiction_resolution" in details
    assert "**Watch:** 1 scenario(s)" in summary


def test_a_missing_stable_set_fails_loudly(tmp_path, capsys):
    code, out, _, _ = run(tmp_path, *regressed_args(stable=tmp_path / "nope.json"))
    assert code == 1 and out == {}
    assert "stable-scenario file" in capsys.readouterr().out


@pytest.mark.parametrize(
    "hostile, gone",
    [
        ("see [the fix](https://evil.example/x) now", ["[the fix]", "https://"]),
        ("![pixel](http://evil.example/p.png)", ["![", "http://"]),
        ("plain https://evil.example/a and www.evil.example", ["https://", "www."]),
        ("closes #123 and amd/gaia#45 and GH-7", ["#123", "#45", "GH-7"]),
        ("[ref]: https://evil.example", ["[ref]", "https://"]),
    ],
)
def test_judge_text_cannot_plant_links_images_or_issue_refs(hostile, gone):
    out = notice_mod.clean(hostile, 400)
    for fragment in gone:
        assert fragment not in out, (fragment, out)
    # Still readable: the words survive.
    assert "evil" in out or "123" in out


def test_neutralised_brackets_render_as_brackets_not_broken_entities():
    out = notice_mod.clean("a [b] c", 100)
    assert out == "a &#91;b&#93; c"


@pytest.mark.parametrize(
    "hostile",
    [
        "\\[x\\]",
        "\\[a\\](https://evil.example)",
        "\\\\[x\\\\]",
        "\\\\\\[x]",
        "[x\\]",
        "\\#91 and \\@someone",
        "cell \\| break",
    ],
)
def test_a_judge_backslash_cannot_turn_an_escape_into_a_live_issue_link(hostile):
    # Markdown reads `\&#91;` as an escaped `&`, leaving a literal `#91`
    # that GitHub links as an issue. Each entity must follow an even run of
    # backslashes, so the backslashes escape each other and the entity renders.
    out = notice_mod.clean(hostile, 400)
    for match in re.finditer(r"(\\*)&#9[13];", out):
        assert len(match.group(1)) % 2 == 0, out
    for match in re.finditer(r"(\\*)\|", out):
        assert len(match.group(1)) % 2 == 1, out  # the pipe stays escaped
    assert "://" not in out


def test_a_judge_backslash_bracket_renders_as_typed():
    out = notice_mod.clean("\\[a\\]", 100)
    assert out == "\\\\&#91;a\\\\&#93;"
