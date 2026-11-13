# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Neither search guidance surface teaches a query Outlook rejects (#3587).

The registered ``search_messages`` description and the agent's SEARCH prompt
both recommended ``label:promotions``, scoped ``after:``/``before:`` without a
backend, and gave a ``from:`` plus ``is:unread``/``newer_than:`` example as the
way to combine operators. On Outlook each of those raises, so a model shaping
a query from its own instructions failed the most natural form it was taught.
The guidance now names the backend each form works on. These tests read both
LLM-facing surfaces, not copies of their wording, so a revert fails here
instead of only in production.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# parents[0] = tests/, [1] = python/, [2] = email/, [3] = agents/, [4] = hub/,
# [5] = repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.agent import _SYSTEM_PROMPT  # noqa: E402
from gaia_agent_email.outlook_query import translate_query  # noqa: E402
from gaia_agent_email.tools.read_tools import ReadToolsMixin  # noqa: E402

from gaia.agents.base.tools import _TOOL_REGISTRY  # noqa: E402


class _Host(ReadToolsMixin):
    """Minimal stand-in for EmailTriageAgent's tool-hosting surface."""

    def __init__(self):
        self._gmail = None
        self._backends = {}
        self._message_mailbox = {}
        self.config = SimpleNamespace(debug=False)


def _tool_description() -> str:
    """The text the model receives, exactly as ``tools.py`` stores it."""
    _TOOL_REGISTRY.clear()
    _Host()._register_read_tools()
    assert "search_messages" in _TOOL_REGISTRY
    return _TOOL_REGISTRY["search_messages"]["description"]


# ---------------------------------------------------------------------------
# Registered tool description
# ---------------------------------------------------------------------------


def test_description_gives_outlook_the_two_search_form_it_accepts():
    description = _tool_description()

    assert "On Outlook" in description
    assert "cannot combine" in description
    assert '``"from:boss@example.com"``' in description
    assert '``"is:unread newer_than:7d"``' in description
    assert "two separate searches" in description


def test_label_promotions_is_no_longer_status_or_recency_advice():
    description = _tool_description()

    assert not re.search(
        r"status / recency[^\n]*\n\s*``label:promotions``", description
    ), "status/recency still maps to an operator Outlook rejects"
    # It may still be named, but only as a Gmail-only option.
    assert re.search(
        r"\([^)]*Gmail[^)]*``label:promotions``[^)]*\)", description
    ), "label:promotions is offered without a Gmail qualifier"


def test_date_example_is_scoped_to_gmail_and_outlook_gets_durations():
    description = _tool_description()

    assert re.search(
        r"On Gmail, combine them.*?after:2026/07/01", description, re.S
    ), "the absolute-date example is not scoped to Gmail"
    assert re.search(
        r"On Outlook, use\s+the ``newer_than:`` / ``older_than:`` durations",
        description,
    ), "Outlook is not pointed at the durations it accepts"


# ---------------------------------------------------------------------------
# Agent SEARCH prompt
# ---------------------------------------------------------------------------


def test_prompt_states_the_outlook_mixing_rule_and_durations():
    assert re.search(
        r"On Outlook, never combine\s+``from:``/``subject:``", _SYSTEM_PROMPT
    )
    assert re.search(r"run\s+them as two searches", _SYSTEM_PROMPT)
    assert re.search(
        r"``newer_than:``/``older_than:`` rather\s+than\s+" r"``after:``/``before:``",
        _SYSTEM_PROMPT,
    )


def test_prompt_no_longer_maps_status_recency_to_label_promotions():
    assert re.search(
        r"\(on\s+Gmail, ``label:promotions`` also works\)", _SYSTEM_PROMPT
    ), "label:promotions is offered without a Gmail qualifier"
    assert not re.search(
        r"``newer_than:7d``\s*/\s*\n?``label:promotions``", _SYSTEM_PROMPT
    ), "status/recency still maps to an operator Outlook rejects"


# ---------------------------------------------------------------------------
# The alternatives the guidance names are the ones the translator accepts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "from:boss@example.com",
        "subject:invoice",
        "is:unread newer_than:7d",
        "is:read older_than:30d",
    ],
)
def test_the_outlook_examples_in_the_guidance_translate(query):
    assert translate_query(query) is not None


def test_the_mixed_query_the_guidance_no_longer_teaches_still_raises():
    with pytest.raises(ValueError, match="cannot be combined"):
        translate_query("from:boss@example.com is:unread newer_than:7d")
