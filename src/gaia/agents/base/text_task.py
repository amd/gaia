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

#: 'Translate "<text>" to French': the quoted span right after the verb.
_QUOTED_TEXT = re.compile(
    _VERB + r"(?:\s+(?:this|the|following|my)"
    r"(?:\s+(?:sentence|text|phrase|line|paragraph|message|quote))?)?"
    r"(?:\s+(?:in)?to\s+[^\W\d_]+)?\s*"
    r"(?P<quote>\"(?P<a>[^\"\n]+)\"|“(?P<b>[^”\n]+)”|«(?P<c>[^»\n]+)»)",
    re.IGNORECASE,
)

#: The quoted passage that opens the text after the colon.
_LEADING_QUOTE = re.compile(r"\s*(?:\"[^\"\n]+\"|“[^”\n]+”|«[^»\n]+»)")

#: A file name or path that opens the text after the colon.
_LEADING_FILE = re.compile(r"\s*[^\s\"'“«]*[\w)]\.[A-Za-z][A-Za-z0-9]{1,4}(?=\s|$)")

#: A plain word, which a path or file name never is.
_WORD = re.compile(r"[^\W\d_][^\W\d_'’-]*[,.;:!?]*")


def _is_prose(text: str) -> bool:
    """Whether quoted *text* is a sentence, not a quoted file name or path."""
    return sum(1 for word in text.split() if _WORD.fullmatch(word)) >= 2


def instruction_part(request: str) -> str:
    """*request* without the text it hands over to translate, reword or proofread.

    A file the instruction itself names stays: "Translate README.md to
    French" and "Translate D:\\a.txt and save it as D:\\b.txt" are returned
    whole. Any other request is returned unchanged.
    """
    request = request or ""
    colon = _TEXT_AFTER_COLON.match(request)
    if colon and not _TARGET_BEFORE_COLON.search(colon.group()):
        tail = request[colon.end() :]
        quote = _LEADING_QUOTE.match(tail)
        if quote:
            # What follows the quoted text is the user's own instruction again.
            rest = tail[quote.end() :]
            return request[: colon.end()] + (rest if re.search(r"\w", rest) else "")
        if tail.strip() and (_LEADING_FILE.match(tail) or not _is_prose(tail)):
            # "Translate this file to French: notes.txt" names a file.
            return request
        return request[: colon.end()]
    quoted = _QUOTED_TEXT.match(request)
    if quoted and _is_prose(
        quoted.group("a") or quoted.group("b") or quoted.group("c")
    ):
        return request[: quoted.start("quote")] + request[quoted.end("quote") :]
    return request
