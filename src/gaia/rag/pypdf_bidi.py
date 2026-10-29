# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Keep pypdf's plain-text extraction from dropping text in mixed-direction lines.

pypdf's ``get_display_str`` hands the text gathered so far to ``visitor_text``
whenever a line switches between right-to-left and left-to-right characters,
then clears it. With no visitor -- the plain ``page.extract_text()`` that RAG
indexes -- that text is simply lost. On an Arabic page every number, Latin
word, and English term mid-sentence wipes the line before it, so "40%" or
"VAT" never reaches the index.

The wrapper below collects what pypdf would have handed to a visitor and puts
it back in front of the returned text. Calls that pass a real visitor keep
pypdf's own behaviour.
"""

import functools
import inspect
import re
import unicodedata

from gaia.logger import get_logger

logger = get_logger(__name__)

_MARKER = "_gaia_keeps_mixed_direction_text"

# Arabic Presentation Forms-A and -B: per-position glyph codes many PDF writers
# (Word, Chrome) emit instead of base letters.
_PRESENTATION_FORMS = re.compile("[\ufb50-\ufdff\ufe70-\ufefe]+")


def normalize_arabic_presentation_forms(text: str) -> str:
    """Map Arabic presentation-form glyphs to base letters (U+FEFB -> "لا").

    Only those code points are touched; NFKC on the whole string would also
    rewrite unrelated characters such as "²" or "ﬁ".
    """
    return _PRESENTATION_FORMS.sub(
        lambda m: unicodedata.normalize("NFKC", m.group()), text
    )


def install() -> None:
    """Patch pypdf once per process; a no-op when already applied."""
    from pypdf._text_extraction import _text_extractor

    original = _text_extractor.get_display_str
    if getattr(original, _MARKER, False):
        return
    params = list(inspect.signature(original).parameters)
    if not params or params[-1] != "visitor_text":
        raise RuntimeError(
            "pypdf changed get_display_str (last parameter is "
            f"{params[-1] if params else None!r}, expected 'visitor_text'), so "
            "Arabic PDF text can't be kept whole. Update gaia/rag/pypdf_bidi.py "
            "for this pypdf version or pin pypdf<6.22 in setup.py."
        )

    @functools.wraps(original)
    def get_display_str(*args, **kwargs):
        if kwargs or args[-1] is not None:
            return original(*args, **kwargs)
        flushed = []
        text, rtl_dir, widths = original(
            *args[:-1], lambda run, *_: flushed.append(run)
        )
        return "".join(flushed) + text, rtl_dir, widths

    setattr(get_display_str, _MARKER, True)
    _text_extractor.get_display_str = get_display_str
    logger.debug("pypdf mixed-direction text patch installed")
