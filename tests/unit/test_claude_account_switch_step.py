# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The account switch must survive the primary probe being out of quota.

Actions runs ``run:`` blocks with ``bash -eo pipefail``. The classifier exits 2
for a quota rejection, which is exactly when the switch is needed, so the
classify step has to record that code instead of dying on it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "claude-account-switch.yml"
)


def _classify_step_script() -> str:
    jobs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    for job in jobs.values():
        for step in job["steps"]:
            if step.get("id") == "primary_verdict":
                return step["run"]
    raise AssertionError("no primary_verdict step in claude-account-switch.yml")


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="runs the step under a POSIX bash, as Actions does",
)
def test_a_quota_verdict_is_recorded_not_fatal(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "echo '::error::QUOTA EXHAUSTED (402/429-class): weekly limit · resets Oct 14 | WHAT TO DO: wait'\n"
        "exit 2\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_python.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_text(_classify_step_script(), encoding="utf-8", newline="\n")
    log = tmp_path / "probe.jsonl"
    log.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "github_output"
    output.write_text("", encoding="utf-8")

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "CLAUDE_CONCLUSION": "failure",
        "CLAUDE_LOG": str(log),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    recorded = output.read_text(encoding="utf-8")
    assert "code=2" in recorded
    assert "QUOTA EXHAUSTED" in recorded
