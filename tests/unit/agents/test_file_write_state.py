# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every file mutation honors the read ledger and records its own changes."""

from unittest.mock import Mock, patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_edit import FileStateTracker, record_read
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.file_tools import FileSearchToolsMixin

ORIGINAL = "def target():\n    return 1\n"
EXTERNAL = "def target():\n    return 2\n"
REPLACEMENT = "def target():\n    return 3\n"


@pytest.fixture(
    params=[
        "write_file",
        "write_python_file",
        "write_markdown_file",
        "update_gaia_md",
        "replace_function",
        "search_write_file",
    ]
)
def writer(request, tmp_path):
    tracker = FileStateTracker.instance()
    tracker.clear()
    saved = dict(_TOOL_REGISTRY)
    kind = request.param
    host = FileSearchToolsMixin() if kind == "search_write_file" else FileIOToolsMixin()
    # Permission policy is orthogonal to the ledger; assert it still precedes reads.
    host.path_validator = Mock()
    host.path_validator.validate_write.return_value = (True, "")
    host.path_validator.is_write_blocked.return_value = (False, "")
    host.path_validator.is_path_allowed.return_value = True
    host.path_validator.create_backup.return_value = None
    if kind == "search_write_file":
        host.register_file_search_tools()
    else:
        host.register_file_io_tools()
    tool_name = "write_file" if kind == "search_write_file" else kind
    function = _TOOL_REGISTRY[tool_name]["function"]
    path = tmp_path / ("GAIA.md" if kind == "update_gaia_md" else "sample.py")

    def call():
        if kind == "update_gaia_md":
            return function(str(tmp_path), project_name="Ledger test")
        if kind == "replace_function":
            return function(str(path), "target", REPLACEMENT, backup=False)
        return function(str(path), REPLACEMENT)

    yield path, call, host, kind
    tracker.clear()
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def test_stale_write_preserves_external_content_and_reanchors_retry(writer):
    path, call, host, _ = writer
    path.write_text(ORIGINAL, encoding="utf-8")
    record_read(str(path), ORIGINAL)
    path.write_text(EXTERNAL, encoding="utf-8")
    result = call()
    assert result["status"] == "error"
    assert result["stale"] is True
    assert "current_content" in result
    assert path.read_text(encoding="utf-8") == EXTERNAL
    host.path_validator.create_backup.assert_not_called()
    assert call()["status"] == "success"


def test_successful_write_records_new_contents_for_later_edits(writer):
    path, call, _, _ = writer
    path.write_text(ORIGINAL, encoding="utf-8")
    record_read(str(path), ORIGINAL)
    assert call()["status"] == "success"
    current = path.read_text(encoding="utf-8")
    assert not FileStateTracker.instance().check(str(path), current).diverged
    edit = _TOOL_REGISTRY["edit_file"]["function"]
    assert edit(str(path), current, current + "\n")["status"] == "success"


def test_unread_existing_file_still_writes_and_records_state(writer):
    path, call, _, _ = writer
    path.write_text(ORIGINAL, encoding="utf-8")
    assert call()["status"] == "success"
    tracker = FileStateTracker.instance()
    assert tracker.has_record(str(path))
    assert not tracker.check(str(path), path.read_text(encoding="utf-8")).diverged


def test_denied_write_does_not_read_current_contents(writer):
    path, call, host, kind = writer
    path.write_text(EXTERNAL, encoding="utf-8")
    record_read(str(path), ORIGINAL)
    host.path_validator.validate_write.return_value = (False, "denied")
    host.path_validator.is_path_allowed.return_value = False
    module = "file_tools" if kind == "search_write_file" else "file_io_tools"
    with patch(f"gaia.agents.tools.{module}.check_file_state") as check:
        assert call()["status"] == "error"
        check.assert_not_called()
    assert path.read_text(encoding="utf-8") == EXTERNAL


def test_missing_file_retains_create_or_replace_behavior(writer):
    path, call, _, kind = writer
    result = call()
    if kind == "replace_function":
        assert result["status"] == "error"
        assert "File not found" in result["error"]
        assert not path.exists()
    else:
        assert result["status"] == "success"
        tracker = FileStateTracker.instance()
        assert tracker.has_record(str(path))
        assert not tracker.check(str(path), path.read_text(encoding="utf-8")).diverged
