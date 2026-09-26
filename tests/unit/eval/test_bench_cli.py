# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia eval tasks`: the flags the nightly workflow will pass, and its startup errors.

The workflow is meant to be thin, so every choice it makes is a flag here and
every bad choice fails at startup with a message naming the fix.
"""

import shlex
import sys

import pytest

from gaia import cli
from gaia.eval import flagship_tasks as ft
from gaia.eval.bench import config as bench_config

EVAL_TASKS = ["eval", "tasks"]


def _parse(*args):
    return cli.build_parser().parse_args([*EVAL_TASKS, *args])


@pytest.fixture
def run(monkeypatch, tmp_path):
    """Call the CLI the way a user does: `gaia eval tasks ...`, via its argv."""

    def _run(*args):
        monkeypatch.setattr(sys, "argv", ["gaia", *EVAL_TASKS, *args])
        try:
            cli.main()
        except SystemExit as exit_code:
            return exit_code.code or 0
        return 0

    monkeypatch.setenv("GAIA_BENCH_WORK_ROOT", str(tmp_path / "work"))
    return _run


def test_the_parser_offers_every_action():
    args = _parse("run", "--suite", "everyday")
    assert (args.tasks_action, args.suite, args.harness, args.repeats) == (
        "run",
        "everyday",
        "gaia",
        1,
    )
    extra = {
        "judge": ["x"],
        "gate": ["x"],
        "report": ["x", "--out", "o"],
        "gateway": ["--port", "1"],
    }
    for action in ("judge", "gate", "report", "gateway", "controls"):
        assert _parse(action, *extra.get(action, [])).tasks_action == action


def test_the_nightly_matrix_flags_reach_the_configuration(monkeypatch, tmp_path, run):
    seen = {}

    def fake_run_suite(
        suite,
        model,
        out_dir,
        on_progress=None,
        tasks_file=None,
        config=None,
        repeat=1,
        only=None,
    ):
        seen.update(
            {
                "suite": suite,
                "model": model,
                "out": out_dir,
                "config": config,
                "repeat": repeat,
                "only": only,
            }
        )
        return {"suite": suite, "model": model, "tasks": [], "harness": config.harness}

    monkeypatch.setattr(ft, "run_suite", fake_run_suite)
    monkeypatch.setattr(ft, "render_report", lambda card, checks: "")
    code = run(
        "run",
        "--suite",
        "everyday",
        "--harness",
        "claude-code",
        "--model",
        "fireworks.glm-5p3-flash",
        "--repeats",
        "2",
        "--run-timeout",
        "1200",
        "--work-root",
        str(tmp_path / "work"),
        "--gateway-url",
        "http://127.0.0.1:8788",
        "--therock-url",
        "https://mirror.invalid/TheRock",
        "--tasks",
        "02-bugfix,b3-triage",
        "--no-judge",
        "--out",
        str(tmp_path / "out"),
    )
    assert code == 0
    config = seen["config"]
    assert (config.harness, config.run_timeout_s, config.repeats) == (
        "claude-code",
        1200,
        2,
    )
    assert config.work_root == tmp_path / "work"
    assert config.gateway_url == "http://127.0.0.1:8788"
    assert config.therock_url == "https://mirror.invalid/TheRock"
    assert seen["only"] == ["02-bugfix", "b3-triage"]
    assert seen["repeat"] == 2, "the last repeat is numbered"
    assert seen["out"] == tmp_path / "out" / "r2"


def test_repeats_write_one_directory_per_run(monkeypatch, tmp_path, run):
    written = []
    monkeypatch.setattr(
        ft,
        "run_suite",
        lambda suite, model, out_dir, **kw: written.append(out_dir)
        or {"suite": suite, "model": model, "tasks": []},
    )
    monkeypatch.setattr(ft, "render_report", lambda card, checks: "")
    run("run", "--repeats", "3", "--no-judge", "--out", str(tmp_path / "out"))
    assert written == [tmp_path / "out" / f"r{n}" for n in (1, 2, 3)]
    # One run keeps the plain directory CI already reads.
    written.clear()
    run("run", "--no-judge", "--out", str(tmp_path / "single"))
    assert written == [tmp_path / "single"]


@pytest.mark.parametrize(
    "args, message",
    [
        (["run", "--harness", "claude-code"], "needs --model"),
        (["run", "--meter", "fireworks"], "FIREWORKS_ACCOUNT_ID"),
        (["run", "--run-timeout", "0"], "at least 1"),
        (["run", "--tasks", "nope"], "which the suite does not have"),
    ],
)
def test_a_bad_run_fails_at_startup_naming_the_fix(
    args, message, monkeypatch, capsys, run
):
    monkeypatch.delenv(bench_config.ENV_FIREWORKS_ACCOUNT, raising=False)
    monkeypatch.setattr(
        ft,
        "run_suite",
        lambda *a, **k: pytest.fail("the run started despite bad settings"),
    )
    assert run(*args) == 2
    assert message in capsys.readouterr().out


def test_an_unknown_harness_is_refused_by_the_parser(capsys):
    with pytest.raises(SystemExit):
        _parse("run", "--harness", "cursor")
    assert "invalid choice" in capsys.readouterr().err


def test_judge_grades_every_repeat_of_a_run(monkeypatch, tmp_path, capsys, run):
    for repeat in (1, 2):
        run_dir = tmp_path / "out" / f"r{repeat}"
        run_dir.mkdir(parents=True)
        ft.write_scorecard(run_dir, {"suite": "core", "model": "m", "tasks": []})
    judged = []
    monkeypatch.setattr(
        ft,
        "judge_run",
        lambda run_dir, *a, **kw: judged.append(run_dir)
        or {"suite": "core", "model": "m", "tasks": []},
    )
    assert run("judge", str(tmp_path / "out")) == 0
    assert judged == [tmp_path / "out" / "r1", tmp_path / "out" / "r2"]


def test_report_writes_the_table_and_names_its_files(
    monkeypatch, tmp_path, capsys, run
):
    from gaia.eval.bench import report as bench_report

    monkeypatch.setattr(bench_report, "find_chrome", lambda: None)
    finished = tmp_path / "finished"
    finished.mkdir()
    ft.write_scorecard(
        finished,
        {
            "suite": "everyday",
            "model": "m",
            "harness": "gaia",
            "cost": {"source": "metered", "usd": 0.1, "tokens": 10},
            "tasks": [
                {
                    "id": "t",
                    "check": "mechanical",
                    "passed": True,
                    "verified": True,
                    "why": "",
                    "error": "",
                    "steps": 1,
                    "tool_calls": 1,
                    "input_tokens": 5,
                    "output_tokens": 5,
                    "wall_seconds": 1.0,
                    "judge": {},
                    "web_uses": [],
                }
            ],
        },
    )
    assert run("report", str(finished), "--out", str(tmp_path / "report")) == 0
    out = capsys.readouterr().out
    assert "[MARKDOWN]" in out and "[HTML]" in out
    assert (tmp_path / "report" / "report.md").is_file()


def test_controls_fail_the_command_when_the_judge_cannot_separate_them(
    monkeypatch, capsys, run
):
    from gaia.eval.bench import controls

    monkeypatch.setattr(
        controls,
        "run_controls",
        lambda model, env, out_dir=None: {
            "task": "02-bugfix",
            "judge_model": model,
            "controls": [
                {
                    "variant": "ideal",
                    "passed": True,
                    "why": "",
                    "grade": {},
                    "expected": "x",
                    "ok": False,
                }
            ],
            "ok": False,
        },
    )
    assert run("controls") == 1
    assert "not trustworthy" in capsys.readouterr().out


def test_the_gateway_command_serves_the_port_it_is_given(monkeypatch, capsys, run):
    from gaia.eval.bench import gateway as gw

    served = {}

    class FakeGateway:
        def __init__(self, upstream, api_key, port=0):
            served.update({"upstream": upstream, "key": api_key, "port": port})
            self.url, self.upstream = f"http://127.0.0.1:{port}", upstream

        def serve_forever(self):
            served["served"] = True

        def stop(self):
            served["stopped"] = True

    monkeypatch.setattr(gw, "Gateway", FakeGateway)
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://localhost:50908/api/v1")
    monkeypatch.setenv("LEMONADE_API_KEY", "upstream-key-value-123")
    assert run("gateway", "--port", "8788") == 0
    assert served["port"] == 8788 and served["served"] and served["stopped"]
    assert served["key"] == "upstream-key-value-123"
    # The key is never printed.
    assert "upstream-key-value-123" not in capsys.readouterr().out


def test_the_documented_examples_parse():
    """Every example in the help, and in the CLI reference, really runs."""
    parser = cli.build_parser()
    docs = ft.REPO_ROOT / "docs" / "reference"
    text = "\n".join(
        [
            _tasks_parser(parser).epilog or "",
            (docs / "cli.mdx").read_text(encoding="utf-8"),
            (docs / "eval.mdx").read_text(encoding="utf-8"),
        ]
    )
    examples = sorted(
        {
            line.strip().split("#")[0].strip()
            for line in text.splitlines()
            if line.strip().startswith("gaia eval tasks")
        }
    )
    assert len(examples) >= 8
    for example in examples:
        parser.parse_args(shlex.split(example)[1:])


def _tasks_parser(parser):
    def subparsers(p):
        return next(
            (a for a in p._subparsers._group_actions if hasattr(a, "choices")), None
        )

    return subparsers(subparsers(parser).choices["eval"]).choices["tasks"]


def test_progress_is_line_buffered_so_a_redirected_run_shows_its_work(
    monkeypatch, tmp_path, run
):
    """A suite runs for a quarter of an hour; block buffering hid every line."""
    reconfigured = {}

    class Recording:
        def reconfigure(self, **kwargs):
            reconfigured.update(kwargs)

        def write(self, text):
            return len(text)

        def flush(self):
            pass

    monkeypatch.setattr(
        ft, "run_suite", lambda *a, **k: {"suite": "core", "model": "m", "tasks": []}
    )
    monkeypatch.setattr(ft, "render_report", lambda card, checks: "")
    monkeypatch.setattr(sys, "stdout", Recording())
    run("run", "--no-judge", "--out", str(tmp_path / "out"))
    assert reconfigured == {"line_buffering": True}
