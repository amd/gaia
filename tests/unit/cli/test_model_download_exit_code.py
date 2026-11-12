# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia download` must not exit 0 after printing "server not running" (#3829).

A script or CI step branches on the exit code, so a printed error with a
zero exit reads as success and the pipeline moves on without the models.
"""

import sys

import pytest

from gaia import cli


@pytest.fixture
def gaia_home(tmp_path, monkeypatch):
    home = tmp_path / "gaia_home"
    home.mkdir()
    monkeypatch.setenv("GAIA_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("GAIA_CONFIG_DIR", raising=False)
    monkeypatch.delenv("GAIA_CONFIG_FILE", raising=False)
    return home


def _run_main(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gaia", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


@pytest.mark.parametrize(
    "argv", [["download", "--agent", "chat"], ["download", "--clear-cache"]]
)
def test_download_exits_nonzero_when_the_server_is_unreachable(
    argv, gaia_home, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "check_lemonade_health", lambda *a, **k: False)
    code = _run_main(argv, monkeypatch)

    assert code == 1
    assert "not running or not accessible" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv", [["download", "--list"], ["download", "--list", "--agent", "chat"]]
)
def test_download_list_exits_nonzero_when_the_server_is_unreachable(
    argv, gaia_home, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "check_lemonade_health", lambda *a, **k: False)

    def must_not_probe(self, model_id):
        raise AssertionError(f"probed {model_id} with the server down")

    monkeypatch.setattr(cli.LemonadeClient, "check_model_available", must_not_probe)
    code = _run_main(argv, monkeypatch)

    captured = capsys.readouterr()
    assert code == 1
    assert "not running or not accessible" in captured.err
    assert "⬜" not in captured.out


def test_clear_cache_names_this_oss_cache_folders_for_a_loaded_model(
    gaia_home, monkeypatch, capsys
):
    from gaia.llm.lemonade_client import LemonadeClientError
    from gaia.llm.lemonade_launcher import StartHint

    monkeypatch.setattr(cli, "check_lemonade_health", lambda *a, **k: True)
    monkeypatch.setattr(
        cli, "describe_start_hint", lambda: StartHint(instruction="hint", command=None)
    )
    monkeypatch.setattr(
        cli, "model_cache_dirs", lambda: ("/lemonade/cache/", "/hf/cache/")
    )
    monkeypatch.setattr(
        cli.LemonadeClient, "get_required_models", lambda self, agent: ["m"]
    )

    def in_use(self, model_id):
        raise LemonadeClientError("file is being used by another process")

    monkeypatch.setattr(cli.LemonadeClient, "delete_model", in_use)
    monkeypatch.setattr(sys, "argv", ["gaia", "download", "--clear-cache"])
    try:
        cli.main()
    except SystemExit:
        pass

    out = capsys.readouterr().out
    assert "Lemonade cache: /lemonade/cache/" in out
    assert "HuggingFace cache: /hf/cache/" in out
    assert "%LOCALAPPDATA%" not in out


@pytest.mark.parametrize(
    "platform, lemonade_part",
    [("win32", "lemonade"), ("linux", ".local/share/lemonade")],
)
def test_model_cache_dirs_follow_the_platform(monkeypatch, platform, lemonade_part):
    import os

    from gaia.llm import lemonade_client

    if platform == "win32" and os.name != "nt":
        pytest.skip("%VAR% expansion only happens on Windows")
    monkeypatch.setenv("LOCALAPPDATA", "LAD")
    monkeypatch.setattr(lemonade_client.sys, "platform", platform)
    lemonade_cache, hf_cache = lemonade_client.model_cache_dirs()

    assert lemonade_part in lemonade_cache.replace("\\", "/")
    assert "huggingface" in hf_cache
    assert "%" not in lemonade_cache + hf_cache
    assert lemonade_cache.startswith("LAD") == (platform == "win32")


def test_download_list_names_an_unknown_agent_before_the_server_check(
    gaia_home, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "check_lemonade_health", lambda *a, **k: False)
    code = _run_main(["download", "--list", "--agent", "nope"], monkeypatch)

    assert code == 1
    assert "Unknown agent: nope" in capsys.readouterr().out
