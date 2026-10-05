# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""`gaia eval retrieval`: answer matching, evidence location, corpus manifest, gate.

None of these need Lemonade; the end-to-end harness over a real FAISS index is
in tests/unit/rag/test_retrieval_bench_harness.py.
"""

import hashlib
import json
import re

import pytest

from gaia import cli
from gaia.eval.retrieval import corpus, runner, sources
from gaia.eval.retrieval.locate import (
    ChunkSpan,
    DocText,
    Evidence,
    LabelError,
    chunk_hits,
    locate_chunks,
    resolve_evidence,
)
from gaia.eval.retrieval.scoring import (
    exact_match,
    is_numeric_answer,
    numeric_match,
    percentile,
    retrieval_summary,
    token_f1,
)

# ── answers ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "gold,prediction",
    [
        ("$1577.00", "Capital expenditure was $1,577 million in FY2018."),
        ("$1577.00", "About $1.577 billion."),
        ("24.26", "The ratio is roughly 24.3."),
        ("1.9%", "It grew 1.9 percent."),
        ("1.9%", "Growth was 0.019 of revenue."),
        ("-0.02", "The change was (0.02)."),
        ("1998", "It was founded in 1998."),
        ("0.66", "The ratio is 0.66."),
    ],
)
def test_numeric_match_accepts_rounding_scale_and_sign_conventions(gold, prediction):
    assert numeric_match(gold, prediction)


@pytest.mark.parametrize(
    "gold,prediction",
    [
        ("0.66", "The ratio is 0.70."),
        ("$1577.00", "Capital expenditure was $1,373 million."),
        ("24.26", "I could not find this in the documents."),
    ],
)
def test_numeric_match_rejects_a_different_figure_or_a_refusal(gold, prediction):
    assert not numeric_match(gold, prediction)


@pytest.mark.parametrize(
    "gold", ["$1577.00", "24.26", "1.9%", "-0.02", "(1,234)", "1998"]
)
def test_short_figures_are_scored_as_numeric(gold):
    assert is_numeric_answer(gold)


@pytest.mark.parametrize(
    "gold", ["Yes, margins improved.", "AES converted inventory 9.5 times", ""]
)
def test_prose_answers_are_not_numeric(gold):
    assert not is_numeric_answer(gold)


def test_exact_match_is_case_and_punctuation_insensitive_and_takes_aliases():
    assert exact_match(
        "Energy Resource Activities Act",
        [],
        "Under the energy resource activities act.",
    )
    assert exact_match("USB 2.0", ["Hi-Speed USB"], "It is a Hi-Speed USB port")
    assert not exact_match("Forest Act", [], "Under the Oil and Gas Act")


@pytest.mark.parametrize(
    "gold,prediction",
    [("No", "I do not know."), ("2x", "a 12x speedup"), ("six", "sixty units")],
)
def test_exact_match_needs_whole_tokens(gold, prediction):
    assert not exact_match(gold, [], prediction)


def test_exact_match_works_for_non_latin_scripts():
    assert exact_match("308", [], "Die Verteidigung gab 308 Punkte ab.")
    assert exact_match("北京", [], "答案是北京。")


def test_token_f1():
    assert token_f1("the Denver Broncos", "Denver Broncos") == 1.0
    assert token_f1("Denver Broncos", "Carolina Panthers") == 0.0


def test_retrieval_summary_counts_recall_at_k_and_mrr():
    summary = retrieval_summary([1, 3, None, 6], ks=(1, 5, 20))
    assert summary == {
        "n": 4,
        "recall@1": 0.25,
        "recall@5": 0.5,
        "recall@20": 0.75,
        "mrr": round((1 + 1 / 3 + 1 / 6) / 4, 4),
    }


def test_percentile_interpolates():
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert percentile([5.0], 0.95) == 5.0
    assert percentile([], 0.5) is None


# ── evidence location ───────────────────────────────────────────────────────

PAGED = "[Page 1]\nIntro text here.\n\n[Page 2]\nThe  pump  rate is\n42 litres.\n\n[Page 3]\nEnd."


def test_doctext_maps_offsets_to_pages():
    doc = DocText.from_full_text("d.pdf", PAGED)
    start = doc.text.index("The pump")
    assert doc.pages_between(start, start + 5) == {2}
    assert doc.pages_between(0, len(doc.text)) == {1, 2, 3}
    assert doc.page_text(2).startswith("[Page 2] The pump rate is 42 litres.")


def test_chunks_are_located_after_whitespace_normalization():
    doc = DocText.from_full_text("d.pdf", PAGED)
    chunks = [
        "[Page 1] Intro text here.",
        "The pump rate is 42 litres. [Page 3]",
        "not in the doc",
    ]
    spans, unlocated = locate_chunks(chunks, {"d.pdf": [0, 1, 2]}, {"d.pdf": doc})
    assert unlocated == [2]
    assert spans[0].pages == {1}
    assert spans[1].pages == {2, 3}


def test_a_quote_resolves_only_on_its_labelled_page():
    doc = DocText.from_full_text("d.pdf", PAGED)
    ev = resolve_evidence(Evidence("d.pdf", 2, "pump rate is 42"), doc)
    assert doc.text[ev.span[0] : ev.span[1]] == "pump rate is 42"
    with pytest.raises(LabelError, match="not found on page 3"):
        resolve_evidence(Evidence("d.pdf", 3, "pump rate is 42"), doc)
    with pytest.raises(LabelError, match="no page 9"):
        resolve_evidence(Evidence("d.pdf", 9, None), doc)


def test_a_chunk_hits_when_it_covers_half_the_quote_or_the_labelled_page():
    quote = Evidence("d.pdf", None, "x", (100, 140))
    assert chunk_hits(ChunkSpan(0, "d.pdf", 90, 125, set()), [quote])
    assert not chunk_hits(ChunkSpan(0, "d.pdf", 130, 200, set()), [quote])
    assert not chunk_hits(ChunkSpan(0, "other.pdf", 0, 999, set()), [quote])
    page = Evidence("d.pdf", 4)
    assert chunk_hits(ChunkSpan(0, "d.pdf", 0, 10, {3, 4}), [page])
    assert not chunk_hits(None, [page])


# ── corpus manifest and labels ──────────────────────────────────────────────


def test_every_manifest_file_is_pinned_and_checksummed():
    manifest = sources.load_manifest()
    assert set(manifest) == {"financebench", "amd", "arxiv", "xquad", "wikipedia"}
    for name, spec in manifest.items():
        assert spec["license"], name
        for rel, entry in spec["files"].items():
            assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), rel
            assert entry["bytes"] > 0, rel
            assert entry["url"].startswith("https://"), rel
            if "githubusercontent" in entry["url"] or "huggingface" in entry["url"]:
                assert re.search(
                    r"/[0-9a-f]{40}/", entry["url"]
                ), f"{rel} is not pinned to a commit"


def test_fetch_refuses_a_file_whose_checksum_does_not_match(tmp_path, monkeypatch):
    monkeypatch.setenv(sources.CACHE_ENV, str(tmp_path))
    good = b"real bytes"
    manifest = {
        "demo": {
            "files": {
                "a.txt": {
                    "url": "https://example.invalid/a.txt",
                    "sha256": hashlib.sha256(good).hexdigest(),
                    "bytes": 10,
                }
            }
        }
    }
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "a.txt").write_bytes(good)
    assert sources.fetch("demo", "a.txt", manifest).read_bytes() == good
    (tmp_path / "demo" / "a.txt").write_bytes(b"edited bytes")
    with pytest.raises(sources.SourceError, match="Checksum mismatch"):
        sources.fetch("demo", "a.txt", manifest)


def test_offline_fetch_of_a_missing_file_names_how_to_download_it(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(sources.CACHE_ENV, str(tmp_path))
    manifest = {
        "demo": {
            "files": {
                "a.txt": {
                    "url": "https://example.invalid/a.txt",
                    "sha256": "0" * 64,
                    "bytes": 1,
                }
            }
        }
    }
    with pytest.raises(sources.SourceError, match="--offline"):
        sources.fetch("demo", "a.txt", manifest, offline=True)
    with pytest.raises(sources.SourceError, match="not listed"):
        sources.fetch("demo", "b.txt", manifest)


@pytest.mark.parametrize("name", ["repo_docs", "technical_docs"])
def test_labelled_datasets_are_well_formed(name):
    spec = json.loads(
        (corpus.DATASETS_DIR / f"{name}.json").read_text(encoding="utf-8")
    )
    doc_ids = {d["id"] for d in spec["documents"]}
    ids = [q["id"] for q in spec["questions"]]
    assert len(ids) == len(set(ids))
    manifest = sources.load_manifest()
    for d in spec["documents"]:
        if "path" in d:
            assert (sources.REPO_ROOT / d["path"]).is_file(), d
        else:
            assert d["file"] in manifest[d["source"]]["files"], d
    for q in spec["questions"]:
        question = corpus.Question(
            q["id"],
            name,
            q["question"],
            q["answer"],
            q["answer_type"],
            [{"doc": q["doc"], **e} for e in q["evidence"]],
        )
        corpus._check_question(question, doc_ids)  # pylint: disable=protected-access
        for e in q["evidence"]:
            assert 20 <= len(" ".join(e["quote"].split())) <= 200, q["id"]


def test_repo_docs_loads_offline_from_committed_files():
    ds = corpus.load_labelled("repo_docs", offline=True)
    assert ds.sources == []
    assert len(ds.questions) >= 40
    assert all(d.path.is_file() for d in ds.documents)


# ── gate ────────────────────────────────────────────────────────────────────


def _results(recall5=0.9, mrr=0.8, agent=1.0, hit=True, case="pass", embedder="emb"):
    return {
        "meta": {"suite": "pr", "embedder": {"model": embedder}},
        "datasets": [
            {
                "dataset": "repo_docs",
                "summary": {
                    "sdk": {"recall@5": recall5, "mrr": mrr},
                    "agent": {"recall@returned": agent},
                },
                "questions": [{"id": "q1", "sdk_rank": 1 if hit else None}],
            }
        ],
        "hard_cases": [{"id": "corrupted_cache", "status": case}],
    }


def test_gate_passes_within_tolerance():
    passed, _ = runner.compare(_results(), _results(recall5=0.89), tolerance=0.02)
    assert passed


def test_gate_fails_on_a_metric_drop_and_lists_the_flipped_question():
    passed, lines = runner.compare(
        _results(), _results(recall5=0.8, hit=False), tolerance=0.02
    )
    assert not passed
    assert any(line.startswith("FAIL repo_docs sdk recall@5") for line in lines)
    assert any("q1: sdk hit@5 PASS->FAIL" in line for line in lines)


def test_gate_fails_when_a_passing_hard_case_regresses_or_errors():
    assert not runner.compare(_results(), _results(case="fail"), 0.02)[0]
    assert not runner.compare(_results(), _results(case="error"), 0.02)[0]
    assert runner.compare(_results(case="fail"), _results(case="pass"), 0.02)[0]


def test_gate_fails_when_more_labelled_documents_are_refused():
    baseline, current = _results(), _results()
    baseline["datasets"][0]["index"] = {"required_failed": 0}
    current["datasets"][0]["index"] = {"required_failed": 2}
    passed, lines = runner.compare(baseline, current, 0.02)
    assert not passed
    assert any("failed to index" in line for line in lines)


def test_gate_refuses_a_baseline_from_another_embedder():
    with pytest.raises(ValueError, match="embedder"):
        runner.compare(_results(), _results(embedder="other"), 0.02)


def test_gate_refuses_a_baseline_captured_with_a_different_vlm_state():
    baseline, current = _results(), _results()
    baseline["meta"]["vlm"] = {"enabled": True}
    current["meta"]["vlm"] = {"enabled": False}
    with pytest.raises(ValueError, match="VLM"):
        runner.compare(baseline, current, 0.02)


def test_markdown_report_renders_a_minimal_run():
    results = {
        "meta": {
            "suite": "pr",
            "started": "t",
            "git_sha": "abc",
            "machine": {},
            "llm_model": "m",
            "answers": False,
            "rag_defaults": {},
            "duration_s": 1,
            "sources": {},
        },
        "datasets": [],
        "hard_cases": [{"title": "Case", "status": "pass", "finding": "ok"}],
    }
    text = runner.render_markdown(results)
    assert "| Case | **pass** | ok |" in text


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_parses_the_documented_invocations():
    parse = cli.build_parser().parse_args
    args = parse(
        ["eval", "retrieval", "--component", "rag", "--suite", "pr", "--gate", "b.json"]
    )
    assert (args.eval_command, args.suite, args.gate, args.tolerance) == (
        "retrieval",
        "pr",
        "b.json",
        0.02,
    )
    args = parse(
        [
            "eval",
            "retrieval",
            "--suite",
            "full",
            "--judge",
            "--scale-tiers",
            "10,100",
            "--xquad-languages",
            "de,zh",
            "--retrieval-only",
            "--offline",
        ]
    )
    assert parse(["eval", "retrieval", "--no-vlm"]).no_vlm
    assert (args.judge, args.scale_tiers, args.retrieval_only, args.offline) == (
        True,
        "10,100",
        True,
        True,
    )
    with pytest.raises(SystemExit):
        parse(["eval", "retrieval", "--component", "code_index"])


def test_committed_pr_baseline_is_a_real_gateable_run():
    path = sources.REPO_ROOT / "tests/fixtures/eval_baselines/rag-retrieval/pr.json"
    baseline = json.loads(path.read_text(encoding="utf-8"))
    assert baseline["meta"]["suite"] == "pr"
    assert baseline["meta"]["vlm"]["enabled"] is False
    assert baseline["meta"]["git_sha"] and baseline["meta"]["machine"]["cpu"]
    passed, _ = runner.compare(baseline, baseline, tolerance=0.0)
    assert passed


@pytest.mark.parametrize(
    "gold,prediction",
    [
        ("-3.5%", "Sales rose 3.5% in the year."),
        ("20.2%", "Margins were strong in fiscal 2020."),
        ("12", "It improved 1,200 basis points."),
    ],
)
def test_numeric_match_respects_sign_years_and_percent(gold, prediction):
    assert not numeric_match(gold, prediction)


def test_a_negative_figure_matches_a_stated_decline():
    assert numeric_match("-3.5%", "Sales declined 3.5% year over year.")
    assert numeric_match("-0.02", "The ratio was -0.02.")


def test_gate_allows_one_flipped_question_but_not_two():
    def run(recall5):
        r = _results(recall5=recall5)
        r["datasets"][0]["summary"]["questions"] = 48
        return r

    assert runner.compare(run(47 / 48), run(46 / 48), 0.02)[0]
    assert not runner.compare(run(47 / 48), run(45 / 48), 0.02)[0]


def test_gate_fails_when_a_baseline_metric_is_missing_from_the_run():
    current = _results()
    del current["datasets"][0]["summary"]["agent"]
    passed, lines = runner.compare(_results(), current, 0.02)
    assert not passed and any("missing from this run" in line for line in lines)
