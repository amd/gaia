# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A process whose stdin is the null device has nobody to ask.

On Windows ``NUL`` is a character device, so ``isatty()`` is True there and a
headless agent used to reach ``input()`` and fail with ``EOFError``: the
benchmark's ``write_file`` over an existing file returned "EOF when reading a
line" instead of writing.
"""

import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[2] / "src")


def _run_with_null_stdin(code: str) -> str:
    done = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {SRC!r}); {code}"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return done.stdout.strip()


def test_null_stdin_is_not_interactive():
    out = _run_with_null_stdin(
        "from gaia.utils.terminal import stdin_is_interactive as f; print(f())"
    )
    assert out == "False"


def test_overwriting_a_file_with_null_stdin_needs_no_answer(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")
    out = _run_with_null_stdin(
        "from gaia.security import PathValidator; "
        f"v = PathValidator(allowed_paths=[{str(tmp_path)!r}]); "
        f"print(v.validate_write({str(target)!r}, content_size=3))"
    )
    assert out.splitlines()[-1] == "(True, '')"


def test_a_piped_stdin_is_not_interactive(monkeypatch):
    from gaia.utils import terminal

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert terminal.stdin_is_interactive() is False


def test_a_closed_stdin_is_not_interactive(monkeypatch):
    from gaia.utils import terminal

    def closed():
        raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(sys.stdin, "isatty", closed)
    assert terminal.stdin_is_interactive() is False
