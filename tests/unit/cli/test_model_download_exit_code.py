# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia download` must not exit 0 after printing "server not running" (#3829).

A script or CI step branches on the exit code, so a printed error with a
zero exit reads as success and the pipeline moves on without the models.
"""

import re
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
    assert "not reachable" in capsys.readouterr().err


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
    assert "not reachable" in captured.err
    assert "gaia init" not in captured.err
    assert "⬜" not in captured.out


def test_download_list_names_an_unknown_agent_before_the_server_check(
    gaia_home, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "check_lemonade_health", lambda *a, **k: False)
    code = _run_main(["download", "--list", "--agent", "nope"], monkeypatch)

    assert code == 1
    assert "Unknown agent: nope" in capsys.readouterr().out


def test_download_refuses_the_voice_on_a_mac_with_why(gaia_home, monkeypatch, capsys):
    import gaia.config

    monkeypatch.setattr(gaia.config, "_current_platform", lambda: "darwin")
    monkeypatch.setattr(cli, "check_lemonade_health", lambda *a, **k: True)
    monkeypatch.setattr(
        cli.LemonadeClient,
        "get_required_models",
        lambda self, agent: ["Gemma-4-E4B-it-GGUF", "kokoro-v1"],
    )
    monkeypatch.setattr(cli.LemonadeClient, "check_model_available", lambda *a: True)

    def never_pull(self, model_name, **_):
        raise AssertionError(f"pulled {model_name}")

    monkeypatch.setattr(cli.LemonadeClient, "pull_model_stream", never_pull)
    code = _run_main(["download", "--agent", "talk"], monkeypatch)

    captured = capsys.readouterr()
    # The console wraps the message inside a box; read it back as one line.
    said = " ".join(re.sub("[│╭╮╰╯─]", " ", captured.out + captured.err).split())
    assert code == 1
    assert "aren't offered on macOS" in said
