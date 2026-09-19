# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The agent gets one scratch directory; the system temp dir stays out of scope."""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.search_scope import search_roots
from gaia.agents.tools.shell_tools import ShellToolsMixin
from gaia.security import PathValidator


@pytest.fixture
def scratch():
    path = Path(tempfile.mkdtemp(prefix="gaia-scratch-test-"))
    yield path
    shutil.rmtree(path)


@pytest.fixture
def validator(tmp_path, scratch):
    project = tmp_path / "project"
    project.mkdir()
    v = PathValidator(allowed_paths=[str(project)])
    v.set_scratch_dir(str(scratch))
    return v


class TestScratchDirectoryGrant:
    def test_scratch_dir_is_writable(self, validator, scratch):
        target = scratch / "run_tests.py"
        allowed, reason = validator.validate_write(
            str(target), content_size=10, prompt_user=False
        )
        assert allowed, reason

    def test_scratch_dir_passes_the_execute_gate(self, validator, scratch):
        target = scratch / "run_tests.py"
        target.write_text("print('ok')\n", encoding="utf-8")
        assert validator.is_path_allowed(str(target), prompt_user=False)

    def test_scratch_dir_is_readable(self, validator, scratch):
        target = scratch / "intermediate.csv"
        target.write_text("a,b\n", encoding="utf-8")
        allowed, reason = validator.validate_read(str(target), prompt_user=False)
        assert allowed, reason

    def test_other_temp_paths_stay_denied(self, validator):
        sibling = Path(tempfile.gettempdir()) / "gaia-not-the-scratch" / "x.py"
        assert not validator.is_path_allowed(str(sibling), prompt_user=False)
        assert not validator.is_path_allowed("/tmp/other/x.py", prompt_user=False)
        allowed, _ = validator.validate_write(
            "/tmp/other/x.py", content_size=1, prompt_user=False
        )
        assert not allowed

    def test_a_missing_scratch_dir_is_refused(self, tmp_path):
        v = PathValidator(allowed_paths=[])
        with pytest.raises(NotADirectoryError):
            v.set_scratch_dir(str(tmp_path / "does-not-exist"))
        assert v.allowed_paths == set()


class TestDenialNamesTheScratchDir:
    def test_write_denial_under_tmp_names_scratch_dir(self, validator, scratch):
        allowed, reason = validator.validate_write(
            "/tmp/run_tests.py", content_size=1, prompt_user=False
        )
        assert not allowed
        assert str(scratch) in reason

    def test_read_denial_under_system_temp_names_scratch_dir(self, validator, scratch):
        target = Path(tempfile.gettempdir()) / "gaia-elsewhere.csv"
        allowed, reason = validator.validate_read(str(target), prompt_user=False)
        assert not allowed
        assert str(scratch) in reason

    def test_denial_outside_temp_does_not_mention_scratch(self, validator, scratch):
        outside = Path.home() / "gaia-scratch-hint-probe" / "x.py"
        allowed, reason = validator.validate_write(
            str(outside), content_size=1, prompt_user=False
        )
        assert not allowed
        assert str(scratch) not in reason

    def test_no_scratch_dir_means_no_hint(self, tmp_path):
        v = PathValidator(allowed_paths=[str(tmp_path)])
        assert v.scratch_hint("/tmp/run_tests.py") == ""


class TestSecretProtectionUnchangedInScratch:
    @pytest.mark.parametrize("name", [".env", "id_rsa", "server.pem"])
    def test_secrets_in_scratch_still_blocked(self, validator, scratch, name):
        target = scratch / name
        allowed, _ = validator.validate_write(
            str(target), content_size=1, prompt_user=False
        )
        assert not allowed
        target.write_text("secret", encoding="utf-8")
        readable, _ = validator.validate_read(str(target), prompt_user=False)
        assert not readable


class TestScratchIsNotASearchRoot:
    def test_project_stays_the_primary_search_root(self, tmp_path, scratch):
        project = tmp_path / "project"
        project.mkdir()
        v = PathValidator(allowed_paths=[str(project)])
        v.set_scratch_dir(str(scratch))
        host = type("Host", (), {"path_validator": v})()

        assert search_roots(host) == [project.resolve()]


class TestToolDenialsNameTheScratchDir:
    """Tools that build their own "not in allowed paths" error still add the hint."""

    @pytest.fixture
    def outside_temp(self):
        path = Path(tempfile.mkdtemp(prefix="gaia-not-scratch-"))
        yield path
        shutil.rmtree(path)

    @pytest.fixture
    def no_prompt(self, validator):
        with patch.object(validator, "_prompt_user_for_access", return_value=False):
            yield

    @pytest.fixture
    def file_io_tools(self, validator, no_prompt):
        host = FileIOToolsMixin()
        host.path_validator = validator
        host.console = None
        with patch.dict(_TOOL_REGISTRY, clear=True):
            host.register_file_io_tools()
            yield {name: entry["function"] for name, entry in _TOOL_REGISTRY.items()}

    def test_edit_file_denial_names_scratch_dir(
        self, file_io_tools, outside_temp, scratch
    ):
        result = file_io_tools["edit_file"](
            file_path=str(outside_temp / "x.py"), old_content="a", new_content="b"
        )
        assert result["status"] == "error"
        assert str(scratch) in result["error"]

    def test_search_code_denial_names_scratch_dir(
        self, file_io_tools, outside_temp, scratch
    ):
        result = file_io_tools["search_code"](directory=str(outside_temp), pattern="x")
        assert result["status"] == "error"
        assert str(scratch) in result["error"]

    def test_shell_working_directory_denial_names_scratch_dir(
        self, validator, no_prompt, outside_temp, scratch
    ):
        host = ShellToolsMixin()
        host.path_validator = validator
        with patch.dict(_TOOL_REGISTRY, clear=True):
            host.register_shell_tools()
            result = _TOOL_REGISTRY["run_shell_command"]["function"](
                command="ls", working_directory=str(outside_temp)
            )
        assert result["status"] == "error"
        assert str(scratch) in result["error"]
