# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The keyword half of document search returns the best matches, in any language."""

import gc
import math
import random
import threading
import time
import weakref
from collections import Counter

from gaia.rag import keyword_search
from gaia.rag.keyword_search import KeywordIndex, keyword_index, rank_keyword_chunks


def _top(query, texts, limit=5):
    return [
        i for i, _score, _words in rank_keyword_chunks(query, enumerate(texts), limit)
    ]


def test_best_match_beats_earlier_chunks_sharing_one_common_word():
    # The old search stopped at the first five chunks holding any query word.
    filler = [f"business clause {n} about services" for n in range(8)]
    target = "A Large Business Subscriber means a customer meeting these conditions"

    assert _top("conditions for Large Business Subscriber", filler + [target])[0] == 8


def test_words_side_by_side_rank_above_the_same_words_apart():
    apart = "net profit fell while the debt of the group rose"
    together = "the group reported net debt of two billion and a profit"

    assert _top("net debt", [apart, together])[0] == 1


def test_arabic_query_finds_arabic_chunk():
    texts = ["شروط الخدمة العامة", "بلغ صافي الدين في نهاية العام", "رسوم التفعيل"]

    assert _top("صافي الدين", texts)[0] == 1


def test_arabic_prefixes_and_letter_forms_still_match():
    assert _top("الشركة", ["شركة كبيرة", "عقد قصير"]) == [0]
    assert _top("بالشركة", ["والشركه كبيرة", "عقد قصير"]) == [0]
    assert _top("إيرادات", ["ايرادات المجموعة", "عقد قصير"]) == [0]


def test_short_arabic_word_is_not_cut_down_to_nothing():
    assert _top("ولد", ["ولد صغير", "عقد قصير"]) == [0]


def test_numbers_are_searchable():
    assert _top("2025", ["results for 2024", "results for 2025"]) == [1]


def test_numbers_match_whichever_digits_they_are_written_in():
    assert _top("٢٠٢٥", ["نتائج ٢٠٢٤", "نتائج ٢٠٢٥"]) == [1]
    assert _top("2025", ["نتائج ٢٠٢٤", "نتائج ٢٠٢٥"]) == [1]
    assert _top("۲۰۲۵", ["results 2024", "results 2025"]) == [1]


def test_filler_words_alone_do_not_bring_in_a_chunk():
    texts = [f"this is the clause number {n} of the terms" for n in range(50)]

    assert _top("what is the cancellation fee", texts) == []


def test_word_in_every_chunk_of_a_small_document_still_matches():
    assert len(_top("contract", ["contract one", "contract two"])) == 2


def test_nothing_matching_returns_nothing():
    assert rank_keyword_chunks("zebra", [(0, "contract"), (1, "terms")]) == []
    assert rank_keyword_chunks("", [(0, "contract")]) == []
    assert rank_keyword_chunks("?!", [(0, "contract")]) == []
    assert rank_keyword_chunks("zebra", []) == []


def test_chunk_keeps_its_own_index_and_matched_words():
    index, score, words = rank_keyword_chunks("large fee", [(41, "a large fee")])[0]

    assert index == 41
    assert words == ["large", "fee"]
    assert 0.6 <= score <= 0.9


def test_limit_is_respected():
    assert len(_top("fee", ["fee a", "fee b", "fee c", "other"], limit=2)) == 2


# The cached index (#6393) must rank exactly as the scoring read straight off
# each chunk's words, which is what this computes.
def _plain_rank(query, chunks, limit=5):
    query_words = list(dict.fromkeys(keyword_search._words(query)))
    query_pairs = list(zip(query_words, query_words[1:]))
    if not query_words:
        return []
    indexed, held_by = {}, Counter()
    for index, text in chunks:
        words = keyword_search._words(text)
        indexed[index] = (set(words), set(zip(words, words[1:])))
        held_by.update(set(words) & set(query_words))
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
        if found / whole >= 0.1:
            ranked.append((found / whole, index, matched))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        (index, 0.6 + 0.3 * share, matched) for share, index, matched in ranked[:limit]
    ]


_VOCAB = (
    "the net debt fee large business subscriber clause contract of and 2025 "
    "x86_64 get_user_by_id الشركة شركة بالشركة والشركه صافي الدين رسوم ٢٠٢٥ ولد"
).split()


def _random_text(rng):
    return " ".join(rng.choice(_VOCAB) for _ in range(rng.randint(0, 25)))


def test_index_ranks_exactly_as_the_plain_scoring():
    rng = random.Random(6393)
    for _ in range(300):
        texts = [_random_text(rng) for _ in range(rng.randint(0, 30))]
        query = _random_text(rng)
        limit = rng.choice([1, 3, 5, 50])
        index = KeywordIndex(texts)

        assert repr(index.rank(query, limit=limit)) == repr(
            _plain_rank(query, enumerate(texts), limit)
        )
        positions = [rng.randrange(len(texts)) for _ in range(len(texts))]
        assert repr(index.rank(query, positions, limit)) == repr(
            _plain_rank(query, ((p, texts[p]) for p in positions), limit)
        )


def test_skipping_the_prefix_regex_changes_no_word():
    text = (
        "والشركة فالعقد بالشركة كالبيت للعميل الكتاب وولد فلل أحمد إيرادات "
        "و ف ال بل ولد Large x86_64 ١٢٣ Über straße"
    )
    lowered = keyword_search._ARABIC_MARKS.sub("", text.lower()).translate(
        keyword_search._ARABIC_LETTERS
    )
    expected = [
        keyword_search._ARABIC_PREFIX.sub("", word)
        for word in keyword_search._WORD.findall(lowered)
    ]

    assert keyword_search._words(text) == expected


def test_removing_chunks_reads_no_kept_chunk_again(monkeypatch):
    texts = [f"clause {n} about the fee" for n in range(20)]
    old = KeywordIndex(texts)
    kept = texts[:5] + texts[10:]
    read = []
    words = keyword_search._words
    monkeypatch.setattr(
        keyword_search, "_words", lambda text: read.append(text) or words(text)
    )

    rebuilt = KeywordIndex(kept + ["a new fee"], reuse=old)

    assert read == ["a new fee"]
    for query in ("fee", "clause 12", "new fee", "the"):
        assert rebuilt.rank(query, limit=50) == KeywordIndex(kept + ["a new fee"]).rank(
            query, limit=50
        )


def test_a_mostly_dead_vocabulary_is_dropped():
    old = KeywordIndex([f"word{n} fee" for n in range(5000)])
    rebuilt = KeywordIndex(["fee one", "fee two"], reuse=old)

    assert len(rebuilt._vocab) == 3
    assert [i for i, _s, _w in rebuilt.rank("fee one")] == [0, 1]


class _Rag:
    def __init__(self, chunks):
        self.chunks = chunks


def test_index_is_read_once_and_follows_every_change_to_the_chunks():
    rag = _Rag(["the fee is large", "the term is short"])
    first = keyword_index(rag)
    assert keyword_index(rag) is first

    rag.chunks.append("a new fee")  # changed in place
    assert keyword_index(rag).texts == [
        "the fee is large",
        "the term is short",
        "a new fee",
    ]

    rag.chunks[0] = "no match here"  # replaced in place
    assert [i for i, _s, _w in keyword_index(rag).rank("fee")] == [2]

    rag.chunks = ["fee"]  # a new list, as index, re-index and removal publish
    assert [i for i, _s, _w in keyword_index(rag).rank("fee")] == [0]

    rag.chunks = []
    assert keyword_index(rag).rank("fee") == []


def test_each_rag_keeps_its_own_index():
    a, b = _Rag(["alpha fee"]), _Rag(["beta fee", "gamma"])

    assert keyword_index(a).texts == ["alpha fee"]
    assert keyword_index(b).texts == ["beta fee", "gamma"]
    assert keyword_index(a).texts == ["alpha fee"]


def test_index_goes_away_with_its_rag():
    gc.collect()
    before = len(keyword_search._SLOTS)
    rag = _Rag(["fee"])
    keyword_index(rag)
    assert len(keyword_search._SLOTS) == before + 1

    gone = weakref.ref(rag)
    del rag
    gc.collect()

    assert gone() is None
    assert len(keyword_search._SLOTS) == before


def test_concurrent_searches_build_the_index_once(monkeypatch):
    rag = _Rag([f"clause {n} fee" for n in range(300)])
    builds = []
    real = keyword_search.KeywordIndex

    def counting(*args, **kwargs):
        builds.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(keyword_search, "KeywordIndex", counting)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(keyword_index(rag).rank("fee")))
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(builds) == 1
    assert all(result == results[0] for result in results)


def test_search_time_does_not_grow_with_the_library_text():
    # Re-reading every chunk per search cost ~1.5 s at this size (#6393).
    rng = random.Random(0)
    words = [f"term{n}" for n in range(3000)] + ["the", "of", "and"] * 200
    texts = [" ".join(rng.choice(words) for _ in range(300)) for _ in range(4000)]
    index = KeywordIndex(texts)
    queries = [" ".join(rng.choice(words) for _ in range(6)) for _ in range(20)]

    start = time.perf_counter()
    for query in queries:
        index.rank(query)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"20 searches over 4,000 chunks took {elapsed:.2f}s"
