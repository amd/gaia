# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent Hub Worker deploys to production only on an explicit dispatch.

agent_hub_worker_ci.yml runs on every PR that touches the Worker. Its deploy job
holds the Cloudflare credentials, so it must never run for a PR or a push, and
must stay behind the worker-deploy environment, which limits deploys to main and
release tags.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "agent_hub_worker_ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_deploy_runs_only_on_a_dispatch_that_asks_for_it(workflow):
    deploy = workflow["jobs"]["deploy"]
    assert deploy["if"] == "github.event_name == 'workflow_dispatch' && inputs.deploy"
    # PyYAML reads the bare `on:` key as True.
    dispatch = workflow[True]["workflow_dispatch"]["inputs"]["deploy"]
    assert dispatch["type"] == "boolean"
    assert dispatch["default"] is False


def test_deploy_is_behind_the_worker_deploy_environment(workflow):
    assert workflow["jobs"]["deploy"]["environment"] == "worker-deploy"


def test_deploy_waits_for_the_tests(workflow):
    assert workflow["jobs"]["deploy"]["needs"] == "test"


def test_a_push_never_cancels_a_dispatched_deploy(workflow):
    # cancel-in-progress is read from the INCOMING run, so a dispatch is only
    # safe from a push if the two never share a concurrency group.
    concurrency = workflow["concurrency"]
    assert concurrency["cancel-in-progress"] == (
        "${{ github.event_name != 'workflow_dispatch' }}"
    )
    group = concurrency["group"]
    assert "github.event_name == 'workflow_dispatch' && 'dispatch' || 'ci'" in group
