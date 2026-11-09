# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Optional-fact collectors skip a failing step but leave a log line behind."""

import logging
from pathlib import Path

import pytest

from gaia.agents.base import system_context
from gaia.agents.base.discovery import SystemDiscovery
from gaia.agents.base.memory import MemoryMixin


def test_failing_collector_is_logged_and_the_rest_still_builds(monkeypatch, caplog):
    def broken_machine():
        raise RuntimeError("machine probe exploded")

    monkeypatch.setattr(system_context.platform, "machine", broken_machine)
    caplog.set_level(logging.DEBUG, logger=system_context.__name__)

    facts = system_context.collect_system_info()

    contents = [f["content"] for f in facts]
    assert not any(c.startswith("CPU architecture:") for c in contents)
    assert any(c.startswith("Python version:") for c in contents)
    assert any(c.startswith("System profile last collected on:") for c in contents)

    logged = [
        r
        for r in caplog.records
        if r.name == system_context.__name__ and "architecture" in r.getMessage()
    ]
    assert len(logged) == 1
    assert logged[0].levelno == logging.DEBUG
    assert "machine probe exploded" in logged[0].getMessage()


def test_unreadable_project_manifest_logs_warning_with_path(
    tmp_path, monkeypatch, caplog
):
    fake_home = tmp_path / "fake_home"
    project = fake_home / "Projects" / "proj"
    project.mkdir(parents=True)
    manifest = project / "pyproject.toml"
    manifest.write_text('[project]\nname = "proj"\n', encoding="utf-8")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    real_read_text = Path.read_text

    def read_text(self, *args, **kwargs):
        if self == manifest:
            raise PermissionError("access denied")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    caplog.set_level(logging.WARNING, logger="gaia.agents.base.discovery")

    facts = SystemDiscovery().scan_project_manifests()

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and str(manifest) in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "access denied" in warnings[0].getMessage()
    assert any("Python" in f["content"] for f in facts)


@pytest.mark.parametrize("bad_timestamp", ["not-a-date", 12345])
def test_unreadable_system_context_timestamp_is_logged(bad_timestamp, caplog):
    caplog.set_level(logging.WARNING, logger="gaia.agents.base.memory")

    reason = MemoryMixin._system_context_refresh_reason(
        [{"id": "mem-42", "content": "Hostname: box", "updated_at": bad_timestamp}]
    )

    assert reason is None
    assert any(
        "mem-42" in r.getMessage() and r.levelno == logging.WARNING
        for r in caplog.records
    )
