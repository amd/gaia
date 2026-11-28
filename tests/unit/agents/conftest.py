# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared fixtures for the base agent tests."""

import pytest


@pytest.fixture
def chat_and_process(tmp_path, monkeypatch):
    """A chat folder with a file the process folder does not have."""
    chat = tmp_path / "runs" / "chat"
    process = tmp_path / "home"
    chat.mkdir(parents=True)
    process.mkdir()
    (chat / "notes.csv").write_text("name,value\nalpha,1\n", encoding="utf-8")
    monkeypatch.chdir(process)
    return chat, process
