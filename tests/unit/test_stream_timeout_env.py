# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""GAIA_STREAM_TIMEOUT parsing for the Agent UI streaming-turn deadline."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from gaia.ui import _chat_helpers
from gaia.ui._chat_helpers import _stream_timeout_from_env

ENV = "GAIA_STREAM_TIMEOUT"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_unset_or_blank_uses_default(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv(ENV, raising=False)
    else:
        monkeypatch.setenv(ENV, raw)
    assert _stream_timeout_from_env() == 600


@pytest.mark.parametrize("raw,expected", [("1800", 1800), (" 1 ", 1), ("86400", 86400)])
def test_valid_value_is_used(monkeypatch, raw, expected):
    monkeypatch.setenv(ENV, raw)
    assert _stream_timeout_from_env() == expected


@pytest.mark.parametrize("raw", ["abc", "1800s", "12.5", "nan", "inf"])
def test_non_integer_raises_naming_the_variable(monkeypatch, raw):
    monkeypatch.setenv(ENV, raw)
    with pytest.raises(ValueError, match=r"GAIA_STREAM_TIMEOUT=.*whole number"):
        _stream_timeout_from_env()


@pytest.mark.parametrize("raw", ["0", "-5", "86401"])
def test_out_of_range_raises(monkeypatch, raw):
    monkeypatch.setenv(ENV, raw)
    with pytest.raises(ValueError, match=r"out of range.*1 to 86400"):
        _stream_timeout_from_env()


def _import_in_subprocess(value):
    src_root = Path(_chat_helpers.__file__).resolve().parents[2]
    env = {**os.environ, ENV: value, "PYTHONPATH": str(src_root)}
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from gaia.ui._chat_helpers import _STREAM_TIMEOUT_SECONDS as t; print(t)",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_bad_value_stops_import():
    result = _import_in_subprocess("ten minutes")
    assert result.returncode != 0
    assert "GAIA_STREAM_TIMEOUT='ten minutes' is not a whole number" in result.stderr


def test_module_constant_reflects_env_at_import():
    result = _import_in_subprocess("1200")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "1200"
