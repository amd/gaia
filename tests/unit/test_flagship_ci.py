# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep the flagship regression lane active for its shared dependencies."""

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "util"))

# GitHub's filter semantics, negation included, shared with the CI path guard.
from check_ci_path_coverage import PathFilter, glob_to_regex  # noqa: E402


def matches_path(path, pattern):
    """True when one filter glob matches `path`."""
    return glob_to_regex(pattern).fullmatch(path) is not None


def fires_on(path, patterns):
    """True when a PR changing only `path` triggers a `paths:` list."""
    return PathFilter("paths", tuple(patterns)).fires_on(path)


@pytest.fixture
def triggers():
    workflow = ROOT / ".github/workflows/test_gaia_agent.yml"
    return yaml.load(workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)["on"]


@pytest.mark.parametrize("event", ["push", "pull_request"])
@pytest.mark.parametrize(
    "changed_path",
    [
        "hub/agents/gaia/python/gaia_agent/stdio.py",
        "hub/agents/chat/python/gaia_agent_chat/agent.py",
        "src/gaia/ui/sse_translation.py",
        "src/gaia/ui/sse_handler.py",
        "src/gaia/llm/lemonade_client.py",
        "src/gaia/logger.py",
        "src/gaia/rag/sdk.py",
        "src/gaia/connectors/catalog/google.py",
        "hub/skills/coding/SKILL.md",
        "tests/unit/test_flagship_ci.py",
        "tests/unit/test_skill_loader.py",
        "tests/conftest.py",
        "pyproject.toml",
    ],
)
def test_dependency_changes_run_flagship_tests(triggers, event, changed_path):
    assert fires_on(
        changed_path, triggers[event]["paths"]
    ), f"{event} does not run flagship tests for {changed_path}"


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_unrelated_docs_do_not_run_flagship_tests(triggers, event):
    assert not fires_on("docs/guides/talk.mdx", triggers[event]["paths"])


def test_single_star_cannot_hide_narrowed_dependency_coverage():
    assert matches_path("src/gaia/agent.py", "src/gaia/*")
    assert not matches_path("src/gaia/agents/base/agent.py", "src/gaia/*")
    assert matches_path("src/gaia/agents/base/agent.py", "src/gaia/**")
