# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The keyword half of document search returns the best matches, in any language."""

from gaia.rag.keyword_search import rank_keyword_chunks


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
