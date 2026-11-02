# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship must not edit files owned by another process."""

from __future__ import annotations

from types import SimpleNamespace

from gaia_agent.agent import REPOSITORY_WORK_PROMPT
from gaia_agent.chat_agent import ChatAgent


_MANAGED_FILE_RULE = (
    "If a file or a sibling lock/ownership marker says another program manages it, "
    "do not edit, overwrite, delete, or work around it. Stop and tell the user "
    "what owns it and the documented command or action that releases it; continue "
    "only after the user confirms it is released."
)


def test_always_on_prompt_stops_for_managed_files_outside_a_repository():
    agent = object.__new__(ChatAgent)
    agent.config = SimpleNamespace(
        prompt_profile="chat",
        enable_filesystem=False,
        enable_scratchpad=False,
        enable_browser=False,
        surface=None,
    )
    agent.model_features = SimpleNamespace(scratchpad=False, image_generation=False)
    agent._inference_location = lambda: SimpleNamespace(
        remote=False,
        display="local",
        describe=lambda: "local",
    )
    prompt = agent._get_system_prompt()
    assert _MANAGED_FILE_RULE in prompt


def test_repository_prompt_does_not_duplicate_the_always_on_rule():
    assert _MANAGED_FILE_RULE not in REPOSITORY_WORK_PROMPT


def test_repository_prompt_does_not_hard_code_fixture_owner_or_command():
    assert "widgetworks-sync" not in REPOSITORY_WORK_PROMPT
    assert "widgetworks sync --stop" not in REPOSITORY_WORK_PROMPT
    assert "sync.toml" not in REPOSITORY_WORK_PROMPT