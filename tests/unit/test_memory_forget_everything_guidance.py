# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""A small model is told how to erase everything, and never to deny it can.

On Gemma-4-E4B, "forget everything you know about me" ran no tool and the
agent said GAIA had no delete-all. The only forget rule it read was
"recall() then forget()", one id at a time, so it concluded each fact had
to go individually. The guidance now maps whole-memory requests to
``forget_everything``, keeps topic requests ("everything about Sam") on
``forget``, and names where the user can erase it themselves (#6701).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.agents.base.memory import STABLE_MEMORY_PROMPT_CHARS
from gaia.agents.base.memory_store import MemoryStore
from gaia.agents.base.tools import _TOOL_REGISTRY
from tests.unit.test_memory_forget_sticks import _Host

VOICE_SKILL = (
    Path(__file__).resolve().parents[2]
    / "hub/agents/gaia/python/gaia_agent/skills/gaia-voice/SKILL.md"
)


@pytest.fixture
def host(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield _Host(db, [])
    db.close()


def _line_with(prompt: str, needle: str) -> str:
    """The bullet holding *needle*, continuation lines included."""
    bullets = prompt.split("\n- ")
    return next(b for b in bullets if needle in b)


def test_whole_memory_requests_map_to_forget_everything(host):
    rule = _line_with(host._build_stable_memory_prompt(), "forget_everything()")

    for phrase in ("Forget everything you know about me", "delete all my data"):
        assert phrase in rule
    assert "one call" in rule


def test_the_rule_says_gaia_can_erase_it_and_where(host):
    rule = _line_with(host._build_stable_memory_prompt(), "forget_everything()")

    assert "Never say GAIA can't" in rule
    assert "Memory › Advanced › Delete all memories" in rule


def test_a_topic_still_goes_through_forget_one_id_at_a_time(host):
    rule = _line_with(host._build_stable_memory_prompt(), "recall() then forget()")

    assert "everything about Sam" in rule


def test_the_guidance_still_fits_the_memory_section(host):
    prompt = host._build_stable_memory_prompt()

    assert len(prompt) <= STABLE_MEMORY_PROMPT_CHARS
    assert "memory truncated" not in prompt


def test_the_tool_specs_point_at_each_other(host):
    everything = _TOOL_REGISTRY["forget_everything"]["description"]
    one = _TOOL_REGISTRY["forget"]["description"]

    assert "forget everything about me" in everything
    assert "delete all my data" in everything
    assert "forget_everything" in one


def test_the_voice_never_denies_a_tool_it_has():
    voice = VOICE_SKILL.read_text(encoding="utf-8")

    assert "If one of your tools does what the user asks, GAIA can do it" in (
        " ".join(voice.split())
    )
