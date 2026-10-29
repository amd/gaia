# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A tool call being written reaches the Agent UI as a live draft."""

import pytest

from gaia.agents.base.tools import partial_json_string, tool
from gaia.ui.email_sidecar import relay
from gaia.ui.email_sidecar.profiles import EMAIL_PROFILE
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator


@pytest.mark.parametrize(
    "arguments, expected",
    [
        ('{"output', None),
        ('{"markdown": "', ""),
        ('{"markdown": "# Hi\\n## Sli', "# Hi\n## Sli"),
        ('{"markdown":"a\\u2192b\\', "a→b"),
        ('{"markdown":"a\\u21', "a"),
        ('{"markdown":"done", "theme": "x"}', "done"),
    ],
)
def test_the_text_written_so_far_is_read_from_partial_json(arguments, expected):
    assert partial_json_string(arguments, "markdown") == expected


def test_a_draft_argument_must_name_a_parameter():
    with pytest.raises(TypeError, match="draft_argument"):

        @tool(draft_argument="missing", registry={})
        def build(markdown: str) -> str:
            """Build."""
            return markdown


def test_a_draft_crosses_the_sidecar_relay_unchanged():
    source = SSEOutputHandler()
    source.report_draft("slide-decks/build_deck_from_markdown", "# Hi")
    canonical = CanonicalTranslator("run", agent_id="gaia").translate(
        source.event_queue.get_nowait()
    )
    ui = SSEOutputHandler()
    relay._dispatch_one(ui, canonical[0], EMAIL_PROFILE, False)
    assert ui.event_queue.get_nowait() == {
        "type": "draft",
        "tool": "slide-decks/build_deck_from_markdown",
        "text": "# Hi",
    }
