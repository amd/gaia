# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regression tests: ``FileIOToolsMixin`` must not rewrite a file's line endings.

``open()`` in text mode, and ``Path.read_text`` / ``Path.write_text``, default
to universal newlines. Reading translates every ``\\r\\n`` to ``\\n``; writing
translates every ``\\n`` back to ``os.linesep``. On Windows that means a single
one-line edit to an LF-terminated file rewrites **every** line in it to CRLF.

Nothing about that is visible on screen, which is why it needs a test: the
damage only shows up in a diff, where a three-line change is reported as a
whole-file rewrite.

These tests assert on raw bytes rather than decoded text, because the whole
point of the bug is that the decoded text is identical either way. They also
pin the mirror-image case — a genuinely CRLF file must stay CRLF — so the fix
cannot be "normalise everything to LF", which would be the same bug pointed the
other way.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator

LF_SOURCE = "def one():\n    return 1\n\n\ndef two():\n    return 2\n"
CRLF_SOURCE = LF_SOURCE.replace("\n", "\r\n")


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


class _Host(Agent, FileIOToolsMixin):
    def __init__(self, **kwargs):
        self.path_validator = PathValidator()
        super().__init__(**kwargs)

    def _register_tools(self):
        self.register_file_io_tools()


@pytest.fixture
def agent(tmp_path):
    with patch("gaia.agents.base.agent.AgentSDK"):
        host = _Host(skip_lemonade=True, silent_mode=True)
    host.path_validator.allowed_paths.add(tmp_path.resolve())
    return host


def _tool(name):
    return _TOOL_REGISTRY[name]["function"]


def _write(path: Path, text: str) -> None:
    """Put exact bytes on disk — ``write_text`` would translate them."""
    path.write_bytes(text.encode("utf-8"))


def _eols(path: Path):
    """(crlf_count, lone_lf_count) for the bytes currently on disk."""
    data = path.read_bytes()
    return data.count(b"\r\n"), data.count(b"\n") - data.count(b"\r\n")


# ---------------------------------------------------------------- edit_file


def test_edit_file_leaves_an_lf_file_lf(agent, tmp_path):
    target = tmp_path / "module.py"
    _write(target, LF_SOURCE)
    _tool("read_file")(file_path=str(target))

    result = _tool("edit_file")(
        file_path=str(target), old_content="return 1", new_content="return 11"
    )

    assert result["status"] == "success"
    crlf, lf = _eols(target)
    assert crlf == 0, "edit_file converted an LF file to CRLF"
    assert lf == LF_SOURCE.count("\n")
    assert (
        target.read_bytes() == LF_SOURCE.replace("return 1\n", "return 11\n").encode()
    )


def test_edit_file_leaves_a_crlf_file_crlf(agent, tmp_path):
    target = tmp_path / "module.py"
    _write(target, CRLF_SOURCE)
    _tool("read_file")(file_path=str(target))

    result = _tool("edit_file")(
        file_path=str(target), old_content="return 1", new_content="return 11"
    )

    assert result["status"] == "success"
    crlf, lone_lf = _eols(target)
    assert lone_lf == 0, "edit_file converted a CRLF file to LF"
    assert crlf == CRLF_SOURCE.count("\r\n")


def test_edit_file_changes_only_the_edited_line(agent, tmp_path):
    """The bug's real cost: every other line comes back modified."""
    target = tmp_path / "wide.py"
    original = "".join(f"x{i} = {i}\n" for i in range(200))
    _write(target, original)
    _tool("read_file")(file_path=str(target))

    _tool("edit_file")(
        file_path=str(target), old_content="x100 = 100", new_content="x100 = 999"
    )

    before = original.splitlines(keepends=True)
    after = target.read_bytes().decode("utf-8").splitlines(keepends=True)
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert changed == [100], f"{len(changed)} lines changed, expected exactly 1"


# ---------------------------------------------------------------- write_file


def test_write_file_round_trips_lf_content(agent, tmp_path):
    target = tmp_path / "data.txt"

    result = _tool("write_file")(file_path=str(target), content=LF_SOURCE)

    assert result["status"] == "success"
    assert target.read_bytes() == LF_SOURCE.encode("utf-8")


def test_write_file_round_trips_crlf_content(agent, tmp_path):
    target = tmp_path / "data.txt"

    result = _tool("write_file")(file_path=str(target), content=CRLF_SOURCE)

    assert result["status"] == "success"
    assert target.read_bytes() == CRLF_SOURCE.encode("utf-8")


# --------------------------------------------- matching across line endings


def test_an_lf_old_content_still_matches_a_crlf_file(agent, tmp_path):
    """``read_file`` normalises what it shows, so a multi-line ``old_content``
    arrives LF-terminated even when the file on disk is CRLF. A line-ending
    difference is not a semantic one and must not be why an edit is refused."""
    target = tmp_path / "crlf.py"
    _write(target, CRLF_SOURCE)
    _tool("read_file")(file_path=str(target))

    result = _tool("edit_file")(
        file_path=str(target),
        old_content="def one():\n    return 1\n",
        new_content="def one():\n    return 111\n",
    )

    assert result["status"] == "success"
    _crlf, lone_lf = _eols(target)
    assert lone_lf == 0, "the retargeted edit left the file with mixed endings"
    assert b"return 111" in target.read_bytes()


def test_a_crlf_old_content_still_matches_an_lf_file(agent, tmp_path):
    """The mirror image, so the tolerance is not one-directional."""
    target = tmp_path / "lf.py"
    _write(target, LF_SOURCE)
    _tool("read_file")(file_path=str(target))

    result = _tool("edit_file")(
        file_path=str(target),
        old_content="def one():\r\n    return 1\r\n",
        new_content="def one():\r\n    return 111\r\n",
    )

    assert result["status"] == "success"
    assert b"\r\n" not in target.read_bytes()
    assert b"return 111" in target.read_bytes()


# ----------------------------------------------------------- edit_python_file


def test_edit_python_file_leaves_an_lf_file_lf(agent, tmp_path):
    target = tmp_path / "mod.py"
    _write(target, LF_SOURCE)
    _tool("read_file")(file_path=str(target))

    result = _tool("edit_python_file")(
        file_path=str(target), old_content="return 2", new_content="return 22"
    )

    assert result["status"] == "success"
    assert b"\r\n" not in target.read_bytes()


# ------------------------------------------------------------ replace_function


def test_replace_function_leaves_an_lf_file_lf(agent, tmp_path):
    target = tmp_path / "mod.py"
    _write(target, LF_SOURCE)
    _tool("read_file")(file_path=str(target))

    result = _tool("replace_function")(
        file_path=str(target),
        function_name="one",
        new_implementation="def one():\n    return 111",
    )

    assert result["status"] == "success"
    assert b"\r\n" not in target.read_bytes()


def test_replace_function_does_not_mix_line_endings_in_a_crlf_file(agent, tmp_path):
    """The replacement text is LF whatever the file is; it must be converted,
    not spliced in as-is."""
    target = tmp_path / "mod.py"
    _write(target, CRLF_SOURCE)
    _tool("read_file")(file_path=str(target))

    result = _tool("replace_function")(
        file_path=str(target),
        function_name="one",
        new_implementation="def one():\n    return 111",
    )

    assert result["status"] == "success"
    _crlf, lone_lf = _eols(target)
    assert lone_lf == 0, "replace_function left the file with mixed line endings"
