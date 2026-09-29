# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Running code identity cannot be borrowed from an unrelated working directory."""

import sys

from gaia.engineering.identity import get_build_identity


def test_identity_is_anchored_and_frozen_is_honestly_unknown(tmp_path, monkeypatch):
    before = get_build_identity()
    monkeypatch.chdir(tmp_path)
    after = get_build_identity()
    assert after["source_commit"] == before["source_commit"]
    assert after["source_commit"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    frozen = get_build_identity()
    assert frozen["source_commit"] is None
    assert frozen["provenance"] == "unknown"
