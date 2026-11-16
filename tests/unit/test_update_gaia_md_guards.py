# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""update_gaia_md runs the same write guardrails as every other write tool (#4006)."""

from unittest.mock import patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.base.verification import check_was_executed
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import BackupError, PathValidator


def _tools(host):
    saved = dict(_TOOL_REGISTRY)
    try:
        host.register_file_io_tools()
        return {
            name: entry["function"]
            for name, entry in _TOOL_REGISTRY.items()
            if saved.get(name) is not entry
        }
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def setup(tmp_path, mock_home):
    project = tmp_path / "project"
    project.mkdir()
    validator = PathValidator()
    validator.allowed_paths.add(project.resolve())
    host = FileIOToolsMixin()
    host.path_validator = validator
    host._path_validator = validator
    tools = _tools(host)
    return host, validator, project, tools


def _read(tools, path):
    assert tools["read_file"](file_path=str(path))["status"] == "success"


def _audits(validator):
    calls = []
    real = validator.audit_write

    def spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    return calls, spy


def test_new_gaia_md_is_written_and_audited(setup):
    _, validator, project, tools = setup
    calls, spy = _audits(validator)

    with patch.object(validator, "audit_write", side_effect=spy):
        result = tools["update_gaia_md"](project_root=str(project), project_name="P")

    assert result["status"] == "success", result
    assert result["created"] is True
    assert "backup_path" not in result
    gaia_md = str(project / "GAIA.md")
    assert [c[:2] + c[3:4] for c in calls] == [("write", gaia_md, "success")]
    assert calls[0][2] == len((project / "GAIA.md").read_bytes())


def test_existing_gaia_md_is_backed_up_before_overwrite(setup):
    _, validator, project, tools = setup
    gaia_md = project / "GAIA.md"
    gaia_md.write_text("# Mine\n", encoding="utf-8")
    _read(tools, gaia_md)
    calls, spy = _audits(validator)

    with patch.object(validator, "audit_write", side_effect=spy):
        result = tools["update_gaia_md"](project_root=str(project), project_name="P")

    assert result["status"] == "success", result
    assert result["created"] is False
    backup = result["backup_path"]
    with open(backup, encoding="utf-8") as f:
        assert f.read() == "# Mine\n"
    assert "## Project: P" in gaia_md.read_text(encoding="utf-8")
    assert calls[-1][3] == "success"
    assert calls[-1][4] == f"backup={backup}"


def test_backup_failure_leaves_the_file_untouched(setup):
    _, validator, project, tools = setup
    gaia_md = project / "GAIA.md"
    gaia_md.write_text("# Mine\n", encoding="utf-8")
    _read(tools, gaia_md)

    with (
        patch.object(validator, "create_backup", side_effect=BackupError("disk full")),
        patch.object(validator, "audit_write") as audit,
    ):
        result = tools["update_gaia_md"](project_root=str(project))

    assert result["status"] == "error"
    assert check_was_executed(result) is False
    assert gaia_md.read_text(encoding="utf-8") == "# Mine\n"
    assert audit.call_args.args[3] == "denied"


def test_blocked_write_is_refused_and_audited(setup):
    _, validator, project, tools = setup
    calls, spy = _audits(validator)

    with (
        patch.object(
            validator, "is_write_blocked", return_value=(True, "Write blocked: test")
        ),
        patch.object(validator, "audit_write", side_effect=spy),
    ):
        result = tools["update_gaia_md"](project_root=str(project))

    assert result["status"] == "error"
    assert result["error"] == "Write blocked: test"
    assert check_was_executed(result) is False
    assert not (project / "GAIA.md").exists()
    assert calls and calls[-1][3] == "denied"


def test_outside_allowed_paths_is_refused_and_audited(setup, tmp_path):
    _, validator, _, tools = setup
    outside = tmp_path / "outside"
    outside.mkdir()
    calls, spy = _audits(validator)

    with (
        patch.object(validator, "is_path_allowed", return_value=False),
        patch.object(validator, "audit_write", side_effect=spy),
    ):
        result = tools["update_gaia_md"](project_root=str(outside))

    assert result["status"] == "error"
    assert not (outside / "GAIA.md").exists()
    assert calls and calls[-1][3] == "denied"


def test_oversized_content_is_refused(setup):
    _, validator, project, tools = setup

    with (
        patch("gaia.security.MAX_WRITE_SIZE_BYTES", 10),
        patch.object(validator, "audit_write") as audit,
    ):
        result = tools["update_gaia_md"](project_root=str(project))

    assert result["status"] == "error"
    assert "exceeds" in result["error"]
    assert not (project / "GAIA.md").exists()
    assert audit.call_args.args[3] == "denied"


def test_unread_existing_gaia_md_is_refused_and_audited(setup):
    _, validator, project, tools = setup
    gaia_md = project / "GAIA.md"
    gaia_md.write_text("# Mine\n", encoding="utf-8")

    with patch.object(validator, "audit_write") as audit:
        result = tools["update_gaia_md"](project_root=str(project))

    assert result["status"] == "error"
    assert gaia_md.read_text(encoding="utf-8") == "# Mine\n"
    assert audit.call_args.args[3] == "denied"


def test_unexpected_write_error_is_audited(setup):
    _, validator, project, tools = setup

    with (
        patch("builtins.open", side_effect=PermissionError("locked")),
        patch.object(validator, "audit_write") as audit,
    ):
        result = tools["update_gaia_md"](project_root=str(project))

    assert result["status"] == "error"
    assert "locked" in result["error"]
    assert audit.call_args.args[3] == "error"
