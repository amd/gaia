# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Find the chunks that best match a query's words, in any language."""

import math
import re
from collections import Counter
from functools import lru_cache
from typing import Dict, FrozenSet, Iterable, List, Tuple

_WORD = re.compile(r"[^\W_]+")
_ARABIC_MARKS = re.compile("[\u064b-\u065f\u0670\u0640]")
_ARABIC_LETTERS = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"}
    | {chr(0x0660 + digit): str(digit) for digit in range(10)}
    | {chr(0x06F0 + digit): str(digit) for digit in range(10)}
)
# "and", "so", "with", "for", "like", each on "the"; the word stays whole if short.
_ARABIC_PREFIX = re.compile("^(?:[وف]?(?:[بلك]?ال|لل)|[وف])(?=..)")

_MIN_SCORE = 0.6
_SCORE_RANGE = 0.3
# Below this a chunk matched only words most chunks hold, such as "the" or "is".
_MIN_SHARE = 0.1


def _words(text: str) -> List[str]:
    text = _ARABIC_MARKS.sub("", text.lower()).translate(_ARABIC_LETTERS)
    return [_ARABIC_PREFIX.sub("", word) for word in _WORD.findall(text)]


@lru_cache(maxsize=4096)
def _chunk_features(text: str) -> Tuple[FrozenSet[str], FrozenSet[Tuple[str, str]]]:
    """Return the reusable word and adjacent-pair features for one chunk.

    The cache key is the chunk text rather than its current list position. RAG
    replaces and compacts ``chunks`` while indexing and removing files, so an
    index-aligned cache could return features for a different document.
    """
    words = _words(text)
    return frozenset(words), frozenset(zip(words, words[1:]))


def rank_keyword_chunks(
    query: str, chunks: Iterable[Tuple[int, str]], limit: int = 5
) -> List[Tuple[int, float, List[str]]]:
    """The ``limit`` chunks that best match, as (index, score, matched words).

    A word that few chunks hold counts for more, so "subscriber" outweighs
    "business" in a business contract, and words of the query that sit side by
    side in a chunk count again. Scores run 0.6-0.9, the range of a good
    embedding match, so these chunks compete with the embedding results.
    """
    query_words = list(dict.fromkeys(_words(query)))
    query_pairs = list(zip(query_words, query_words[1:]))
    if not query_words:
        return []

    indexed: Dict[int, Tuple[FrozenSet[str], FrozenSet[Tuple[str, str]]]] = {}
    held_by: Counter = Counter()
    query_word_set = set(query_words)
    for index, text in chunks:
        unique, pairs = _chunk_features(text)
        indexed[index] = (unique, pairs)
        held_by.update(unique & query_word_set)

    # Plus one, so a word in every chunk of a small document still counts a little.
    total = len(indexed)
    weights = {
        word: math.log((total + 1) / (held_by[word] + 1)) + 1 / (total + 1)
        for word in query_words
    }
    whole = sum(weights.values()) + sum(
        weights[first] + weights[second] for first, second in query_pairs
    )

    ranked = []
    for index, (unique, pairs) in indexed.items():
        matched = [word for word in query_words if word in unique]
        if not matched:
            continue
        found = sum(weights[word] for word in matched) + sum(
            weights[first] + weights[second]
            for first, second in query_pairs
            if (first, second) in pairs
        )
        if found / whole >= _MIN_SHARE:
            ranked.append((found / whole, index, matched))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        (index, _MIN_SCORE + _SCORE_RANGE * share, matched)
        for share, index, matched in ranked[:limit]
    ]
