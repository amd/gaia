# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""util/run_eval_lane.py sets a local run up the way eval_flagship.yml does.

The two drifts worth a test are the harmful ones: a gaia_* lane with the real
gh on PATH while tool calls are auto-approved, and a backend started from the
checkout instead of the staged fixtures the scenarios name.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "util"))
_spec = importlib.util.spec_from_file_location(
    "run_eval_lane", REPO_ROOT / "util" / "run_eval_lane.py"
)
run_eval_lane = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval_lane)


def test_a_gaia_lane_runs_the_fake_gh_from_the_staged_fixtures(tmp_path):
    p = run_eval_lane.plan(["gaia_memory", "gaia_honesty"], tmp_path, 4200)

    assert p["env"]["GAIA_AUTO_APPROVE_TOOLS"] == "1"
    first_on_path = p["env"]["PATH"].split(os.pathsep)[0]
    assert first_on_path == str(tmp_path / ".gaia-eval-bin")
    assert p["cwd"] == tmp_path / "gaia-eval"
    assert p["extra_args"] == [
        "--exclude-tag",
        "live",
        "--exclude-tag",
        "local_blocked_win_shim",
    ]
    assert p["env"]["GAIA_MEMORY_DISABLED"] == "0"


def test_a_plain_lane_gets_no_auto_approval_and_no_memory(tmp_path):
    p = run_eval_lane.plan(["tool_selection"], tmp_path, 4200)

    assert "GAIA_AUTO_APPROVE_TOOLS" not in p["env"]
    assert "PATH" not in p["env"]
    assert p["cwd"] == REPO_ROOT
    assert p["env"]["GAIA_MEMORY_DISABLED"] == "1"
    assert not p["memory"]


def test_every_ci_lane_resolves():
    # Read the lanes from the file so splitting or renaming one can't strand this test.
    lanes = [
        entry["lane"]
        for entry in json.loads(run_eval_lane.LANES_FILE.read_text(encoding="utf-8"))[
            "lanes"
        ]
    ]
    assert lanes
    for lane in lanes:
        assert run_eval_lane.lane_categories(lane)


def test_an_unknown_lane_names_the_real_ones():
    with pytest.raises(SystemExit, match="gaia-signal"):
        run_eval_lane.lane_categories("no-such-lane")


def test_each_category_gets_an_empty_home_with_the_staged_skills(tmp_path):
    staged = tmp_path / "_staged"
    (staged / "skills" / "word-count").mkdir(parents=True)
    (staged / "skills" / "trusted-keys.json").write_text("{}")

    env = run_eval_lane.category_home(tmp_path / "gaia_memory", staged)

    home = tmp_path / "gaia_memory"
    assert env["GAIA_HOME"] == str(home)
    assert env["GAIA_MEMORY_DB"] == str(home / "memory.db")
    assert env["GAIA_CONFIG_DIR"] == str(home)
    assert env["GAIA_DAEMON_HOME"] == str(home / "host")
    assert (home / "skills" / "trusted-keys.json").is_file()
    assert not (home / "memory.db").exists()


def test_agent_model_is_the_default_model_the_backend_resolves(tmp_path):
    import subprocess

    from gaia.llm.lemonade_client import GPT_OSS_20B_MODEL_NAME

    env = run_eval_lane.category_home(tmp_path / "gaia_core", tmp_path / "_staged")
    run_eval_lane.write_agent_model(tmp_path / "gaia_core", GPT_OSS_20B_MODEL_NAME)

    resolved = subprocess.run(
        [
            sys.executable,
            "-c",
            "from gaia.llm.lemonade_client import resolve_default_chat_model as r;"
            "print(r())",
        ],
        env={
            **{k: v for k, v in os.environ.items() if k != "GAIA_CONFIG_FILE"},
            **env,
            "PYTHONPATH": str(REPO_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert resolved == GPT_OSS_20B_MODEL_NAME


def test_a_category_home_that_already_exists_is_refused(tmp_path):
    # A reused home is exactly how one run's memory reaches the next.
    (tmp_path / "gaia_rag").mkdir()

    with pytest.raises(SystemExit, match="already exists"):
        run_eval_lane.category_home(tmp_path / "gaia_rag", tmp_path / "_staged")


class _FakeSubprocess:
    """Stands in for ``subprocess`` in run_eval_lane: the eval writes a scorecard."""

    PIPE = STDOUT = None

    def __init__(self, run_dir, statuses):
        self.run_dir = run_dir
        self.statuses = statuses

    def run(self, cmd, **kwargs):
        if cmd[:3] == ["gaia", "eval", "agent"]:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            (self.run_dir / "scorecard.json").write_text(
                json.dumps(
                    {
                        "scenarios": [
                            {"scenario_id": f"s{i}", "status": status}
                            for i, status in enumerate(self.statuses)
                        ]
                    }
                )
            )
            kwargs["stdout"].write(f"Results: x\nOutput: {self.run_dir}\n")
        return type("Done", (), {"returncode": 0})()

    def Popen(self, *_args, **_kwargs):  # noqa: N802 - mirrors subprocess
        return type("Proc", (), {"pid": 1, "poll": lambda self: None})()


def _run_one_category(tmp_path, monkeypatch, statuses):
    category = "error_recovery"
    p = run_eval_lane.plan([category], tmp_path, 4200)
    homes = tmp_path / "out" / "gaia-homes"
    env = {
        **os.environ,
        **p["env"],
        **run_eval_lane.category_home(homes / category, homes / "_staged"),
    }
    (homes / category / "gaia_chat.db").write_text("chat history")
    health = iter([False, True])
    monkeypatch.setattr(run_eval_lane, "_get", lambda _url: next(health))
    monkeypatch.setattr(run_eval_lane, "_stop_tree", lambda _proc: None)
    monkeypatch.setattr(run_eval_lane, "_require_empty_memory", lambda *_a: None)
    monkeypatch.setattr(
        run_eval_lane,
        "subprocess",
        _FakeSubprocess(tmp_path / "results" / "eval-1", statuses),
    )
    args = type(
        "Args", (), {"port": 4200, "judge_model": "m", "out": tmp_path / "out"}
    )()
    run_eval_lane._run_category(category, env, p, args)
    return homes / category, tmp_path / "out" / "kept-homes" / category


def test_a_category_with_failures_keeps_its_gaia_home(tmp_path, monkeypatch):
    home, kept = _run_one_category(tmp_path, monkeypatch, ["PASS", "TIMEOUT"])

    assert not home.exists()
    assert (kept / "gaia_chat.db").read_text() == "chat history"


def test_a_category_that_passed_cleans_its_gaia_home_up(tmp_path, monkeypatch):
    home, kept = _run_one_category(
        tmp_path, monkeypatch, ["PASS", "SKIPPED_NO_DOCUMENT"]
    )

    assert not home.exists()
    assert not kept.exists()
