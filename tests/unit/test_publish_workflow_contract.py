# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
The backend-test step in publish.yml is the release's only pytest gate — assert
it can actually fail.

``test_unit.yml`` triggers on pushes to ``main``, not on the ``v*`` tag that
starts a release, so this one step in ``build-npm`` is the whole Python test
coverage between a tag and PyPI/npm. It shipped with ``2>/dev/null || echo
"Backend tests skipped"``, which made it green whether the tests passed, failed,
or never ran (issue #3508).

These are configuration assertions, not behaviour tests. They exist because the
failure mode is silent: the step still runs and still goes green, it just stops
gating anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yml"

STEP_NAME = "Run backend tests"


@pytest.fixture(scope="module")
def backend_test_step() -> dict:
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing"
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = [
        step
        for step in workflow["jobs"]["build-npm"]["steps"]
        if step.get("name") == STEP_NAME
    ]
    assert steps, f"build-npm no longer has a {STEP_NAME!r} step"
    return steps[0]


def test_the_backend_tests_can_fail_the_release(backend_test_step):
    """No ``||`` fallback and no discarded stderr — the step must be able to go red."""
    run = backend_test_step["run"]
    assert "||" not in run, (
        f"{STEP_NAME!r} swallows a failure with '||'. This is the only pytest run "
        "on the release path, so a fallback here publishes an untested build "
        "(CLAUDE.md 'No Silent Fallbacks')."
    )
    assert "2>/dev/null" not in run, (
        f"{STEP_NAME!r} discards stderr, which hides why a step failed even when "
        "it does fail."
    )


def test_the_backend_tests_install_the_extras_they_import(backend_test_step):
    """``gaia.ui`` imports fastapi, which only the api/ui extras declare.

    ``pip install -e ".[dev]"`` alone cannot collect ``tests/unit/chat/ui/`` —
    ``gaia/ui/_chat_helpers.py`` does ``from fastapi import HTTPException`` at
    import time. Without the extra, a loud step fails on every release instead of
    on a real regression.
    """
    run = backend_test_step["run"]
    assert "pytest tests/unit/chat/ui/" in run, (
        f"{STEP_NAME!r} no longer runs the UI backend suite — the release would "
        "publish with no Python test coverage at all."
    )
    assert "api" in _install_extras(run), (
        f"{STEP_NAME!r} must install the api extra; tests/unit/chat/ui/ imports "
        "fastapi through gaia.ui."
    )


def _install_extras(run: str) -> set[str]:
    """Extras named in the step's ``pip install -e ".[...]"`` line."""
    _, _, tail = run.partition('pip install -e ".[')
    extras, _, _ = tail.partition("]")
    return {extra.strip() for extra in extras.split(",")}
