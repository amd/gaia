# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Text the user hands over to work on, kept apart from what they ask.

``Translate to Spanish: "Please save the report in D:\\r.docx"`` asks for a
translation. The save and the path belong to the sentence being translated,
so a check that reads the request for files to open, saves owed or commands
to run reads only :func:`instruction_part`.
"""

from __future__ import annotations

import re

_VERB = r"^\s*(?:please\s+)?(?:translate|rephrase|reword|paraphrase|proofread)\b"

#: "Translate this sentence: <text>". The colon ends a clause, so the colon
#: of a drive letter or a URL ("D:\x", "https://") does not count.
_TEXT_AFTER_COLON = re.compile(_VERB + r"[^\n]*?:(?=\s|[\"'“”«]|$)", re.IGNORECASE)
#: "Translate this file: D:\x.txt", "save it to: D:\y.txt": the colon
#: introduces a file, not text.
_TARGET_BEFORE_COLON = re.compile(
    r"\b(?:files?|documents?|docs?|paths?|folders?|director(?:y|ies)|to|into|as|in|at)"
    r"\s*:$",
    re.IGNORECASE,
)

#: "Translate this sentence to French": the verb, what it acts on, a language.
_BARE_INSTRUCTION = (
    _VERB + r"(?:\s+(?:this|(?:the\s+)?following|the|my)"
    r"(?:\s+(?:sentence|text|phrase|line|paragraph|message|quote))?)?"
    r"(?:\s+(?:in)?to\s+[^\W\d_]+)?\s*"
)

#: 'Translate "<text>" to French': the quoted span right after the verb.
_QUOTED_TEXT = re.compile(
    _BARE_INSTRUCTION
    + r"(?P<quote>\"(?P<a>[^\"\n]+)\"|“(?P<b>[^”\n]+)”|«(?P<c>[^»\n]+)»)",
    re.IGNORECASE,
)

#: The quoted passage that opens the text after the colon.
_LEADING_QUOTE = re.compile(r"\s*(?:\"[^\"\n]+\"|“[^”\n]+”|«[^»\n]+»)")

#: A file name, path or web page that opens the text after the colon.
_LEADING_FILE = re.compile(
    r"\s*(?:https?://\S+"
    r"|[^\s\"'“«]*[\w)]\.[A-Za-z][A-Za-z0-9]{1,4}(?=[,;.!?]?(?:\s|$)))"
)

#: A file name in the instruction itself: the text to work on is that file.
_NAMES_FILE = re.compile(r"[\w)]\.[A-Za-z][A-Za-z0-9]{1,4}(?=[\s,;:]|$)")

#: A plain word, which a path or file name never is.
_WORD = re.compile(r"[^\W\d_][^\W\d_'’-]*[,.;:!?]*")

#: "Quarterly Sales Report.docx": words, but a file name.
_ENDS_IN_EXTENSION = re.compile(r"\.[A-Za-z][A-Za-z0-9]{1,4}$")


def _is_prose(text: str) -> bool:
    """Whether *text* is a sentence, not a file name or path."""
    return sum(1 for word in text.split() if _WORD.fullmatch(word)) >= 2


def _is_quoted_prose(text: str) -> bool:
    """Whether quoted *text* is a sentence, not a quoted file name or path."""
    return not _ENDS_IN_EXTENSION.search(text.strip()) and _is_prose(text)


def instruction_part(request: str) -> str:
    """*request* without the text it hands over to translate, reword or proofread.

    A file the instruction itself names stays: "Translate README.md to
    French" and "Translate D:\\a.txt and save it as D:\\b.txt" are returned
    whole. Any other request is returned unchanged. Unquoted text after a
    colon is cut whole, a save typed after it included: the two can't be told
    apart without guessing.
    """
    request = request or ""
    colon = _TEXT_AFTER_COLON.match(request)
    if (
        colon
        and not _TARGET_BEFORE_COLON.search(colon.group())
        and not _NAMES_FILE.search(colon.group())
    ):
        tail = request[colon.end() :]
        quote = _LEADING_QUOTE.match(tail)
        if quote and not _is_quoted_prose(quote.group().strip()[1:-1]):
            # A quoted file name or path, not text.
            return request
        if quote:
            # What follows the quoted text is the user's own instruction again.
            rest = tail[quote.end() :]
            return request[: colon.end()] + (rest if re.search(r"\w", rest) else "")
        if tail.strip() and (_LEADING_FILE.match(tail) or not _is_prose(tail)):
            # "Translate this file to French: notes.txt" names a file.
            return request
        return request[: colon.end()]
    quoted = _QUOTED_TEXT.match(request)
    if quoted and _is_quoted_prose(
        quoted.group("a") or quoted.group("b") or quoted.group("c")
    ):
        return request[: quoted.start("quote")] + request[quoted.end("quote") :]
    return request


#: An instruction that asks for more than text: a file, a save, a command.
_ASKS_FOR_WORK = re.compile(
    r"[\/]|\b[\w-]+\.[A-Za-z][A-Za-z0-9]{1,4}\b"
    r"|\b(?:save|write|export|store|put|run|execute|shell|terminal|command|"
    r"files?|folders?|director(?:y|ies)|documents?|open|search|browse|"
    r"download|email|send)\b",
    re.IGNORECASE,
)

#: "…, do nothing else", "…and reply with only the translation": asks for
#: nothing beyond the reply.
_REPLY_ONLY = (
    r"(?:,?\s*(?:and\s+)?(?:do\s+nothing\s+else"
    r"|(?:reply|respond|answer)(?:\s+with)?\s+(?:only|just)"
    r"(?:\s+(?:the|your))?(?:\s+(?:translation|result|text|answer))?)\s*)*"
)

#: All that may come before the colon: the bare instruction, kept to the reply.
_ONLY_THE_INSTRUCTION = re.compile(
    _BARE_INSTRUCTION + _REPLY_ONLY + r":", re.IGNORECASE
)

#: All that may follow the quoted text: a target language and an end mark.
_ONLY_A_LANGUAGE = re.compile(
    r"\s*(?:(?:in)?to\s+[^\W\d_]+)?\s*[.!?]?\s*", re.IGNORECASE
)

#: What a declined tool call is told, so the model answers in its reply.
TEXT_ONLY_DECLINE = (
    "Not run: this request hands over text to translate, reword or proofread "
    "and asks for nothing else, so no command or file change is part of it. "
    "Write the result in your reply."
)


def hands_over_text_only(request: str) -> bool:
    """Whether *request* asks only for text work on quoted text it supplies.

    False for any request whose own instruction names a file, a save or a
    command: that work was asked for. False too when the text is not quoted,
    or when anything but the verb and a target language surrounds the quote:
    that may be work no word list foresees, so the user is asked.
    """
    request = request or ""
    colon = _TEXT_AFTER_COLON.match(request)
    if colon and not _TARGET_BEFORE_COLON.search(colon.group()):
        if not _ONLY_THE_INSTRUCTION.fullmatch(colon.group()):
            return False
        quote = _LEADING_QUOTE.match(request[colon.end() :])
        after = request[colon.end() + quote.end() :] if quote else None
    else:
        quote = _QUOTED_TEXT.match(request)
        after = request[quote.end("quote") :] if quote else None
    if after is None or not _ONLY_A_LANGUAGE.fullmatch(after):
        return False
    asked = instruction_part(request)
    return asked != request and not _ASKS_FOR_WORK.search(asked)
