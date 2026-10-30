# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Run by tests/integration/test_daemon_lifetime.py in a child pytest.

The real actions are swapped for recorders at import, before the root
conftest's guard wraps them, so nothing here can reach the real ~/.gaia even if
the guard is broken.
"""

import os
from pathlib import Path

from gaia.daemon import client
from gaia.llm.lemonade_embedded import EmbeddedLemonade

CALLS = []

EmbeddedLemonade.stop = lambda self, *a, **k: CALLS.append(("stop", self.root))
client._spawn_and_wait = lambda *a, **k: CALLS.append(("spawn", None))

REAL_HOME = Path(os.path.expanduser("~")) / ".gaia"


def test_embedded_stop_on_the_real_home_is_refused():
    try:
        EmbeddedLemonade(home=REAL_HOME).stop()
    finally:
        assert CALLS == [], "the guarded action ran"


def test_embedded_stop_on_a_tmp_home_runs(tmp_path):
    EmbeddedLemonade(home=tmp_path).stop()
    assert CALLS == [("stop", tmp_path / "lemonade")]
    CALLS.clear()


def test_daemon_spawn_without_gaia_home_is_refused(monkeypatch):
    monkeypatch.delenv("GAIA_HOME", raising=False)
    try:
        client._spawn_and_wait(1.0)
    finally:
        assert CALLS == [], "the guarded spawn ran"


def test_daemon_spawn_with_a_tmp_gaia_home_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    client._spawn_and_wait(1.0)
    assert CALLS == [("spawn", None)]
    CALLS.clear()
