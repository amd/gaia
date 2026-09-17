# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Asking for a file by name must not be filtered out by its extension.

``search_file`` restricts itself to a hard-coded list of "interesting"
extensions. ``.log`` was not on it, so searching for ``ci.log`` in a directory
containing ``ci.log`` returned nothing — and the agent, reasonably, reported
that the workspace was empty and gave up on the task.
"""

import os

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_tools import FileSearchToolsMixin


class _Host(FileSearchToolsMixin):
    """Bare mixin host — no validator, so the search falls back to cwd."""


@pytest.fixture
def search(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    snapshot = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    _Host().register_file_search_tools()
    fn = _TOOL_REGISTRY["search_file"]["function"]
    yield fn
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _names(result):
    files = result.get("files") or result.get("results") or []
    return {os.path.basename(f["path"] if isinstance(f, dict) else f) for f in files}


@pytest.mark.parametrize("name", ["ci.log", "pyproject.toml", "config.yaml"])
def test_a_file_named_exactly_is_found_whatever_its_extension(search, tmp_path, name):
    (tmp_path / name).write_text("x", encoding="utf-8")
    assert name in _names(search(file_pattern=name))


def test_an_explicit_file_types_filter_still_wins(search, tmp_path):
    # The caller narrowing the search on purpose must not be widened back out.
    (tmp_path / "ci.log").write_text("x", encoding="utf-8")
    (tmp_path / "ci.txt").write_text("x", encoding="utf-8")
    found = _names(search(file_pattern="ci", file_types="txt"))
    assert "ci.txt" in found
    assert "ci.log" not in found


def test_a_bare_stem_still_matches_a_known_extension(search, tmp_path):
    (tmp_path / "notes.md").write_text("x", encoding="utf-8")
    assert "notes.md" in _names(search(file_pattern="notes"))
