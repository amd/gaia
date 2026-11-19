# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every flagship relay test must run in CI, not skip for a missing package.

The relay step installs the flagship before running these files. A wildcard
keeps a new tests/unit/test_relay_*.py from silently skipping, and the
live-flagship UI file is named explicitly.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test_unit.yml"
STEP = "Run flagship relay tests"
GLOB = "tests/unit/test_relay_*.py"
UI_LIVE = "tests/unit/chat/ui/test_relayed_internet_switch.py"
PLATFORMS = {"unit-tests", "unit-tests-macos", "unit-tests-windows"}


def _relay_steps() -> list[tuple[str, dict]]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return [
        (job_id, step)
        for job_id, job in data["jobs"].items()
        for step in job.get("steps", [])
        if step.get("name") == STEP
    ]


def test_every_unit_platform_runs_the_relay_suite():
    assert {job_id for job_id, _ in _relay_steps()} == PLATFORMS


def test_the_relay_step_installs_the_flagship_and_runs_both_targets():
    steps = _relay_steps()
    assert len(steps) == 3
    for job_id, step in steps:
        run = step["run"]
        assert 'uv pip install --system -e "hub/agents/gaia/python"' in run
        assert GLOB in run
        assert UI_LIVE in run
        assert step.get("shell") == "bash", f"{job_id}: the glob needs bash on Windows"


def test_the_glob_reaches_every_top_level_relay_test():
    files = {p.name for p in (REPO_ROOT / "tests" / "unit").glob("test_relay_*.py")}
    assert {
        "test_relay_always_allow.py",
        "test_relay_flagship_documents.py",
        "test_relay_flagship_question.py",
        "test_relay_flagship_tool_history.py",
        "test_relay_followup.py",
        "test_relay_full_access.py",
        "test_relay_tool_confirmation.py",
    } <= files
    assert (REPO_ROOT / UI_LIVE).is_file()
