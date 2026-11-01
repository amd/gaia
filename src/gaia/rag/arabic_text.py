# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Clean up Arabic text extracted from PDFs so it matches what people type."""

import re
import unicodedata

# Arabic Presentation Forms-A and -B: per-position glyph codes many PDF writers
# (Word, Chrome) emit instead of base letters.
_PRESENTATION_FORMS = re.compile("[ﭐ-﷿ﹰ-﻾]+")

_ARABIC_YEH = "ي"
_SHARED_GLYPH_LETTERS = str.maketrans({"ی": _ARABIC_YEH, "ھ": "ه"})

# The Arabic block holds digits too; those read left to right.
_ARABIC_LETTER = re.compile("[؀-ٟ٪-ۯۺ-ۿﭐ-﷿ﹰ-﻾]")
_LATIN_LETTER = re.compile("[A-Za-z]")
_LTR_RUN = re.compile(r"[0-9A-Za-z٠-٩۰-۹]")

_CLOSING_MARKS = ".,:;!?%)،؛؟٪"
_SHOW_TEXT_OPERATORS = (b"Tj", b"TJ", b"'", b'"')

# Points two text pieces' baselines can differ by and still sit on one line.
_SAME_LINE_PT = 2.0


def extract_pdf_page_text(page) -> str:
    """A pypdf page's text, with right-to-left lines put in reading order."""
    pieces = []
    starts = []
    in_show = [False]

    def before_operator(operator, _operands, cm, tm):
        in_show[0] = operator in _SHOW_TEXT_OPERATORS
        if in_show[0]:
            starts.append(_device_position(cm, tm))

    # pypdf's own text position lags a piece behind, so take it from the operator.
    def collect(text, _cm, _tm, _font, _size):
        # A piece is flushed as the next show operator starts; that one is not in it.
        finished = starts[:-1] if in_show[0] else starts[:]
        del starts[: len(finished)]
        if text.strip() and finished:
            pieces.append((*finished[0], text))

    text = (
        page.extract_text(visitor_operand_before=before_operator, visitor_text=collect)
        or ""
    )
    return _repair_visual_order_lines(text, pieces) if pieces else text


def _device_position(cm, tm) -> tuple:
    return (
        tm[4] * cm[0] + tm[5] * cm[2] + cm[4],
        tm[4] * cm[1] + tm[5] * cm[3] + cm[5],
    )


def _repair_visual_order_lines(text: str, pieces: list) -> str:
    """Reverse the lines a PDF writer stored left to right on the page.

    Chrome and InDesign store an Arabic line's runs in on-screen order, so the
    text reads from its last clause and a number lands beside the wrong words.
    """
    start = 0
    for end in range(1, len(pieces) + 1):
        if end < len(pieces) and abs(pieces[end][1] - pieces[start][1]) < _SAME_LINE_PT:
            continue
        line = pieces[start:end]
        start = end
        if not _is_visual_order(line):
            continue
        stored = r"\s*".join(re.escape(piece[2].strip()) for piece in line)
        # Escaped so backslashes in the text can't read as group refs.
        reading = _reading_order(line).replace("\\", r"\\")
        text = re.sub(stored, reading, text, count=1)
    return text


def _is_visual_order(line: list) -> bool:
    """True when every step between Arabic and a number moves left to right."""
    joined = "".join(piece[2] for piece in line)
    if len(_ARABIC_LETTER.findall(joined)) <= len(_LATIN_LETTER.findall(joined)):
        return False

    previous_x = None
    previous_arabic = False
    moved_right = False
    for x, _y, piece_text in line:
        arabic = bool(_ARABIC_LETTER.search(piece_text))
        # Quote marks and full stops sit either side of a run in any writer.
        if not arabic and not _LTR_RUN.search(piece_text):
            continue
        if previous_x is not None and arabic != previous_arabic:
            if x < previous_x:
                return False
            moved_right = True
        previous_x, previous_arabic = x, arabic
    return moved_right


def _reading_order(line: list) -> str:
    """Join a visual-order line right to left, keeping numbers left to right."""
    parts: list[str] = []
    for index, (_x, _y, raw) in enumerate(line):
        piece = raw.strip()
        previous = line[index - 1][2] if index else ""
        following = line[index + 1][2] if index + 1 < len(line) else ""
        in_number = (
            not _LTR_RUN.search(piece)
            and not _ARABIC_LETTER.search(piece)
            and _LTR_RUN.search(previous)
            and _LTR_RUN.search(following)
            and not _ARABIC_LETTER.search(previous + following)
        )
        continues_number = (
            parts
            and _LTR_RUN.search(piece)
            and not _ARABIC_LETTER.search(piece + parts[-1])
            and _LTR_RUN.search(parts[-1])
        )
        if in_number or continues_number:
            parts[-1] += piece
        else:
            parts.append(piece)

    joined = ""
    for part in reversed(parts):
        joined += part if not joined or part[0] in _CLOSING_MARKS else f" {part}"
    return joined


def normalize_arabic_pdf_text(text: str) -> str:
    """Map Arabic presentation-form glyphs to base letters (U+FEFB -> "لا").

    Only those code points are touched; NFKC on the whole string would also
    rewrite unrelated characters such as "²" or "ﬁ". Fonts share one glyph for
    Arabic and Persian medial yeh and heh, so some writers (Chrome) report the
    Persian letter mid-word; a page holding an Arabic yeh is not Persian, so
    those letters are put right there and left alone on Persian pages.
    """
    text = _PRESENTATION_FORMS.sub(
        lambda m: unicodedata.normalize("NFKC", m.group()), text
    )
    return text.translate(_SHARED_GLYPH_LETTERS) if _ARABIC_YEH in text else text
