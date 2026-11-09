"""Emoji reactions in chat-app replies.

A reply may end with one ``[react: <emoji>]`` line. A chat adapter that
supports reactions puts that emoji on the user's message and posts the rest
as the reply; a reaction with nothing else to say posts no reply at all, the
way a person answers "thanks!" with a heart.
"""

import re
from typing import List, Optional, Tuple

#: Told to the agent on surfaces that render emoji and reactions.
CHAT_STYLE_NOTE = (
    "This is a chat app: write the way a person texts \u2014 short, plain, "
    "contractions. Emoji go ON the user's message as a reaction, not in your "
    "text: when their message carries feeling (good news, thanks, a joke, "
    "frustration), end your reply with `[react: <one emoji>]` alone on the "
    "last line, e.g. `[react: \U0001f389]` for a win, `[react: \u2764\ufe0f]` "
    "for thanks, `[react: \U0001f602]` for a joke. Most replies get none: "
    "never react to a plain question, request or retry. A reaction alone is "
    'a full answer to thanks or an "ok" that needs no words.'
)

# One emoji: a pictograph, optionally with a variation selector or skin tone,
# joined by ZWJ to more of the same; or a keycap; or a flag pair.
_PICTO = (
    "[\u2600-\u27bf\u2b00-\u2bff\u2190-\u21ff\u2300-\u23ff\u3030\u303d"
    "\u3297\u3299\u00a9\u00ae\u203c\u2049\u2122\u2139\U0001f000-\U0001faff]"
)
_MOD = "[\ufe0f\U0001f3fb-\U0001f3ff]*"
_EMOJI = re.compile(
    f"(?:{_PICTO}{_MOD}(?:\u200d{_PICTO}{_MOD})*"
    "|[0-9#*]\ufe0f?\u20e3"
    "|[\U0001f1e6-\U0001f1ff]{2})"
)

# What reads as an emoji in prose. Arrows and (c)/(tm) stay: "Settings -> X".
_PROSE_PICTO = "[\u2600-\u27bf\U0001f000-\U0001faff]"
_PROSE_EMOJI = re.compile(
    f"{_PROSE_PICTO}{_MOD}(?:\u200d{_PROSE_PICTO}{_MOD})*|[\U0001f1e6-\U0001f1ff]{{2}}"
)
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([.,!?;:])")
_RUN_OF_SPACES = re.compile(r"[ \t]{2,}")
_REACT_LINE = re.compile(r"^\s*\[react:\s*(?P<emoji>[^\]\s]+)\s*\]\s*$", re.I)


def is_single_emoji(text: str) -> bool:
    return bool(_EMOJI.fullmatch(text))


def split_reaction(answer: str) -> Tuple[Optional[str], str]:
    """Return ``(emoji, text)``: the reaction asked for and the reply without it.

    Only a last line is a reaction, so a ``[react: ...]`` quoted mid-answer
    stays text. A marker naming something that is not one emoji is removed
    and ignored: it would otherwise reach the user as a stray tag.
    """
    lines = answer.rstrip().split("\n")
    match = _REACT_LINE.match(lines[-1]) if lines else None
    if not match:
        return None, answer
    text = "\n".join(lines[:-1]).rstrip()
    emoji = match.group("emoji")
    return (emoji if is_single_emoji(emoji) else None), text


def take_reaction(answer: str) -> Tuple[Optional[str], str]:
    """The reaction for the user's message, and the reply with no emoji in it.

    On a surface with reactions the emoji belongs on the user's message, not
    in the reply text. An explicit ``[react: ...]`` wins; otherwise the first
    emoji the reply used becomes the reaction. Code is left as written.
    """
    emoji, text = split_reaction(answer)
    found: List[str] = []

    def strip(segment: str) -> str:
        def drop(match: "re.Match[str]") -> str:
            found.append(match.group(0).strip())
            return ""

        # An emoji between a word and a capital was that sentence's full stop.
        segment = re.sub(
            rf"(?<=\w)[ \t]*(?:{_PROSE_EMOJI.pattern})(?=[ \t]+[A-Z])",
            lambda m: drop(m) + ".",
            segment,
        )
        segment = _SPACE_BEFORE_PUNCT.sub(r"\1", _PROSE_EMOJI.sub(drop, segment))
        return _RUN_OF_SPACES.sub(" ", segment)

    parts, end = [], 0
    for code in _CODE.finditer(text):
        parts.append(strip(text[end : code.start()]))
        parts.append(code.group(0))
        end = code.end()
    parts.append(strip(text[end:]))
    cleaned = "\n".join(line.rstrip() for line in "".join(parts).split("\n"))
    return emoji or (found[0] if found else None), cleaned.strip()
