# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Electron test workflow runs with a read-only token (#3528).

It only checks out, builds, tests and uploads artifacts, none of which needs a
write scope. A job that later needs one should grant it at the job level.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _grants(block) -> dict:
    if isinstance(block, str):
        return {"*": block}
    return dict(block or {})


@pytest.mark.parametrize("name", ["test_electron.yml"])
def test_workflow_token_is_read_only(name):
    workflow = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert _grants(workflow.get("permissions")) == {"contents": "read"}
    for job_id, job in workflow["jobs"].items():
        for scope, level in _grants(job.get("permissions")).items():
            assert "write" not in level, f"{name} job {job_id} grants {scope}: {level}"
