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

# What renders as an emoji in prose: a character shown as emoji by default, or
# any pictograph asking for it with VS16. Ticks, boxes, stars and arrows
# (U+2713, U+2610, U+2605, U+279C) are text, so a checklist keeps its marks.
_DEFAULT_EMOJI = (
    "[\u231a\u231b\u23e9-\u23ec\u23f0\u23f3\u25fd\u25fe\u2614\u2615\u2648-\u2653"
    "\u267f\u2693\u26a1\u26aa\u26ab\u26bd\u26be\u26c4\u26c5\u26ce\u26d4\u26ea"
    "\u26f2\u26f3\u26f5\u26fa\u26fd\u2705\u270a\u270b\u2728\u274c\u274e"
    "\u2753-\u2755\u2757\u2795-\u2797\u27b0\u27bf\u2b1b\u2b1c\u2b50\u2b55"
    "\U0001f004\U0001f0cf\U0001f18e\U0001f191-\U0001f19a\U0001f201\U0001f21a"
    "\U0001f22f\U0001f232-\U0001f236\U0001f238-\U0001f23a\U0001f250\U0001f251"
    "\U0001f300-\U0001f64f\U0001f680-\U0001f6ff\U0001f7e0-\U0001f7eb"
    "\U0001f900-\U0001faff]"
)
_PROSE_PICTO = f"(?:{_PICTO}\ufe0f|{_DEFAULT_EMOJI})"
_PROSE_EMOJI = re.compile(
    f"{_PROSE_PICTO}{_MOD}(?:\u200d{_PICTO}{_MOD})*|[\U0001f1e6-\U0001f1ff]{{2}}"
)
_ONLY_EMOJI = re.compile(f"(?:[ \t]|{_PROSE_EMOJI.pattern})+")
# Code (an unclosed fence runs to the end) and links are left as written.
_KEEP = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`|https?://\S+", re.S)
# Where a line's content starts: indent, quote marks, a list marker.
_LINE_LEAD = re.compile(r"[ \t]*(?:>[ \t]*)*(?P<bullet>(?:[-*+]|\d+[.)])[ \t]+)?")
_REACT_LINE = re.compile(r"^\s*\[react:[ \t]*(?P<emoji>[^\]\n]*?)[ \t]*\]\s*$", re.I)

#: Status marks: what they say is the reply's content, so they stay in it.
_STATUS_MARKS = frozenset(
    "\u2705\u274c\u274e\u26a0\u2714\u2716\u2b55"
    "\U0001f7e2\U0001f7e1\U0001f7e0\U0001f534\U0001f7e9\U0001f7e5"
)
#: Words a model writes in a marker to mean no reaction at all.
_NO_REACTION = frozenset({"", "none", "no", "nothing", "no reaction", "n/a", "-"})

#: What a reaction marker that names no emoji gets: it still asked to acknowledge.
ACKNOWLEDGE = "\U0001f44d"
_SHORTCODES = {
    "+1": "\U0001f44d",
    "thumbsup": "\U0001f44d",
    "thumbs_up": "\U0001f44d",
    "heart": "\u2764\ufe0f",
    "red_heart": "\u2764\ufe0f",
    "tada": "\U0001f389",
    "party": "\U0001f389",
    "joy": "\U0001f602",
    "laughing": "\U0001f606",
    "smile": "\U0001f604",
    "slightly_smiling_face": "\U0001f642",
    "pray": "\U0001f64f",
    "clap": "\U0001f44f",
    "fire": "\U0001f525",
    "wave": "\U0001f44b",
    "eyes": "\U0001f440",
    "100": "\U0001f4af",
    "white_check_mark": "\u2705",
    "check": "\u2705",
}


def is_single_emoji(text: str) -> bool:
    return bool(_EMOJI.fullmatch(text))


def split_reaction(answer: str) -> Tuple[Optional[str], str]:
    """Return ``(emoji, text)``: the reaction asked for and the reply without it.

    Only a last line is a reaction, so a ``[react: ...]`` quoted mid-answer
    stays text. A marker is always removed, since it would otherwise reach the
    user as a stray tag: a shortcode (``:tada:``) becomes its emoji, and one
    that names nothing readable becomes :data:`ACKNOWLEDGE`.
    """
    lines = answer.rstrip().split("\n")
    match = _REACT_LINE.match(lines[-1]) if lines else None
    if not match:
        return None, answer
    text = "\n".join(lines[:-1]).rstrip()
    named = match.group("emoji").strip()
    if named.lower().strip(":").replace("_", " ") in _NO_REACTION:
        return None, text
    if is_single_emoji(named):
        return named, text
    code = re.sub(r"[\s-]+", "_", named.strip(":").lower())
    return _SHORTCODES.get(code, ACKNOWLEDGE), text


def _strip_line(
    line: str,
    kept: List[Tuple[int, int]],
    at: int,
    found: List[str],
    column: bool,
) -> Optional[str]:
    """*line* without its prose emoji (appended to *found*), or None to drop it."""
    if line.lstrip().startswith("|"):
        return line
    hits = [
        m
        for m in _PROSE_EMOJI.finditer(line)
        if not any(a <= at + m.start() < b for a, b in kept)
        and m.group(0)[0] not in _STATUS_MARKS
    ]
    if not hits:
        return line
    if _ONLY_EMOJI.fullmatch(line) and len(hits) == len(_PROSE_EMOJI.findall(line)):
        found.extend(m.group(0) for m in hits)
        return None
    opening = _LINE_LEAD.match(line)
    lead = opening.end()
    marks = column or bool(opening.group("bullet"))
    out, end = [], 0
    for m in hits:
        start, stop = m.span()
        if start < end:
            continue
        opens = not _PROSE_EMOJI.sub("", line[lead:start]).strip()
        if marks and opens:
            # A mark opening a list item, or a column of them, is a bullet.
            continue
        found.append(m.group(0))
        before = line[end:start]
        if opens:
            rest = line[stop:]
            out.append(before)
            end = stop + len(rest) - len(rest.lstrip(" \t"))
            continue
        prefix = before.rstrip(" \t")
        spaced = prefix != before or (
            not before and bool(out) and out[-1].endswith((" ", "\t"))
        )
        rest = line[stop:]
        trail = len(rest) - len(rest.lstrip(" \t"))
        nxt = rest[trail : trail + 1]
        if not nxt or nxt in ".,!?;:":
            if not before and out:
                out[-1] = out[-1].rstrip(" \t")
            out.append(prefix)
            end = stop + trail
        elif spaced and trail and not before:
            # The space before this emoji is already out: drop the one after.
            end = stop + trail
        elif spaced and trail:
            # An emoji between a word and a capital was that sentence's full
            # stop; after a one-letter word ("I <heart> it") it was a verb.
            stop_here = nxt.isupper() and re.search(r"\w\w$", prefix)
            out.append(prefix + "." if stop_here else prefix)
            end = stop
        else:
            out.append(before)
            end = stop
    out.append(line[end:])
    return "".join(out)


def take_reaction(answer: str) -> Tuple[Optional[str], str]:
    """The reaction for the user's message, and the reply with no emoji in it.

    On a surface with reactions the emoji belongs on the user's message, not
    in the reply text. An explicit ``[react: ...]`` wins; otherwise the first
    emoji the reply used becomes the reaction. Code, links, table rows and an
    emoji that marks a list item are left as written, and so is spacing that
    no removed emoji touched.
    """
    emoji, text = split_reaction(answer)
    kept = [m.span() for m in _KEEP.finditer(text)]
    found: List[str] = []
    rows = text.split("\n")
    column = (
        sum(bool(_PROSE_EMOJI.match(r, _LINE_LEAD.match(r).end())) for r in rows) > 1
    )
    lines, at = [], 0
    for line in rows:
        cleaned = _strip_line(line, kept, at, found, column)
        if cleaned is not None:
            lines.append(cleaned)
        at += len(line) + 1
    return emoji or (found[0] if found else None), "\n".join(lines).strip("\n").rstrip()
