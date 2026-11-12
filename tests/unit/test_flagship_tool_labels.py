# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every tool the flagship registers reads as plain words in the Agent UI.

A tool with no label shows its id in the activity list ("Using run python
failed"), and one with no narration override reads as "Running request user
input". The flagship's tool list is ``FULL_CORE_TOOLS`` plus every bundle and
optional tool, which ``test_full_tool_bundles.py`` holds equal to the live
registry; this test holds the labels to that list.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# test_unit.yml has no flagship wheel; test_gaia_agent.yml runs this file.
pytest.importorskip("gaia_agent")

from gaia_agent.tool_bundles import (  # noqa: E402
    FULL_BUNDLES,
    FULL_CORE_TOOLS,
    FULL_OPTIONAL_TOOLS,
)

from gaia.ui.event_narration import (  # noqa: E402
    _NARRATION_OVERRIDES,
    _VERB_FORMS,
    derive_narration,
)

LOCALES = Path(__file__).resolve().parents[2] / "src/gaia/apps/webui/src/i18n/locales"


def _flagship_tools() -> list[str]:
    tools = set(FULL_CORE_TOOLS) | set(FULL_OPTIONAL_TOOLS)
    for bundle in FULL_BUNDLES:
        tools |= set(bundle.members)
    return sorted(tools)


def _label_keys(locale: str) -> set[str]:
    text = (LOCALES / f"{locale}.ts").read_text(encoding="utf-8")
    return set(re.findall(r"^\s*'(tool\.[a-z0-9_]+\.(?:done|active))':", text, re.M))


@pytest.mark.parametrize("locale", ["en", "ar"])
def test_every_flagship_tool_has_a_ui_label(locale):
    keys = _label_keys(locale)
    missing = [
        name
        for name in _flagship_tools()
        if f"tool.{name}.done" not in keys or f"tool.{name}.active" not in keys
    ]
    assert not missing, (
        f"flagship tools with no activity-list label in {locale}.ts: {missing}. "
        f"Add 'tool.<name>.done' and 'tool.<name>.active' to "
        f"src/gaia/apps/webui/src/i18n/locales/{locale}.ts."
    )


def test_every_flagship_tool_narrates_as_an_action():
    unreadable = []
    for name in _flagship_tools():
        head = re.split(r"[_.\s-]+", name)[0].lower()
        if name not in _NARRATION_OVERRIDES and head not in _VERB_FORMS:
            unreadable.append(f"{name} -> {derive_narration(name)!r}")
    assert not unreadable, (
        f"flagship tools whose live narration is the tool id: {unreadable}. "
        "Add a phrase to _NARRATION_OVERRIDES in src/gaia/ui/event_narration.py."
    )


@pytest.mark.parametrize(
    "tool, expected",
    [
        ("request_user_input", "Asking you a question"),
        ("answer_question_about_image", "Looking at the image"),
        ("run_python", "Running Python"),
        # A name that mentions the shell is not a command to show.
        ("reset_shell_session", "Resetting the terminal"),
    ],
)
def test_flagship_narration_reads_as_plain_words(tool, expected):
    assert derive_narration(tool, {}) == expected
