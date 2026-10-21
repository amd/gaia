# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The retrieval benchmark over a real RAGSDK + FAISS index, with a stand-in embedder.

Everything but the embedding call is production code: the chunker, the cache,
FAISS, the agent's ``query_documents`` tool. The hashed bag-of-words encoder
makes retrieval deterministic, so these tests pin the harness's scoring, not
model quality.
"""

import hashlib
import re
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

pytest.importorskip("faiss", reason="Real vector-index tests require faiss-cpu")

from gaia.eval.retrieval import hard_cases, harness, runner  # noqa: E402
from gaia.eval.retrieval.corpus import Dataset, Document, Question  # noqa: E402
from gaia.eval.retrieval.locate import DocText, locate_chunks  # noqa: E402
from gaia.rag.sdk import RAGSDK, RAGConfig  # noqa: E402

DIM = 512


def _bow(texts, **_kwargs):
    out = np.zeros((len(texts), DIM), dtype="float32")
    for row, text in enumerate(texts):
        for word in re.findall(r"\w+", text.lower()):
            out[row, int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = np.linalg.norm(out[row])
        if norm:
            out[row] /= norm
    return out


def _fake_make_rag(cache_dir, allowed, model=None, **overrides):
    settings = dict(
        cache_dir=str(cache_dir),
        allowed_paths=[str(p) for p in allowed],
        max_indexed_files=1_000_000,
        max_total_chunks=100_000_000,
        enable_lru_eviction=False,
        max_chunks=5,
        use_llm_chunking=False,
    )
    settings.update(overrides)
    with patch("gaia.rag.sdk.AgentSDK"):
        sdk = RAGSDK(RAGConfig(**settings))
    sdk._load_embedder = lambda: None
    sdk._encode_texts = _bow
    sdk._get_hmac_key = lambda: b"retrieval-bench-test-key" * 2
    sdk.llm_client = MagicMock()
    sdk.llm_client.health_check.return_value = {}
    sdk.chat.send.return_value = MagicMock(
        text="The figure is 42 kilonewtons.", stats={}
    )
    return sdk


def _needs_chat_agent():
    """The ``agent`` pipeline calls the chat agent's real ``query_documents``."""
    pytest.importorskip(
        "gaia_agent_chat", reason="runs in test_chat_agent.yml, with the chat wheel"
    )


@pytest.fixture(autouse=True)
def fake_rag(monkeypatch):
    monkeypatch.setattr(harness, "make_rag", _fake_make_rag)
    monkeypatch.setattr(hard_cases, "make_rag", _fake_make_rag)


TOPICS = {
    "pumps": (
        "Centrifugal pumps move water with an impeller. The rated thrust of the "
        "Kestrel pump is 42 kilonewtons at full speed.",
        "rated thrust of the Kestrel pump",
        "42 kilonewtons",
    ),
    "birds": (
        "Arctic terns migrate farther than any other bird. Each year the Svalbard "
        "colony departs on 14 August for Antarctica.",
        "Svalbard colony departs on 14 August",
        "14 August",
    ),
    "bread": (
        "Sourdough relies on wild yeast. The Hartwell bakery proofs its rye loaves "
        "for nineteen hours before baking.",
        "proofs its rye loaves for nineteen hours",
        "nineteen hours",
    ),
    "trains": (
        "High speed rail uses dedicated track. The Corvid line opened its viaduct "
        "over the Mersey estuary in 2011 after six years of work.",
        "viaduct over the Mersey estuary in 2011",
        "Mersey estuary",
    ),
    "chess": (
        "Opening theory runs deep. The Lindqvist gambit sacrifices the queen's "
        "knight on move seven to open the long diagonal.",
        "sacrifices the queen's knight on move seven",
        "queen's knight",
    ),
}


def _filler(topic, n=12):
    return "\n\n".join(
        f"Paragraph {i} about {topic}: general background with no particular figures."
        for i in range(n)
    )


@pytest.fixture
def dataset(tmp_path):
    docs, questions = [], []
    for i, (topic, (fact, quote, answer)) in enumerate(TOPICS.items()):
        path = tmp_path / "docs" / f"{topic}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            f"{_filler(topic)}\n\n{fact}\n\n{_filler(topic, 4)}\n", encoding="utf-8"
        )
        docs.append(Document(topic, path, "repo"))
        questions.append(
            Question(
                f"q{i}",
                "synthetic",
                f"What does the text say about the {quote.split()[1]} {topic}?",
                answer,
                "exact",
                [{"doc": topic, "quote": quote}],
                tags=["text"],
            )
        )
    return Dataset("synthetic", "test", docs, questions)


def test_every_chunk_the_real_chunker_cuts_maps_back_to_its_page(tmp_path):
    rag = _fake_make_rag(tmp_path / "c", [tmp_path])
    pages = [
        f"[Page {p}]\n"
        + "\n\n".join(
            f"Section {p}.{s}\nLine one of section {p}.{s} has   odd   spacing.\nAnd a second sentence. "
            * 6
            for s in range(5)
        )
        for p in range(1, 9)
    ]
    full = "\n\n".join(pages)
    chunks = rag._split_text_into_chunks(full)
    doc = DocText.from_full_text("d.pdf", full)
    spans, unlocated = locate_chunks(
        chunks, {"d.pdf": list(range(len(chunks)))}, {"d.pdf": doc}
    )
    assert len(chunks) > 5
    assert unlocated == []
    assert all(spans[i].pages for i in spans)


def test_a_chunk_split_to_fit_the_embedder_does_not_hide_the_next_one(tmp_path):
    rag = _fake_make_rag(tmp_path / "c", [tmp_path])
    full = (
        ". ".join(
            f"Sentence {i} " + " ".join(f"tok{i}x{j}" for j in range(11))
            for i in range(120)
        )
        + "."
    )
    chunks = rag._split_text_into_chunks(full)
    doc = DocText.from_full_text("d.txt", full)
    spans, unlocated = locate_chunks(
        chunks, {"d.txt": list(range(len(chunks)))}, {"d.txt": doc}
    )
    assert unlocated == []
    # The case under test: an overlap that starts before the split's short tail.
    assert any(spans[i].start < spans[i - 1].start for i in range(1, len(chunks)))
    assert all(spans[i].end > spans[i - 1].start for i in range(1, len(chunks)))


def _vlm_document(rag, pages, images_per_page, body_sections=6, headings=True):
    """A PDF's extracted text with image text merged exactly as indexing merges it."""
    out = []
    for p in range(1, pages + 1):
        body = "\n\n".join(
            ("# " if headings else "")
            + f"Heading {p}.{s}\nBody text of section {p}.{s} on page {p}. "
            + " ".join(f"word{p}x{s}x{w}" for w in range(20))
            for s in range(body_sections)
        )
        images = [
            {
                "image_num": n,
                "text": f"Figure {p}.{n} shows the separator.\n# Diagram {p}.{n}\n"
                + " ".join(f"img{p}x{n}x{w}" for w in range(30))
                + f" ends{p}x{n}.",
            }
            for n in range(1, images_per_page.get(p, 0) + 1)
        ]
        out.append(f"[Page {p}]\n{rag._merge_page_texts(body, images, page_num=p)}")
    return "\n\n".join(out)


def _locate_all(chunks, full):
    doc = DocText.from_full_text("d.pdf", full)
    return locate_chunks(chunks, {"d.pdf": list(range(len(chunks)))}, {"d.pdf": doc})


@pytest.mark.parametrize(
    "pages, images, headings, blocks",
    [
        (5, {1: 3, 2: 1, 4: 2, 5: 2}, True, [(1, 1), (1, 2), (1, 3), (4, 1), (5, 2)]),
        # Too few sections, so the chunker splits on blank lines instead.
        (1, {1: 3}, False, [(1, 1), (1, 2), (1, 3)]),
    ],
    ids=["sections", "paragraphs"],
)
def test_image_text_on_several_pages_maps_back_to_the_source(
    tmp_path, pages, images, headings, blocks
):
    rag = _fake_make_rag(tmp_path / "c", [tmp_path])
    full = _vlm_document(rag, pages, images, body_sections=1, headings=headings)
    chunks = rag._split_text_into_chunks(full)
    spans, unlocated = _locate_all(chunks, full)
    assert unlocated == []
    joined = " ".join(chunks)
    for p, n in blocks:
        assert f"ends{p}x{n}." in joined
        # The last word of one image block never fuses with the next marker.
        assert f"ends{p}x{n}.[Page" not in joined


@pytest.mark.parametrize(
    "images", [{1: 2, 2: 1}, {1: 2, 2: 1, 3: 1}], ids=["two-pages", "three-pages"]
)
def test_a_chunk_of_document_and_image_text_spans_both_pages(tmp_path, images):
    rag = _fake_make_rag(tmp_path / "c", [tmp_path])
    full = _vlm_document(rag, 3, images, body_sections=2, headings=False)
    chunks = rag._split_text_into_chunks(full)
    spans, unlocated = _locate_all(chunks, full)
    assert unlocated == []
    mixed = [
        i
        for i, c in enumerate(chunks)
        if "IMAGE 2" in c and "ends1x2." in c and "word2x0x0" in c
    ]
    assert mixed, "expected a chunk holding page 1's image text and page 2's body"
    assert {1, 2} <= spans[mixed[0]].pages


def test_a_chunk_absent_from_the_source_is_still_unlocated(tmp_path):
    rag = _fake_make_rag(tmp_path / "c", [tmp_path])
    full = _vlm_document(rag, 2, {1: 2})
    chunks = rag._split_text_into_chunks(full)
    fused = "ends1x1.[Page 1] ### 🖼️ IMAGE 2 Figure 1.2 shows the separator."
    invented = "[Page 1] ### 🖼️ IMAGE 9 text the model never produced"
    _, unlocated = _locate_all(chunks + [fused, invented], full)
    assert unlocated == [len(chunks), len(chunks) + 1]


def test_unlocatable_chunks_still_stop_the_benchmark(tmp_path):
    rag = _fake_make_rag(tmp_path / "c", [tmp_path])
    full = _vlm_document(rag, 2, {1: 2})
    chunks = rag._split_text_into_chunks(full)
    rag.chunks = chunks + ["[Page 1] ### 🖼️ IMAGE 9 text the model never produced"]
    rag.file_to_chunk_indices = {"d.pdf": list(range(len(rag.chunks)))}
    rag.file_metadata = {"d.pdf": {"full_text": full}}
    bench = harness.BenchIndex(tmp_path, [], rag=rag)
    with pytest.raises(harness.IndexingError, match="could not be located"):
        bench._locate()


def test_both_pipelines_find_the_labelled_evidence(dataset, tmp_path):
    _needs_chat_agent()
    bench = harness.index_dataset(dataset, tmp_path / "work")
    assert bench.build_stats["indexed"] == len(TOPICS)
    assert bench.build_stats["unlocated_chunks"] == 0
    for q in dataset.questions:
        result = bench.score_retrieval(q, ("sdk", "agent"))
        assert result.sdk_rank is not None and result.sdk_rank <= 5, q.id
        assert result.sdk_doc_rank == 1, q.id
        assert result.agent_rank is not None, q.id


def test_a_label_quote_missing_from_the_document_fails_loudly(dataset, tmp_path):
    bench = harness.index_dataset(dataset, tmp_path / "work")
    bad = Question(
        "bad",
        "synthetic",
        "?",
        "x",
        "exact",
        [{"doc": "pumps", "quote": "text that is not there"}],
    )
    with pytest.raises(harness.LabelError):
        bench.score_retrieval(bad, ("sdk",))


def test_answers_are_classified_by_whether_evidence_reached_the_model(
    dataset, tmp_path
):
    _needs_chat_agent()
    opts = runner.RunOptions(suite=runner.SUITES["pr"], out_dir=tmp_path / "out")
    result = runner._run_dataset(
        dataset, tmp_path / "work", opts, answers=True, judge=None
    )
    by_id = {q["id"]: q["answer"] for q in result["questions"]}
    assert by_id["q0"]["correct"] and by_id["q0"]["outcome"] == "correct"
    assert not by_id["q1"]["correct"]
    assert by_id["q1"]["outcome"] in ("generation_miss", "retrieval_miss")
    assert by_id["q1"]["outcome"] == (
        "generation_miss" if by_id["q1"]["in_context"] else "retrieval_miss"
    )
    assert result["summary"]["answers"]["scored"] == len(TOPICS)


def test_a_refused_document_is_recorded_and_its_questions_miss(dataset, tmp_path):
    _needs_chat_agent()
    dataset.documents[0].path.write_text("", encoding="utf-8")
    bench = harness.index_dataset(dataset, tmp_path / "work")
    assert bench.build_stats["required_failed"] == 1
    assert bench.build_stats["failed"][0]["doc"] == "pumps"
    result = bench.score_retrieval(dataset.questions[0], ("sdk", "agent"))
    assert result.evidence_not_indexed
    assert result.sdk_rank is None and result.agent_rank is None


def _ctx(dataset, tmp_path):
    return hard_cases.CaseContext(tmp_path / "hc", dataset, None, answers=False)


def test_replaced_document_reports_what_each_refresh_path_serves(dataset, tmp_path):
    result = hard_cases.replaced_document(_ctx(dataset, tmp_path))
    checks = result["details"]["checks"]
    assert checks["v1_served_before_change"]
    assert checks["new_content_after_reindex_document"]
    assert checks["new_content_in_new_session_same_cache"]
    assert result["status"] == (
        "pass" if checks["new_content_after_index_document_again"] else "fail"
    )


def test_exact_duplicates_are_measured_in_both_pipelines(dataset, tmp_path):
    _needs_chat_agent()
    result = hard_cases.exact_duplicate(_ctx(dataset, tmp_path))
    assert len(result["details"]["per_question"]) == 3
    assert result["status"] in ("pass", "fail")


def test_corrupted_cache_entries_are_rebuilt_never_served(dataset, tmp_path):
    result = hard_cases.corrupted_cache(_ctx(dataset, tmp_path))
    assert result["status"] == "pass", result["details"]
    assert set(result["details"]["outcomes"]) == {
        "truncated",
        "tampered",
        "signature_missing",
        "garbage",
    }


def test_capacity_eviction_names_the_evicted_document(dataset, tmp_path):
    result = hard_cases.capacity_eviction(_ctx(dataset, tmp_path))
    details = result["details"]
    assert details["first_still_indexed"] is False
    assert result["status"] == ("pass" if details["signal_keys"] else "fail")


def test_prompt_injection_is_skipped_without_answer_generation(dataset, tmp_path):
    assert hard_cases.prompt_injection(_ctx(dataset, tmp_path))["status"] == "skipped"


def test_each_benchmark_answer_starts_with_an_empty_chat_history(dataset, tmp_path):
    bench = harness.index_dataset(dataset, tmp_path / "work")
    bench.answer(dataset.questions[0].question)
    bench.rag.chat.clear_history.assert_called_once()
    bench.answer(dataset.questions[1].question, keep_history=True)
    bench.rag.chat.clear_history.assert_called_once()


def test_query_history_leak_is_skipped_without_answer_generation(dataset, tmp_path):
    result = hard_cases.query_history_leak(_ctx(dataset, tmp_path))
    assert result["status"] == "skipped"
