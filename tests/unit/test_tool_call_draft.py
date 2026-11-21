# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A tool call being written reaches the Agent UI as a live draft."""

import json

import pytest

from gaia.agents.base.tools import partial_json_string, tool
from gaia.ui.sidecar import relay
from gaia.ui.sidecar.profiles import GAIA_PROFILE
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
        ('{"markdown":"ok \\ud83d\\ude00!', "ok \U0001f600!"),
        ('{"markdown":"ok \\ud83d', "ok "),
        ('{"markdown":"ok \\ud83d\\ude', "ok "),
        ('{"markdown":"ok \\ud83d and', "ok "),
        ('{"markdown":"ok \\ude00 and', "ok "),
    ],
)
def test_the_text_written_so_far_is_read_from_partial_json(arguments, expected):
    text = partial_json_string(arguments, "markdown")
    assert text == expected
    if text is not None:
        # The stdio transport encodes every draft; a lone surrogate would raise.
        json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")


def test_a_frame_of_only_malformed_tool_call_fragments_sends_no_draft(monkeypatch):
    from unittest.mock import patch

    from gaia.llm.providers import lemonade as lemonade_provider
    from gaia.llm.providers.lemonade import LemonadeProvider

    monkeypatch.setattr(lemonade_provider, "TOOL_CALL_DRAFT_INTERVAL_S", 0.0)
    frames = [
        {"choices": [{"delta": {"tool_calls": ["not-a-fragment"]}}]},
        {"choices": [{"delta": {"content": "hello"}, "finish_reason": "stop"}]},
    ]
    with patch("gaia.llm.providers.lemonade.LemonadeClient") as backend:
        backend.return_value.chat_completions.return_value = iter(frames)
        provider = LemonadeProvider(model="Gemma-4-E4B-it-GGUF")
    drafts = []
    provider.tool_call_draft = lambda tool, arguments: drafts.append(tool)

    out = "".join(provider.chat([{"role": "user", "content": "q"}], stream=True))

    assert drafts == [] and "hello" in out


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
    relay._dispatch_one(ui, canonical[0], GAIA_PROFILE, False)
    assert ui.event_queue.get_nowait() == {
        "type": "draft",
        "tool": "slide-decks/build_deck_from_markdown",
        "text": "# Hi",
    }
