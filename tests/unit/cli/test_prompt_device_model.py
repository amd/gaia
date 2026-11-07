# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia prompt --device npu` must select the NPU model, not the GPU default (#3827).

An explicit ``--device`` maps to its device config's model the same way
``gaia chat`` does, and fails loudly when the hardware is unavailable.
"""

import sys

import pytest

from gaia import cli
from gaia.agents.registry import NPU_DEFAULT_MODEL_NAME

GGUF_MODEL = "Gemma-4-E4B-it-GGUF"


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


def _stub_init_and_client(monkeypatch, calls):
    def fake_initialize_lemonade_for_agent(agent, **kwargs):
        calls["init"] = kwargs
        return True, "http://localhost:13305/api/v1"

    monkeypatch.setattr(
        cli, "initialize_lemonade_for_agent", fake_initialize_lemonade_for_agent
    )

    class _StubClient:
        # Mirror the real signature: _gaia_cli_client_params filters kwargs
        # against it, so a stub with only **kwargs would receive nothing.
        def __init__(
            self,
            model=None,
            max_tokens=512,
            show_stats=False,
            logging_level=None,
            base_url=None,
            use_claude=False,
            claude_model=None,
            **_,
        ):
            calls["client"] = {"model": model, "base_url": base_url}

        async def prompt(self, message):
            yield "ok"

        def get_stats(self):
            return None

    monkeypatch.setattr(cli, "GaiaCliClient", _StubClient)


def _devices(npu_available=True):
    return {
        "devices": {
            "amd_npu": {"available": npu_available},
            "amd_gpu": [{"available": True}],
        }
    }


def _run_main(argv, monkeypatch, devices):
    monkeypatch.setattr(sys, "argv", ["gaia", *argv])
    monkeypatch.setattr(cli.LemonadeClient, "get_system_info", lambda self: devices)
    try:
        cli.main()
    except SystemExit as exc:
        return exc.code
    # A normal return (the response dict) is success.
    return 0


def test_explicit_npu_maps_to_the_npu_model(gaia_home, monkeypatch):
    calls = {}
    _stub_init_and_client(monkeypatch, calls)
    code = _run_main(["prompt", "hi", "--device", "npu"], monkeypatch, _devices())

    assert code == 0
    assert calls["init"]["model"] == NPU_DEFAULT_MODEL_NAME
    assert calls["client"]["model"] == NPU_DEFAULT_MODEL_NAME


def test_npu_unavailable_fails_loudly(gaia_home, monkeypatch, capsys):
    calls = {}
    _stub_init_and_client(monkeypatch, calls)
    code = _run_main(
        ["prompt", "hi", "--device", "npu"],
        monkeypatch,
        _devices(npu_available=False),
    )

    assert code == 1
    assert "NPU not available" in capsys.readouterr().err
    assert calls == {}


def test_no_device_keeps_the_default_model(gaia_home, monkeypatch):
    calls = {}
    _stub_init_and_client(monkeypatch, calls)
    code = _run_main(["prompt", "hi"], monkeypatch, _devices())

    assert code == 0
    assert calls["init"]["model"] is None
    assert calls["client"]["model"] is None


def test_explicit_model_wins_over_device(gaia_home, monkeypatch):
    calls = {}
    _stub_init_and_client(monkeypatch, calls)
    code = _run_main(
        ["prompt", "hi", "--device", "npu", "--model", GGUF_MODEL],
        monkeypatch,
        _devices(),
    )

    assert code == 0
    assert calls["init"]["model"] == GGUF_MODEL
    assert calls["client"]["model"] == GGUF_MODEL
