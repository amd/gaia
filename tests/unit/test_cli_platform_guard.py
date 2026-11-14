# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every `gaia` process (the Agent UI backend, the daemon, `gaia -p`) keeps off
WMI, whose timed-out queries crash a Windows process with no traceback."""

import sys

import pytest

import gaia.cli
from gaia import platform_guard


class _Guarded(Exception):
    pass


def test_main_guards_before_anything_else(monkeypatch):
    def _guard():
        raise _Guarded

    monkeypatch.setattr(platform_guard, "avoid_wmi", _guard)
    monkeypatch.setattr(
        "gaia.logger.configure",
        lambda *a, **k: pytest.fail("logging was configured before the guard"),
    )
    monkeypatch.setattr(sys, "argv", ["gaia", "--version"])
    with pytest.raises(_Guarded):
        gaia.cli.main()
