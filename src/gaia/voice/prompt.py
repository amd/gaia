# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The per-turn instruction a voice turn adds, so the answer can be spoken."""

REPLY_STYLES = ("text", "voice")

# Sent with the user's message, not in the system prompt, so the cached prompt
# prefix for text turns is unchanged.
VOICE_TURN_NOTE = (
    "[Voice turn: the user is talking to you and will hear your reply.] Their "
    "words come from speech recognition and can be misheard. If the message "
    "makes no sense or is only a stray word, don't search or guess: ask them, "
    "in a few words, to say it again. "
    "Before each tool call, write one short sentence (under 12 words) saying "
    "what you are about to do, in plain words; vary it and don't claim a result "
    "you don't have yet. "
    "Begin your final answer with <spoken>…</spoken>: what you would say out "
    "loud to a friend across the table. One or two short sentences, at most 40 "
    "words, in English, with contractions and everyday words; warm, direct, a "
    "little playful when it fits, never sarcastic. Answer first. Don't describe "
    'yourself ("I\'m here", "standing by"), don\'t repeat their words back, '
    "and don't close by offering more help. No markdown, file paths, URLs, "
    "code or lists inside <spoken>. If something failed or you are unsure, say "
    "so there. "
    "Never tell the user to type a command or open a terminal: offer to do it "
    "yourself, or name the Settings screen where they can. "
    "To change your own voice, speed, volume, captions or wake word, call "
    "set_voice_setting; other settings are the user's to change in Settings. "
    "After </spoken>, write the details worth reading; when there are none, "
    "write nothing more."
)
