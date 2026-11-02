# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship must not edit files owned by another process."""

from __future__ import annotations

from gaia_agent.agent import REPOSITORY_WORK_PROMPT


def test_repository_prompt_stops_for_managed_files():
    assert "sibling lock/ownership marker" in REPOSITORY_WORK_PROMPT
    assert "do not edit, overwrite, delete, or work around it" in REPOSITORY_WORK_PROMPT
    assert "documented command or action that releases it" in REPOSITORY_WORK_PROMPT
    assert "user confirms it is released" in REPOSITORY_WORK_PROMPT


def test_repository_prompt_does_not_hard_code_fixture_owner_or_command():
    assert "widgetworks-sync" not in REPOSITORY_WORK_PROMPT
    assert "widgetworks sync --stop" not in REPOSITORY_WORK_PROMPT
    assert "sync.toml" not in REPOSITORY_WORK_PROMPT