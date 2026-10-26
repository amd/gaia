# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The sidecar is spawned by the Mac app with the app's stripped PATH, so its
entry point repairs the PATH before it serves anything."""

import sys
import types

import pytest

from gaia_agent import entry


def _stop_after_path_repair(monkeypatch):
    """Make main() return as soon as the transport is chosen."""
    monkeypatch.setattr(
        entry, "selects_http", lambda argv: (_ for _ in ()).throw(SystemExit(0))
    )


def test_the_path_is_repaired_before_a_transport_is_chosen(monkeypatch):
    order = []
    monkeypatch.setattr("gaia.env.apply_login_path", lambda: order.append("path"))
    monkeypatch.setattr(
        entry,
        "selects_http",
        lambda argv: order.append("dispatch") or (_ for _ in ()).throw(SystemExit(0)),
    )
    with pytest.raises(SystemExit):
        entry.main([])
    assert order == ["path", "dispatch"]


def test_an_older_core_without_the_repair_warns_on_stderr_and_still_runs(
    monkeypatch, capsys
):
    monkeypatch.setitem(sys.modules, "gaia.env", types.ModuleType("gaia.env"))
    _stop_after_path_repair(monkeypatch)
    with pytest.raises(SystemExit):
        entry.main([])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot read your login PATH" in captured.err
    assert "pip install --upgrade amd-gaia" in captured.err
