# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One toybox task, end to end, through a real harness and a real model.

Opt-in: it needs a running Lemonade (``require_lemonade``), and for the Claude
Code leg the ``claude`` CLI and its login. What it proves is what no mocked
test can — that the call the harness makes is one the backend accepts, and
that a real agent's record scores and judges.

    GAIA_BENCH_E2E=1 pytest tests/integration/eval/test_bench_e2e.py

``GAIA_BENCH_E2E_MODEL`` picks the model (default: the flagship's default).
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from gaia.eval import flagship_tasks as ft
from gaia.eval.bench import config as bench_config
from gaia.eval.bench import harness, leaks
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

pytestmark = pytest.mark.skipif(
    os.environ.get("GAIA_BENCH_E2E") != "1",
    reason="set GAIA_BENCH_E2E=1 to run one task against a real model",
)

TASK = "b3-duplicate"  # a question, over the gh stand-in: no model edits needed
TIMEOUT_S = int(os.environ.get("GAIA_BENCH_E2E_TIMEOUT", "900"))


@pytest.fixture
def one_task(tmp_path):
    source = json.loads(ft.TASKS_FILE.read_text(encoding="utf-8"))
    source["suites"]["e2e"] = [TASK]
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(source))
    return path


def _config(tmp_path, harness_name):
    return bench_config.resolve(
        harness=harness_name,
        run_timeout=TIMEOUT_S,
        work_root=str(tmp_path / "work"),
    )


def _check(card, out_dir: Path):
    (task,) = card["tasks"]
    assert (
        task["error_kind"] != "unavailable"
    ), f"the model backend was not there: {task['error']}"
    assert not task["error"], task["error"]
    assert task["steps"] >= 1 and task["tool_calls"] >= 1, "the agent did no work"
    assert task["input_tokens"] > 0, "no tokens were counted for the run"
    transcript = json.loads((out_dir / TASK / "transcript.json").read_text())
    assert transcript["answer"].strip(), "the agent answered nothing"
    # It had to reach the issues, and only through the stand-in.
    assert task["gh_calls"] >= 1 and task["gh_blocked_writes"] == 0
    body = "".join(p.read_text() for p in out_dir.rglob("*") if p.is_file())
    assert leaks.Scrubber.from_environment().leaks(body) == []
    return transcript


def test_gaia_runs_and_scores_one_real_task(require_lemonade, tmp_path, one_task):
    out_dir = tmp_path / "gaia"
    card = ft.run_suite(
        "e2e",
        os.environ.get("GAIA_BENCH_E2E_MODEL") or DEFAULT_MODEL_NAME,
        out_dir,
        tasks_file=one_task,
        config=_config(tmp_path, harness.GAIA),
    )
    transcript = _check(card, out_dir)
    assert transcript["conversation"], "GAIA's own record is missing"
    assert card["harness"] == harness.GAIA and card["revision"]


@pytest.mark.skipif(not shutil.which("claude"), reason="needs the Claude Code CLI")
def test_claude_code_runs_and_scores_the_same_task(tmp_path, one_task):
    """Anthropic's own model: no gateway, so this leg needs no Lemonade."""
    model = os.environ.get("GAIA_BENCH_E2E_CC_MODEL", "claude-sonnet-5")
    out_dir = tmp_path / "cc"
    card = ft.run_suite(
        "e2e",
        model,
        out_dir,
        tasks_file=one_task,
        config=_config(tmp_path, harness.CLAUDE_CODE),
    )
    transcript = _check(card, out_dir)
    assert transcript["events"], "Claude Code's stream was not recorded"
    assert card["cost"]["source"] == "api_equivalent"
