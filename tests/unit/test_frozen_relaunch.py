# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The frozen `gaia` runs GAIA's own `-m` and `-c` relaunches, and nothing else.

GAIA starts its daemon as `sys.executable -m gaia.daemon`; in the desktop app
`sys.executable` is the frozen `gaia`, which has no Python to hand that to.
"""

from __future__ import annotations

import sys

import pytest

from gaia import frozen


def test_cli_arguments_go_to_the_cli():
    assert frozen.dispatch([]) is None
    assert frozen.dispatch(["chat", "--ui"]) is None
    assert frozen.dispatch(["-m"]) is None


def test_m_runs_a_gaia_module_as_main(monkeypatch):
    ran = {}

    def run_module(name, run_name, alter_sys):
        ran.update(name=name, run_name=run_name, argv=list(sys.argv))

    monkeypatch.setattr(frozen.runpy, "run_module", run_module)
    monkeypatch.setattr(sys, "argv", ["gaia.exe"])
    assert frozen.dispatch(["-m", "gaia.daemon", "--x"]) == 0
    assert ran == {
        "name": "gaia.daemon",
        "run_name": "__main__",
        "argv": ["gaia.daemon", "--x"],
    }


@pytest.mark.parametrize("module", ["pip", "uv", "gaiax", "os", "uvicornx"])
def test_m_refuses_anything_but_gaia(module, monkeypatch, capsys):
    monkeypatch.setattr(frozen.runpy, "run_module", pytest.fail)
    assert frozen.dispatch(["-m", module, "install", "x"]) == 2
    assert "can't install packages" in capsys.readouterr().err


def test_c_runs_like_python_c(monkeypatch):
    # The attempts child drops sys.path[0], as it would under `python -c`.
    monkeypatch.setattr(sys, "path", ["/bundle/base_library.zip", "/bundle"])
    monkeypatch.setattr(sys, "argv", ["gaia.exe"])
    code = "import sys; del sys.path[0]; sys.ran = list(sys.argv)"
    assert frozen.dispatch(["-c", code, "-p", "hi"]) == 0
    assert sys.path == ["/bundle/base_library.zip", "/bundle"]
    assert sys.ran == ["-c", "-p", "hi"]
    del sys.ran


def test_a_module_exit_status_reaches_the_caller(monkeypatch):
    def run_module(name, run_name, alter_sys):
        raise SystemExit(3)

    monkeypatch.setattr(frozen.runpy, "run_module", run_module)
    with pytest.raises(SystemExit) as exc:
        frozen.dispatch(["-m", "gaia.cli"])
    assert exc.value.code == 3
