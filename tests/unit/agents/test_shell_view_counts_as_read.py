# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A file the model viewed through the shell counts as read for the edit tools.

Of 29 edit failures in the benchmark runs, 17 were the read-first guard
refusing a file the model had just shown itself with ``sed -n``, ``head`` or
``cat`` -- each followed by a re-read and a retry step. The shell tool now
records the files a successful viewer command showed, resolved against the
directory the command ran in, into the same record the guard consults.
"""

# pylint: disable=protected-access,attribute-defined-outside-init

import sys

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.shell_tools import ShellToolsMixin, viewed_paths
from gaia.security import PathValidator

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX viewer commands run through cmd.exe"
)

SAMPLE = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"


class _Console:
    """Bypass permissions: the developer tier, so ``sed -n`` runs."""

    bypass_permissions = True
    full_access = True
    auto_approve_gated_tools = True

    def print_diff(self, *_):
        pass

    def print_info(self, *_):
        pass


class _Host(ShellToolsMixin, FileIOToolsMixin):
    """One agent's shell and file tools, without the agent around them."""

    debug = False

    def __init__(self):
        super().__init__()
        self.console = _Console()


@pytest.fixture
def host(tmp_path):
    validator = PathValidator()
    validator.allowed_paths.add(tmp_path.resolve())
    saved = dict(_TOOL_REGISTRY)
    try:
        host = _Host()
        host.path_validator = validator
        host._path_validator = validator
        host.register_shell_tools()
        host.register_file_io_tools()
        host.tools = {
            name: entry["function"]
            for name, entry in _TOOL_REGISTRY.items()
            if name in ("run_shell_command", "edit_file")
        }
        yield host
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


def _edit(host, path):
    return host.tools["edit_file"](
        file_path=str(path), old_content="return 1", new_content="return 10"
    )


def _shell(host, command, cwd):
    result = host.tools["run_shell_command"](
        command=command, working_directory=str(cwd)
    )
    assert result["status"] == "success", result
    assert result["return_code"] == 0, result
    return result


def test_sed_view_then_edit_is_allowed(host, sample, tmp_path):
    _shell(host, f"sed -n '1,80p' {sample}", tmp_path)

    result = _edit(host, sample)

    assert result["status"] == "success", result
    assert "return 10" in sample.read_text()


def test_never_viewed_file_is_still_refused(host, sample):
    result = _edit(host, sample)

    assert result["status"] == "error"
    assert result["error_type"] == "not_read"
    assert "return 1" in sample.read_text()


def test_relative_path_resolves_against_the_cd_target(host, tmp_path):
    sub = tmp_path / "pkg"
    sub.mkdir()
    target = sub / "mod.py"
    target.write_text(SAMPLE, encoding="utf-8")

    _shell(host, "cd pkg && head -3 mod.py", tmp_path)

    assert _edit(host, target)["status"] == "success"


def test_a_viewer_that_failed_records_nothing(host, sample, tmp_path):
    result = host.tools["run_shell_command"](
        command=f"cat {tmp_path / 'missing.py'} {sample}",
        working_directory=str(tmp_path),
    )
    assert result["return_code"] != 0

    assert _edit(host, sample)["error_type"] == "not_read"


def test_a_file_that_changed_after_the_view_is_refused(host, sample, tmp_path):
    _shell(host, f"head {sample.name}", tmp_path)
    sample.write_text(SAMPLE + "\n# appended\n", encoding="utf-8")

    assert _edit(host, sample)["error_type"] == "changed_since_read"


@pytest.mark.parametrize(
    "segment, viewed",
    [
        (["sed", "-n", "1,80p", "sample.py"], True),
        (["sed", "-n", "-e", "1,80p", "sample.py"], True),
        (["grep", "-n", "alpha", "sample.py"], True),
        (["head", "-n", "20", "sample.py"], True),
        (["tail", "-n", "+2", "sample.py"], True),
        (["cat", "sample.py"], True),
        (["sed", "1p", "sample.py"], False),
        (["grep", "alpha", "sample.py"], False),
        (["wc", "-l", "sample.py"], False),
        (["ls", "sample.py"], False),
    ],
)
def test_viewed_paths_names_only_line_viewers(sample, tmp_path, segment, viewed):
    assert (viewed_paths(segment, str(tmp_path)) == [str(sample.resolve())]) is viewed
