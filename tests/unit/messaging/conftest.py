# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep the Discord bridge's ``gaia`` launcher off the machine's own install."""

import pytest


@pytest.fixture(autouse=True)
def fake_gaia_launcher(tmp_path, monkeypatch):
    """A stand-in ``gaia.exe``, so a PC without one installed still tests."""
    from gaia.messaging.discord import adapter

    launcher = tmp_path / "launcher" / "gaia.exe"
    launcher.parent.mkdir()
    launcher.write_bytes(b"MZ")
    monkeypatch.setattr(adapter, "_gaia_launcher", lambda: launcher)
    return launcher
