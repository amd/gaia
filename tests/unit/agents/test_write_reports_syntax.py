# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A .py file that will not parse says so in the result of the write itself.

Told only "success", a local model wrote print("Primes below 100:\\") — then
called a later "unterminated string literal" a parsing artifact and described
output the file cannot produce.
"""

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator


@pytest.fixture
def tools(tmp_path):
    host = FileIOToolsMixin()
    host.path_validator = PathValidator()
    host.path_validator.allowed_paths.add(tmp_path.resolve())
    saved = dict(_TOOL_REGISTRY)
    try:
        host.register_file_io_tools()
        yield {
            n: _TOOL_REGISTRY[n]["function"]
            for n in ("read_file", "write_file", "edit_file")
        }
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


BROKEN = 'def main():\n    print("Primes below 100:\\")\n'


def test_writing_python_that_does_not_parse_says_where(tools, tmp_path):
    out = tools["write_file"](str(tmp_path / "sieve.py"), BROKEN)
    assert out["status"] == "success"
    assert out["syntax_error"].startswith("line 2: unterminated string literal")


def test_an_edit_that_breaks_the_syntax_says_so(tools, tmp_path):
    path = tmp_path / "ok.py"
    path.write_text('print("hi")\n')
    tools["read_file"](str(path))
    out = tools["edit_file"](str(path), 'print("hi")', 'print("hi\\")')
    assert out["status"] == "success" and "syntax_error" in out


@pytest.mark.parametrize(
    "name, content",
    [("fine.py", 'print("ok")\n'), ("notes.md", 'print("broken\\")\n')],
)
def test_valid_python_and_other_files_carry_nothing(tools, tmp_path, name, content):
    out = tools["write_file"](str(tmp_path / name), content)
    assert "syntax_error" not in out
