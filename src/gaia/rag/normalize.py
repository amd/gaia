# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The one form of text that search compares, so a question meets its passage
however either was typed. Chunks keep the text users are shown."""

import re
import unicodedata

# Tashkeel, dagger alef and tatweel: vowel marks and stretching, not letters.
_ARABIC_MARKS = re.compile("[\u064b-\u065f\u0670\u0640]")
# Alef wasla is an alef with a mark on it; Arabic decimal and thousands marks;
# Persian and Sindhi kaf and Persian yeh are the Arabic letters.
_FOLDS = str.maketrans(
    {"\u0671": "\u0627", "\u066b": ".", "\u066c": ","}
    | {"\u06a9": "\u0643", "\u06aa": "\u0643", "\u06cc": "\u064a"}
)
_NON_ASCII_DIGIT = re.compile(r"(?![0-9])\d")


def search_form(text: str) -> str:
    """``text`` with compatibility forms folded (NFKC), no Arabic vowel marks,
    and every script's digits written 0-9."""
    text = unicodedata.normalize("NFKC", text)
    text = _ARABIC_MARKS.sub("", text).translate(_FOLDS)
    return _NON_ASCII_DIGIT.sub(lambda m: str(unicodedata.digit(m.group())), text)
