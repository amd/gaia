# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Shared fixtures for integration tests."""

from pathlib import Path

import pytest


@pytest.fixture
def isolated_gaia_home(tmp_path, monkeypatch):
    """Fake the user's home and point ``GAIA_HOME`` outside it.

    Yields the fake home. Fails at teardown if anything was written to
    ``<home>/.gaia``: with ``GAIA_HOME`` set, a write there means some code
    path hard-codes ``Path.home() / ".gaia"`` and would hit the real one.
    """
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setenv("GAIA_HOME", str(tmp_path / "gaia-home"))
    monkeypatch.delenv("GAIA_DOCUMENT_ROOTS", raising=False)
    yield fake_home
    stray = fake_home / ".gaia"
    assert not stray.exists(), (
        f"Test wrote under {stray} while GAIA_HOME pointed elsewhere: "
        f"{sorted(str(p.relative_to(stray)) for p in stray.rglob('*'))}"
    )
