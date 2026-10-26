# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A frozen binary reads tiktoken's cl100k_base from its bundle (#5066).

The published agent logged ``Unknown encoding cl100k_base`` and counted tokens
by estimate. The PyInstaller side is checked by the frozen smoke test; these
pin the runtime switch and the build-time staging.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from gaia.utils import tiktoken_bundle

_CACHE_ENV = ("TIKTOKEN_CACHE_DIR", "DATA_GYM_CACHE_DIR")


@pytest.fixture
def no_cache_env(monkeypatch):
    for name in _CACHE_ENV:
        monkeypatch.delenv(name, raising=False)


class TestUseBundledCache:
    def test_a_frozen_binary_reads_the_bundled_cache(
        self, tmp_path, monkeypatch, no_cache_env
    ):
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

        tiktoken_bundle.use_bundled_cache()

        assert os.environ["TIKTOKEN_CACHE_DIR"] == str(
            tmp_path / tiktoken_bundle.BUNDLE_DIR
        )

    @pytest.mark.parametrize("name", _CACHE_ENV)
    def test_a_cache_folder_the_user_set_wins(
        self, name, tmp_path, monkeypatch, no_cache_env
    ):
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        monkeypatch.setenv(name, "/user/cache")

        tiktoken_bundle.use_bundled_cache()

        assert os.environ[name] == "/user/cache"
        if name != "TIKTOKEN_CACHE_DIR":
            assert "TIKTOKEN_CACHE_DIR" not in os.environ

    def test_a_source_checkout_is_left_alone(self, monkeypatch, no_cache_env):
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)

        tiktoken_bundle.use_bundled_cache()

        assert "TIKTOKEN_CACHE_DIR" not in os.environ


class TestStageCache:
    def test_it_runs_tiktoken_with_the_cache_pointed_at_dest(
        self, tmp_path, monkeypatch
    ):
        dest = tmp_path / "cache"
        (dest / "stale").parent.mkdir()
        (dest / "stale").write_text("old")
        calls = []

        def fake_run(cmd, env, **_):
            calls.append((cmd, env))
            Path(env["TIKTOKEN_CACHE_DIR"], "9b5ad71b").write_bytes(b"bpe")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(tiktoken_bundle.subprocess, "run", fake_run)
        monkeypatch.setenv("DATA_GYM_CACHE_DIR", "/elsewhere")

        assert tiktoken_bundle.stage_cache(dest) == dest

        assert sorted(p.name for p in dest.iterdir()) == ["9b5ad71b"]
        cmd, env = calls[0]
        assert cmd[0] == sys.executable
        assert "get_encoding('cl100k_base')" in cmd[-1]
        assert env["TIKTOKEN_CACHE_DIR"] == str(dest)
        assert "DATA_GYM_CACHE_DIR" not in env

    def test_a_failed_download_fails_the_build(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            tiktoken_bundle.subprocess,
            "run",
            lambda cmd, **_: subprocess.CompletedProcess(cmd, 1, "", "ProxyError"),
        )

        with pytest.raises(SystemExit) as excinfo:
            tiktoken_bundle.stage_cache(tmp_path / "cache")

        assert "cl100k_base" in str(excinfo.value)
        assert "ProxyError" in str(excinfo.value)

    def test_a_run_that_wrote_nothing_fails_the_build(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            tiktoken_bundle.subprocess,
            "run",
            lambda cmd, **_: subprocess.CompletedProcess(cmd, 0, "", ""),
        )

        with pytest.raises(SystemExit):
            tiktoken_bundle.stage_cache(tmp_path / "cache")


def test_the_tokenizer_switches_to_the_bundle_before_loading(
    tmp_path, monkeypatch, no_cache_env
):
    """``_Tokenizer`` must point tiktoken at the bundle before it loads."""
    pytest.importorskip("tiktoken")
    import tiktoken

    from gaia.agents.base import turn_metrics

    seen = {}

    def fake_get_encoding(name):
        seen[name] = os.environ.get("TIKTOKEN_CACHE_DIR")
        raise ValueError("stop here")

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(tiktoken, "get_encoding", fake_get_encoding)

    turn_metrics._Tokenizer()

    assert seen == {"cl100k_base": str(tmp_path / tiktoken_bundle.BUNDLE_DIR)}
