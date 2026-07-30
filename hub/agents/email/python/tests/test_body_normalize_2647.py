# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tests for #2647: extend banner stripping (not just the delimiter scrub,
which #2642/test_body_normalize_2642.py already covers) to the three prompt
builders that build an LLM prompt directly from a raw decoded body —
``summarize_tools._build_user_prompt``, ``llm_triage._build_user_prompt``,
and ``calendar_tools._build_llm_user_prompt``.

Each assertion targets the CONSTRUCTED PROMPT (deterministic), never model
output, per the issue's acceptance criteria.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.tools.calendar_tools import (  # noqa: E402
    _build_llm_user_prompt as _calendar_build_llm_user_prompt,
)
from gaia_agent_email.tools.llm_triage import (  # noqa: E402
    _build_user_prompt as _triage_build_user_prompt,
)
from gaia_agent_email.tools.summarize_tools import (  # noqa: E402
    _build_user_prompt as _summarize_build_user_prompt,
)

_AMD_GENERAL_BANNER = "AMD General"
_EXTERNAL_SOURCE_BANNER = (
    "Caution: This message originated from an External Source. Use proper "
    "caution\nwhen opening attachments, clicking links, or responding."
)
_SUBSTANTIVE_MSG = (
    "Not a problem. Just remember to assign the issue to yourself, and keep "
    "the\npull request in draft until CI is clean."
)


class TestSummarizePromptDropsBanners:
    def test_amd_general_banner_does_not_reach_summarize_prompt(self):
        body = f"{_AMD_GENERAL_BANNER}\n\n{_SUBSTANTIVE_MSG}"
        prompt = _summarize_build_user_prompt("Subject", "dev@example.invalid", body)
        assert _AMD_GENERAL_BANNER not in prompt
        assert "keep the\npull request in draft until CI is clean" in prompt

    def test_external_source_banner_does_not_reach_summarize_prompt(self):
        body = f"{_EXTERNAL_SOURCE_BANNER}\n\nok, great! Thanks so much!"
        prompt = _summarize_build_user_prompt("Subject", "dev@example.invalid", body)
        assert "originated from an External Source" not in prompt
        assert "ok, great! Thanks so much!" in prompt


class TestTriageClassificationPromptDropsBanners:
    def test_amd_general_banner_does_not_reach_triage_prompt(self):
        body = f"{_AMD_GENERAL_BANNER}\n\n{_SUBSTANTIVE_MSG}"
        prompt = _triage_build_user_prompt("Subject", "dev@example.invalid", body)
        assert _AMD_GENERAL_BANNER not in prompt
        assert "keep the\npull request in draft until CI is clean" in prompt

    def test_external_source_banner_does_not_reach_triage_prompt(self):
        body = f"{_EXTERNAL_SOURCE_BANNER}\n\nok, great! Thanks so much!"
        prompt = _triage_build_user_prompt("Subject", "dev@example.invalid", body)
        assert "originated from an External Source" not in prompt
        assert "ok, great! Thanks so much!" in prompt


class TestCalendarMeetingDetectionPromptDropsBanners:
    def test_amd_general_banner_does_not_reach_calendar_prompt(self):
        body = f"{_AMD_GENERAL_BANNER}\n\n{_SUBSTANTIVE_MSG}"
        prompt = _calendar_build_llm_user_prompt("Subject", body)
        assert _AMD_GENERAL_BANNER not in prompt
        assert "keep the\npull request in draft until CI is clean" in prompt

    def test_external_source_banner_does_not_reach_calendar_prompt(self):
        body = f"{_EXTERNAL_SOURCE_BANNER}\n\nok, great! Thanks so much!"
        prompt = _calendar_build_llm_user_prompt("Subject", body)
        assert "originated from an External Source" not in prompt
        assert "ok, great! Thanks so much!" in prompt


class TestHardNegativePreservedAcrossAllThreePaths:
    """A body legitimately ABOUT one of these phrases (not opening with the
    literal banner) must survive intact on every one of the three paths —
    the same hard negative #2642 already proves for the thread path."""

    _BODY = (
        "Quick question - our vendor's mail gateway keeps adding that "
        "'external source' caution banner to every reply. Is there a way to "
        "suppress it for trusted partners?"
    )

    def test_summarize_prompt_preserves_hard_negative(self):
        prompt = _summarize_build_user_prompt(
            "Subject", "dev@example.invalid", self._BODY
        )
        assert self._BODY in prompt

    def test_triage_prompt_preserves_hard_negative(self):
        prompt = _triage_build_user_prompt("Subject", "dev@example.invalid", self._BODY)
        assert self._BODY in prompt

    def test_calendar_prompt_preserves_hard_negative(self):
        prompt = _calendar_build_llm_user_prompt("Subject", self._BODY)
        assert self._BODY in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
