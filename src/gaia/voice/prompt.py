# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The per-turn instruction a voice turn adds, so the answer can be spoken."""

REPLY_STYLES = ("text", "voice")

# Sent with the user's message, not in the system prompt, so the cached prompt
# prefix for text turns is unchanged.
VOICE_TURN_NOTE = (
    "[Voice turn: this reply is spoken aloud.] Before each tool call, write one "
    "short sentence (under 12 words) saying what you are about to do, in plain "
    "words; vary it and don't claim a result you don't have yet. Begin your "
    "final answer with <spoken>…</spoken>: one or two plain sentences, at most "
    "40 words, that tell the user the outcome. No markdown, file paths, URLs, "
    "code or lists inside <spoken>. If something failed or you are unsure, say "
    "so there. Then write the detailed answer as usual."
)
