# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Find the chunks that best match a query's words, in any language."""

import math
import re
import threading
import weakref
from array import array
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_WORD = re.compile(r"[^\W_]+")
_ARABIC_MARKS = re.compile("[\u064b-\u065f\u0670\u0640]")
_ARABIC_LETTERS = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"}
    | {chr(0x0660 + digit): str(digit) for digit in range(10)}
    | {chr(0x06F0 + digit): str(digit) for digit in range(10)}
)
# "and", "so", "with", "for", "like", each on "the"; the word stays whole if short.
_ARABIC_PREFIX = re.compile("^(?:[وف]?(?:[بلك]?ال|لل)|[وف])(?=..)")
# Every letter _ARABIC_PREFIX can start with; other words skip the regex.
_PREFIX_START = frozenset("وفبلكا")

_MIN_SCORE = 0.6
_SCORE_RANGE = 0.3
# Below this a chunk matched only words most chunks hold, such as "the" or "is".
_MIN_SHARE = 0.1

_ID_SIZE = array("I").itemsize


def _words(text: str) -> List[str]:
    text = _ARABIC_MARKS.sub("", text.lower()).translate(_ARABIC_LETTERS)
    return [
        _ARABIC_PREFIX.sub("", word) if word[0] in _PREFIX_START else word
        for word in _WORD.findall(text)
    ]


class KeywordIndex:
    """The words of a list of chunks, read once so a search only reads the query.

    Holds each chunk's words as ids in reading order, and for each word the
    chunks that hold it. Pass the previous index as ``reuse`` and chunks whose
    text it already read are not read again, so removing a document costs no
    re-reading.
    """

    def __init__(self, texts: Sequence[str], reuse: Optional["KeywordIndex"] = None):
        self.texts: List[str] = list(texts)
        self._build(reuse)
        # Words of removed chunks stay in the vocabulary until it is mostly dead.
        if reuse is not None and len(self._vocab) > 2 * len(self._holders) + 1000:
            self._build(None)

    def _build(self, reuse: Optional["KeywordIndex"]) -> None:
        if reuse is None:
            vocab: Dict[str, int] = {}
            known: Dict[str, bytes] = {}
        else:
            vocab, known = dict(reuse._vocab), reuse._sequence_of
        sequence_of: Dict[str, bytes] = {}
        holders: Dict[int, array] = {}
        self._sequences: List[bytes] = []
        for position, text in enumerate(self.texts):
            sequence = sequence_of.get(text)
            if sequence is None:
                sequence = known.get(text)
                if sequence is None:
                    sequence = array(
                        "I", [vocab.setdefault(w, len(vocab)) for w in _words(text)]
                    ).tobytes()
                sequence_of[text] = sequence
            self._sequences.append(sequence)
            for word_id in set(array("I", sequence)):
                held = holders.get(word_id)
                if held is None:
                    held = holders[word_id] = array("I")
                held.append(position)

        self._vocab = vocab
        self._sequence_of = sequence_of
        self._holders = holders

    def __len__(self) -> int:
        return len(self.texts)

    def holds(self, chunks: Sequence[str]) -> bool:
        """Whether this index was built from exactly these chunk texts."""
        return self.texts == (chunks if isinstance(chunks, list) else list(chunks))

    def _side_by_side(self, position: int, pair: bytes) -> bool:
        sequence = self._sequences[position]
        at = sequence.find(pair)
        while at != -1 and at % _ID_SIZE:
            at = sequence.find(pair, at + 1)
        return at != -1

    def rank(
        self, query: str, positions: Optional[Iterable[int]] = None, limit: int = 5
    ) -> List[Tuple[int, float, List[str]]]:
        """The ``limit`` chunks that best match, as (position, score, matched words).

        Searches every chunk, or only ``positions``. A word that few chunks hold
        counts for more, so "subscriber" outweighs "business" in a business
        contract, and words of the query that sit side by side in a chunk count
        again. Scores run 0.6-0.9, the range of a good embedding match, so these
        chunks compete with the embedding results.
        """
        query_words = list(dict.fromkeys(_words(query)))
        query_pairs = list(zip(query_words, query_words[1:]))
        if not query_words:
            return []

        if positions is None:
            searched = None
            total = len(self.texts)
        else:
            # A position listed twice counts twice toward how common a word is.
            searched = Counter(positions)
            total = len(searched)

        held_by: Counter = Counter()
        matched: Dict[int, List[str]] = {}
        for word in query_words:
            word_id = self._vocab.get(word)
            held = self._holders.get(word_id, ()) if word_id is not None else ()
            if searched is None:
                held_by[word] = len(held)
            for position in held:
                if searched is not None:
                    times = searched.get(position)
                    if not times:
                        continue
                    held_by[word] += times
                matched.setdefault(position, []).append(word)

        # Plus one, so a word in every chunk of a small document still counts a little.
        weights = {
            word: math.log((total + 1) / (held_by[word] + 1)) + 1 / (total + 1)
            for word in query_words
        }
        whole = sum(weights.values()) + sum(
            weights[first] + weights[second] for first, second in query_pairs
        )
        pair_ids = {
            (first, second): array(
                "I", [self._vocab[first], self._vocab[second]]
            ).tobytes()
            for first, second in query_pairs
            if first in self._vocab and second in self._vocab
        }

        if searched is None:
            candidates = sorted(matched)
        else:
            order = {position: n for n, position in enumerate(searched)}
            candidates = sorted(matched, key=order.__getitem__)

        ranked = []
        for position in candidates:
            words = matched[position]
            found = sum(weights[word] for word in words) + sum(
                weights[first] + weights[second]
                for first, second in query_pairs
                if first in words
                and second in words
                and self._side_by_side(position, pair_ids[(first, second)])
            )
            if found / whole >= _MIN_SHARE:
                ranked.append((found / whole, position, words))

        ranked.sort(key=lambda item: item[0], reverse=True)
        return [
            (position, _MIN_SCORE + _SCORE_RANGE * share, words)
            for share, position, words in ranked[:limit]
        ]


class _Slot:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.index: Optional[KeywordIndex] = None


_SLOTS_LOCK = threading.Lock()
#: RAG instance -> its keyword index; an index goes away with its RAG instance.
_SLOTS: "weakref.WeakKeyDictionary[Any, _Slot]" = weakref.WeakKeyDictionary()


def keyword_index(rag: Any) -> KeywordIndex:
    """The keyword index of ``rag.chunks``, rebuilt only when the chunks change."""
    with _SLOTS_LOCK:
        slot = _SLOTS.get(rag)
        if slot is None:
            slot = _SLOTS[rag] = _Slot()
    with slot.lock:
        chunks = rag.chunks
        if slot.index is None or not slot.index.holds(chunks):
            slot.index = KeywordIndex(chunks, reuse=slot.index)
        return slot.index


def rank_keyword_chunks(
    query: str, chunks: Iterable[Tuple[int, str]], limit: int = 5
) -> List[Tuple[int, float, List[str]]]:
    """:meth:`KeywordIndex.rank` over (index, text) pairs, read once for this call.

    For chunks searched more than once, use :func:`keyword_index` instead.
    """
    items = list(chunks)
    text_of = dict(items)
    keys = list(text_of)
    position_of = {key: position for position, key in enumerate(keys)}
    index = KeywordIndex([text_of[key] for key in keys])
    return [
        (keys[position], score, words)
        for position, score, words in index.rank(
            query, [position_of[key] for key, _text in items], limit
        )
    ]
