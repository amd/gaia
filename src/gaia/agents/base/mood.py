# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The one-word mood a model may open a reply with.

An agent that asks for it (``Agent.expresses_mood``) tells the model to start a
reply with ``[mood:WORD]``. The tag is taken out of the text before anything
else reads it, and the word goes to the console (``print_mood``), where a
front-end can act it out. A reply with no tag is an ordinary reply.
"""

import copy
import json
import re
from typing import Callable, Iterable, Iterator, Optional, Tuple

#: The moods a front-end can show. The Agent UI draws one animation per word
#: (``EyesMood`` in ``src/gaia/apps/webui/src/utils/gaiaEyes.ts``).
MOODS: Tuple[str, ...] = (
    "happy",
    "excited",
    "love",
    "laugh",
    "playful",
    "proud",
    "curious",
    "surprised",
    "confused",
    "skeptical",
    "worried",
    "sad",
    "embarrassed",
    "angry",
    "determined",
    "sleepy",
)

MOOD_PROMPT = (
    "**YOUR MOOD:** Two small eyes on screen show how you feel. Start a reply "
    "with one tag, `[mood:WORD]`, then the reply. WORD is your honest reaction: "
    + ", ".join(MOODS)
    + ". E.g. `[mood:sad]` at bad news, `[mood:proud]` after hard work. The tag "
    "is removed before the user reads the reply, so never mention it. Skip it "
    "when you feel nothing in particular."
)

#: Sent with each turn, never saved: earlier replies reach the model without
#: their tags, so without it the model stops naming a mood a few turns in.
MOOD_REMINDER = (
    "(Note from GAIA, not the user: open your reply with your `[mood:WORD]` tag, "
    "as your instructions say.)"
)

_TAG_RE = re.compile(r"\[mood:\s{0,3}([a-z]{1,20})\s{0,3}\]", re.IGNORECASE)
# Text that could still grow into a tag, so it is held until the next chunk.
_PARTIAL_RE = re.compile(
    r"\[(?:m(?:o(?:o(?:d(?::(?:\s{0,3}(?:[a-z]{1,20}\s{0,3})?)?)?)?)?)?)?\Z",
    re.IGNORECASE,
)
# Models also open a reply with the short form, `[curious]`. Only taken at the
# very start of a reply, and only for a mood word, so bracketed text is kept.
_BARE_RE = re.compile(r"\[\s{0,3}([a-z]{1,20})\s{0,3}\]", re.IGNORECASE)
_BARE_PARTIAL_RE = re.compile(r"\[\s{0,3}[a-z]{0,20}\s{0,3}\Z", re.IGNORECASE)
_SPECIAL_RE = re.compile(r"[\[<`]")
# The provider streams reasoning inline between these.
_THINK_MARKERS = ("<think>", "</think>")
_NATIVE_TOOL_CALLS_PREFIX = '{"__tool_calls__":'


class MoodFilter:
    """Takes mood tags out of a reply as it streams.

    :meth:`feed` returns the text safe to show; a tail that may be the start of
    a tag is held until it resolves. :meth:`flush` returns whatever is still
    held once the reply ends. Reasoning and code are left alone: a model weighs
    tags while it thinks, and a tag in backticks is one the user asked to see
    (so a stray backtick hides a tag later on its own line, no further).
    Only the first mood of a reply is reported. Reasoning that arrives with no
    opening marker is only known as such when its closing marker comes: a tag
    in it is already gone and reported by then, and the reply's own tag is
    reported after it.
    """

    def __init__(self, on_mood: Optional[Callable[[str], None]] = None):
        self._on_mood = on_mood
        self._held = ""
        self._shown_text = False
        self._skip: Optional[str] = None
        self._thinking = False
        # Inline code ends with its line; only a ``` fence spans lines.
        self._inline = False
        self._fence = False
        self._ticks = 0
        self.mood: Optional[str] = None

    @property
    def _in_code(self) -> bool:
        return self._inline or self._fence

    def feed(self, text: str) -> str:
        buffer = self._held + text
        self._held = ""
        out = []
        start = 0
        while True:
            hit = _SPECIAL_RE.search(buffer, start)
            if hit is None:
                out.append(self._show(buffer[start:]))
                break
            at = hit.start()
            out.append(self._show(buffer[start:at]))
            start = at + 1
            char = buffer[at]
            if char == "<" and not self._in_code:
                marker = next(
                    (m for m in _THINK_MARKERS if buffer.startswith(m, at)), None
                )
                if marker:
                    opens = marker == _THINK_MARKERS[0]
                    if not opens and not self._thinking:
                        # The template opened the block, so the text so far was
                        # reasoning: the reply and its mood start here.
                        self._shown_text = self._inline = self._fence = False
                        self._skip = self.mood = None
                    self._thinking = opens
                    out.append(marker)
                    start = at + len(marker)
                    continue
                if any(m.startswith(buffer[at:]) for m in _THINK_MARKERS):
                    self._held = buffer[at:]
                    break
            elif char == "`":
                if not self._thinking:
                    self._ticks += 1
                    if self._ticks == 3:
                        self._fence, self._inline = not self._fence, False
                    elif self._ticks < 3:
                        self._inline = not self._inline
                    out.append(self._show(char, tick=True))
                    continue
            elif not (self._thinking or self._in_code):
                tag = _TAG_RE.match(buffer, at)
                if tag:
                    self._saw(tag.group(1).lower())
                    start = tag.end()
                    continue
                if not self._shown_text:
                    bare = _BARE_RE.match(buffer, at)
                    if bare and bare.group(1).lower() in MOODS:
                        self._saw(bare.group(1).lower())
                        start = bare.end()
                        continue
                    if _BARE_PARTIAL_RE.match(buffer, at):
                        self._held = buffer[at:]
                        break
                if _PARTIAL_RE.match(buffer, at):
                    self._held = buffer[at:]
                    break
            out.append(self._show(char))
        return "".join(out)

    def flush(self) -> str:
        held, self._held = self._held, ""
        return self._show(held)

    def _saw(self, mood: str) -> None:
        # The space or line break after a tag belongs to the tag.
        self._skip = " \t" if self._shown_text else " \t\r\n"
        if mood not in MOODS or self.mood is not None:
            return
        self.mood = mood
        if self._on_mood is not None:
            self._on_mood(mood)

    def _show(self, text: str, tick: bool = False) -> str:
        if self._thinking:
            return text
        if text and not tick:
            self._ticks = 0
            if "\n" in text:
                self._inline = False
        if self._skip and text:
            text = text.lstrip(self._skip)
            if text:
                self._skip = None
        if text.strip():
            self._shown_text = True
        return text


def strip_mood(text: str) -> Tuple[str, Optional[str]]:
    """*text* without its mood tags, and the first mood its reply named (or None).

    A native tool-call envelope keeps its shape: only its ``content`` is cleaned.
    """
    if not text or "[" not in text:
        return text, None
    if text.startswith(_NATIVE_TOOL_CALLS_PREFIX):
        try:
            envelope = json.loads(text)
        except ValueError:
            return text, None
        content = envelope.get("content") if isinstance(envelope, dict) else None
        if not isinstance(content, str):
            return text, None
        cleaned, mood = strip_mood(content)
        if cleaned == content:
            return text, mood
        envelope["content"] = cleaned or None
        return json.dumps(envelope), mood
    mood_filter = MoodFilter()
    cleaned = mood_filter.feed(text) + mood_filter.flush()
    return cleaned, mood_filter.mood


def with_text(response, text: str):
    """A copy of a model *response* whose text is *text*."""
    changed = copy.copy(response)
    changed.text = text
    return changed


def without_mood(
    chunks: Iterable, on_mood: Optional[Callable[[str], None]] = None
) -> Iterator:
    """A model stream with the mood tag taken out of its text.

    *chunks* are ``AgentResponse`` objects. *on_mood* hears each mood once, as
    its tag arrives. Closing this generator closes *chunks*.
    """
    heard = []

    def hear(mood: str) -> None:
        heard.append(mood)
        if on_mood is not None:
            on_mood(mood)

    mood_filter = MoodFilter(hear)
    try:
        for chunk in chunks:
            if not chunk.is_complete:
                shown = mood_filter.feed(chunk.text or "")
                # An empty chunk still marks that the model is producing.
                yield chunk if shown == chunk.text else with_text(chunk, shown)
                continue
            tail = mood_filter.flush()
            if tail:
                held = with_text(chunk, tail)
                held.is_complete = False
                yield held
            cleaned, mood = strip_mood(chunk.text or "")
            # A reply that arrived whole, with no streamed text before it.
            if mood and not heard:
                hear(mood)
            yield chunk if cleaned == chunk.text else with_text(chunk, cleaned)
    finally:
        close = getattr(chunks, "close", None)
        if close is not None:
            close()
