# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Benchmark configuration: flags, then environment, then defaults; no machine paths."""

import sys
import tempfile
from pathlib import Path

import pytest

from gaia.eval.bench import config as cfg
from gaia.eval.bench import sandbox


def test_defaults_work_on_any_host():
    config = cfg.resolve(environ={})
    assert config.harness == "gaia"
    assert config.run_timeout_s == cfg.DEFAULT_RUN_TIMEOUT_S
    assert config.work_root == Path(tempfile.gettempdir()) / "gaia-bench"
    assert config.therock_url == "https://github.com/ROCm/TheRock"
    assert config.gateway_url is None and config.meter is None
    assert cfg.results_root({}) == Path("eval/results")


def test_flags_win_over_the_environment():
    env = {
        cfg.ENV_WORK_ROOT: "/env/work",
        cfg.ENV_RUN_TIMEOUT: "900",
        cfg.ENV_THEROCK_URL: "https://mirror.example/TheRock",
        cfg.ENV_GATEWAY_URL: "http://127.0.0.1:9000",
    }
    from_env = cfg.resolve(environ=env)
    assert from_env.work_root == Path("/env/work") and from_env.run_timeout_s == 900
    assert from_env.therock_url == "https://mirror.example/TheRock"
    assert from_env.gateway_url == "http://127.0.0.1:9000"
    flags = cfg.resolve(environ=env, work_root="/flag", run_timeout=60, therock_url="x")
    assert (flags.work_root, flags.run_timeout_s, flags.therock_url) == (
        Path("/flag"),
        60,
        "x",
    )


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"harness": "cursor"}, "Unknown harness"),
        ({"meter": "openai"}, "Unknown meter"),
        ({"meter": "fireworks"}, cfg.ENV_FIREWORKS_ACCOUNT),
        ({"run_timeout": 0}, "at least 1"),
        ({"repeats": "three"}, "integer"),
    ],
)
def test_bad_or_missing_settings_fail_at_startup_naming_the_fix(kwargs, message):
    with pytest.raises(cfg.BenchConfigError, match=message):
        cfg.resolve(environ={}, **kwargs)


def test_the_fence_is_refused_where_it_cannot_be_enforced(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(cfg.BenchConfigError, match="macOS sandbox-exec"):
        cfg.resolve(environ={}, fence=True)
    with pytest.raises(sandbox.FenceUnavailable):
        sandbox.wrap(["true"], [Path("/answers")])


def test_the_fence_names_file_read_data_in_every_allow_rule(tmp_path):
    text = sandbox.profile(
        fenced=[tmp_path / "answers"],
        read_write=[tmp_path / "answers" / "task"],
        read_only=[tmp_path / "tools"],
    )
    lines = text.splitlines()
    assert lines[:2] == ["(version 1)", "(allow default)"]
    deny = [line for line in lines if line.startswith("(deny")]
    allow = [line for line in lines if line.startswith("(allow file")]
    assert deny and all("file-read-data" in line for line in deny)
    # Without file-read-data named, the deny wins and the agent cannot read its workdir.
    assert allow and all("file-read-data" in line for line in allow)
    assert lines.index(deny[0]) < lines.index(
        allow[0]
    ), "a later rule must reopen the task"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox-exec")
def test_the_fence_really_blocks_answer_keys_and_leaves_the_workdir_open(tmp_path):
    import subprocess

    answers = tmp_path / "answers"
    task = answers / "task"
    task.mkdir(parents=True)
    (answers / "key.txt").write_text("the answer")
    (task / "work.txt").write_text("mine")
    cmd = sandbox.wrap(
        ["/bin/sh", "-c", f"cat {task / 'work.txt'}; cat {answers / 'key.txt'}"],
        fenced=[answers],
        read_write=[task],
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert "mine" in proc.stdout
    assert "the answer" not in proc.stdout
