# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Importing gaia without installed ``amd-gaia`` metadata must keep stdout clean.

A source checkout with no package metadata is a normal developer setup, and
stdout is a protocol channel for MCP stdio servers, JSON-emitting commands and
subprocess workers — a log line there corrupts the first frame.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"

_SITECUSTOMIZE = textwrap.dedent("""
    import importlib.metadata as _md

    _real_version = _md.version

    def _version(name):
        if name == "amd-gaia":
            raise _md.PackageNotFoundError(name)
        return _real_version(name)

    _md.version = _version
    """)


def _run_without_metadata(tmp_path, code):
    (tmp_path / "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), str(SRC_DIR)])
    env["PYTHONIOENCODING"] = "utf-8"
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=tmp_path,
        timeout=180,
        check=False,
    )


@pytest.mark.parametrize("module", ["gaia", "gaia.cli"])
def test_import_without_metadata_keeps_stdout_clean(tmp_path, module):
    result = _run_without_metadata(tmp_path, f"import {module}")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "Failed to get package version" not in result.stderr


def test_version_falls_back_to_source_tree_declaration(tmp_path):
    result = _run_without_metadata(
        tmp_path,
        "import gaia.version as v; print(repr(v.version)); print(v.__version__)",
    )

    assert result.returncode == 0, result.stderr
    reported, declared = result.stdout.splitlines()
    assert declared
    assert reported == repr(declared)
