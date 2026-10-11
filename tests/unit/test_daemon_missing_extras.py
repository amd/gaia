# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A daemon that cannot start for lack of [api] must say so, not "exited early".

``gaia daemon`` checked its extras, but the programmatic start every other
caller uses (the TUI's setup gate, ``gaia init``) spawned the daemon anyway. It
died on ``ModuleNotFoundError: uvicorn`` and the user saw "the daemon process
exited early (code 1)" plus "press f to set it up again", which fails the same
way every time.
"""

from __future__ import annotations

import pytest

from gaia.daemon import client
from gaia.daemon.errors import DaemonStartError


@pytest.fixture
def no_daemon_running(monkeypatch):
    monkeypatch.setattr(client, "attach", lambda *a, **k: None)
    monkeypatch.setattr(client, "read_instance", lambda: None)


def test_missing_extras_names_the_packages_and_the_install(
    monkeypatch, no_daemon_running
):
    monkeypatch.setattr(client, "missing_daemon_deps", lambda: ["fastapi", "uvicorn"])

    with pytest.raises(DaemonStartError) as exc:
        client.start_or_attach(timeout=1)

    message = str(exc.value)
    assert "fastapi, uvicorn" in message
    assert 'pip install "amd-gaia[api]"' in message
    assert "exited early" not in message


def test_complete_install_still_spawns(monkeypatch, no_daemon_running):
    spawned = []
    monkeypatch.setattr(client, "missing_daemon_deps", lambda: [])
    monkeypatch.setattr(client, "_spawn_and_wait", lambda t: spawned.append(t))

    client.start_or_attach(timeout=1)

    assert spawned == [1]


def test_missing_daemon_deps_reports_unimportable_modules(monkeypatch):
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a: None if name == "uvicorn" else real(name, *a),
    )

    assert client.missing_daemon_deps() == ["uvicorn"]
