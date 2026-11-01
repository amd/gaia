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
