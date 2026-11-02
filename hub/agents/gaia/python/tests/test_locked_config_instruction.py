# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The flagship must not edit files owned by another process."""

from __future__ import annotations

from gaia_agent.agent import REPOSITORY_WORK_PROMPT

from tests.unit.test_profilespec_characterization import chat_agent_build_context

_MANAGED_FILE_RULE = (
    "If a file or a sibling lock/ownership marker says another program manages it, "
    "do not edit, overwrite, delete, or work around it. Stop and tell the user "
    "what owns it and the documented command or action that releases it; continue "
    "only after the user confirms it is released."
)


def test_always_on_rule_reaches_local_gemma_outside_a_repository(tmp_path):
    outside_repo = tmp_path / "not-a-repository"
    outside_repo.mkdir()
    with chat_agent_build_context(
        "chat", model_id="Gemma-4-E4B-it-GGUF", cwd=outside_repo
    ) as agent:
        prompt = agent._get_system_prompt()
    assert _MANAGED_FILE_RULE in prompt


def test_repository_prompt_does_not_duplicate_the_always_on_rule():
    assert _MANAGED_FILE_RULE not in REPOSITORY_WORK_PROMPT


def test_repository_prompt_does_not_hard_code_fixture_owner_or_command():
    assert "widgetworks-sync" not in REPOSITORY_WORK_PROMPT
    assert "widgetworks sync --stop" not in REPOSITORY_WORK_PROMPT
    assert "sync.toml" not in REPOSITORY_WORK_PROMPT
