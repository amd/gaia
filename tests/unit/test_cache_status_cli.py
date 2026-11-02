# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia cache status` presents Context7 as optional (#1090)."""

from __future__ import annotations

import argparse
import logging

import pytest

from gaia import cli


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / ".gaia"))


def _status(monkeypatch, available, caplog):
    from gaia.mcp.external_services import Context7Service

    monkeypatch.setattr(
        Context7Service, "check_availability", classmethod(lambda cls: available)
    )
    with caplog.at_level(logging.DEBUG):
        cli.handle_cache_command(argparse.Namespace(cache_action="status"))


def test_unavailable_context7_is_marked_optional(
    isolated_cache, monkeypatch, capsys, caplog
):
    _status(monkeypatch, False, caplog)
    out = capsys.readouterr().out
    assert "Context7 (optional) is not available" in out
    assert "UNAVAILABLE" not in out
    assert "To enable Context7" in out
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_available_context7_status_unchanged(
    isolated_cache, monkeypatch, capsys, caplog
):
    _status(monkeypatch, True, caplog)
    out = capsys.readouterr().out
    assert "Context7 is AVAILABLE" in out
    assert "To enable Context7" not in out


def test_missing_npx_logs_below_warning(monkeypatch, caplog):
    from gaia.mcp import external_services
    from gaia.mcp.external_services import Context7Service

    monkeypatch.setattr(Context7Service, "_availability_checked", False)
    monkeypatch.setattr(external_services.shutil, "which", lambda _name: None)
    with caplog.at_level(logging.DEBUG):
        assert Context7Service.check_availability() is False
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
