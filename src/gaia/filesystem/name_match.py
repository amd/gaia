# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""File-name matching that ignores case and accents.

"resume" has to find ``résumé 📄 draft.txt``: users type names without
accents, and the file name may be stored composed (NFC) or decomposed (NFD,
as macOS writes it). Every name search folds both sides with :func:`fold_name`
so the walk, the glob and the index agree.
"""

from __future__ import annotations

import fnmatch
import re
import unicodedata
from typing import List

# Combining marks that are accents on Latin, Greek and Cyrillic letters. Other
# marks (Japanese dakuten, Indic vowel signs) change the letter, so they stay.
_DIACRITIC_BLOCKS = (
    (0x0300, 0x036F),
    (0x1AB0, 0x1AFF),
    (0x1DC0, 0x1DFF),
    (0x20D0, 0x20FF),
    (0xFE20, 0xFE2F),
)


def _is_diacritic(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _DIACRITIC_BLOCKS)


def strip_accents(text: str) -> str:
    """*text* without accents, case kept: ``"Résumé"`` → ``"Resume"``.

    NFKD splits each accent off its letter, the accents are dropped, and NFC
    puts back together what was not an accent (Hangul syllables), so a
    syllable cannot match half of another. Emoji and CJK come back unchanged.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not _is_diacritic(ch))
    return unicodedata.normalize("NFC", stripped)


def fold_name(text: str) -> str:
    """*text* without case or accents: ``"Résumé"`` → ``"resume"``."""
    return strip_accents(text).casefold()


def fold_glob_match(name: str, pattern: str) -> bool:
    """``fnmatch`` on folded text, the same on every platform.

    Folding leaves ``*``, ``?`` and ``[...]`` alone, so glob semantics hold.
    """
    return fnmatch.fnmatchcase(fold_name(name), fold_name(pattern))


def fold_words_match(name: str, query: str) -> bool:
    """Every whitespace-separated word of *query* occurs in *name*."""
    folded = fold_name(name)
    return all(word in folded for word in fold_name(query).split())


_WORD = re.compile(r"\w+")


def fts_name_terms(query: str) -> List[str]:
    """The folded words of *query*, for an FTS5 prefix search on file names.

    Glob and punctuation characters are FTS5 syntax, so a raw query like
    ``résumé*.txt`` is a syntax error there; only the words are kept.
    """
    return _WORD.findall(fold_name(query))
