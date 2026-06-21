# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""WordCountAgent — a GAIA agent with exactly one tool.

This is a *reference example* that shows the tool pattern: the LLM reads the
user's request, decides to call ``count_text``, the framework runs the Python
function, and the LLM turns the result into a natural-language answer.

Two ideas worth copying
-----------------------
1. **Keep tool logic in a module-level pure function** (:func:`count_text_stats`).
   The ``@tool``-decorated wrapper inside :meth:`WordCountAgent._register_tools`
   just calls it and serializes the result. This keeps the logic unit-testable
   without constructing the Agent (which needs an LLM client).
2. **The docstring IS the tool schema.** The framework parses the wrapper's
   docstring and signature to tell the LLM what the tool does and what
   arguments it takes — so write it for the model, not just for humans.

To add a tool to your own agent: write a pure function, add an ``@tool``
wrapper in ``_register_tools``, mention it in the system prompt, and call
``self._snapshot_tools()`` at the end.
"""

import json
import re
from typing import Dict, Optional

from gaia.agents.base import Agent
from gaia.agents.base.tools import tool

_SYSTEM_PROMPT = """\
You are GAIA's Word Count agent. You answer questions about the size of a
piece of text the user gives you.

You have one tool:
- count_text(text) — returns word, character, sentence, and line counts for
  the given text as JSON.

Behavior:
- When the user gives you text to measure, call count_text with that exact
  text. Do not estimate counts yourself.
- Report the numbers the tool returns in a short, friendly sentence.
- If the user asks something the tool can't answer, say so plainly.
"""


def count_text_stats(text: str) -> Dict[str, int]:
    """Return word/character/sentence/line counts for ``text``.

    Pure function so it can be unit-tested without an LLM or the Agent class.

    Args:
        text: The text to measure.

    Returns:
        A dict with ``words``, ``characters``, ``characters_no_spaces``,
        ``sentences``, and ``lines``.
    """
    text = text or ""
    words = len(text.split())
    # A "sentence" ends in . ! or ? — count runs of those terminators so
    # "Wow!!!" counts once. Empty text has zero sentences.
    sentences = len(re.findall(r"[.!?]+", text)) if text.strip() else 0
    lines = len(text.splitlines()) if text else 0
    return {
        "words": words,
        "characters": len(text),
        "characters_no_spaces": len(re.sub(r"\s", "", text)),
        "sentences": sentences,
        "lines": lines,
    }


class WordCountAgent(Agent):
    """A minimal tool-using agent with a single ``count_text`` tool."""

    AGENT_ID = "word-count"
    AGENT_NAME = "Word Count"
    AGENT_DESCRIPTION = "Single-tool reference agent — demonstrates the @tool decorator"
    CONVERSATION_STARTERS = [
        "Count the words in: the quick brown fox",
        "How many sentences are in this paragraph?",
    ]

    DEFAULT_MODEL = "Gemma-4-E4B-it-GGUF"

    def __init__(self, model_id: Optional[str] = None, **kwargs):
        self.response_mode = "conversational"
        super().__init__(model_id=model_id or self.DEFAULT_MODEL, **kwargs)

    def _get_system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def _register_tools(self) -> None:
        @tool
        def count_text(text: str) -> str:
            """Count the words, characters, sentences, and lines in some text.

            Args:
                text: The text to measure.

            Returns:
                A JSON string with keys ``words``, ``characters``,
                ``characters_no_spaces``, ``sentences``, and ``lines``.
            """
            return json.dumps(count_text_stats(text))

        # Freeze this agent's tools so they don't leak into other agents
        # running in the same process.
        self._snapshot_tools()
