# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Datasets for the retrieval benchmark: documents, questions, evidence labels.

Four kinds, all on real documents:

* hand-labelled JSON in ``eval/retrieval/datasets/`` (documents committed in
  this repo, or downloaded via ``sources.json``) — evidence is a verbatim quote
  and, for PDFs, its page;
* FinanceBench — SEC filings with annotated evidence pages;
* XQuAD — the multilingual slice; documents are its Wikipedia articles;
* Simple English Wikipedia — distractors for the scale tiers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from gaia.eval.retrieval import sources
from gaia.eval.retrieval.scoring import is_numeric_answer

DATASETS_DIR = sources.REPO_ROOT / "eval" / "retrieval" / "datasets"
ANSWER_TYPES = ("numeric", "exact", "judge")
XQUAD_LANGUAGES = ("ar", "de", "el", "en", "es", "hi", "ru", "th", "tr", "vi", "zh")


@dataclass
class Document:
    id: str
    path: Path
    source: str


@dataclass
class Question:
    id: str
    dataset: str
    question: str
    answer: str
    answer_type: str
    evidence: List[dict]
    aliases: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    language: str = "en"

    @property
    def doc_ids(self) -> List[str]:
        return sorted({e["doc"] for e in self.evidence})


@dataclass
class Dataset:
    id: str
    description: str
    documents: List[Document]
    questions: List[Question]
    sources: List[str] = field(default_factory=list)

    def subset(self, question_ids: Optional[Sequence[str]] = None) -> "Dataset":
        if question_ids is None:
            return self
        wanted = set(question_ids)
        return Dataset(
            self.id,
            self.description,
            self.documents,
            [q for q in self.questions if q.id in wanted],
            self.sources,
        )


def _check_question(q: Question, doc_ids: set) -> None:
    if q.answer_type not in ANSWER_TYPES:
        raise ValueError(f"{q.id}: answer_type {q.answer_type!r} not in {ANSWER_TYPES}")
    if not q.evidence:
        raise ValueError(f"{q.id}: no evidence")
    for ev in q.evidence:
        if ev.get("doc") not in doc_ids:
            raise ValueError(
                f"{q.id}: evidence names unknown document {ev.get('doc')!r}"
            )
        if ev.get("page") is None and not ev.get("quote"):
            raise ValueError(f"{q.id}: evidence needs a page or a quote")


def load_labelled(name: str, offline: bool = False) -> Dataset:
    """Load ``eval/retrieval/datasets/<name>.json`` and fetch its documents."""
    path = DATASETS_DIR / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Labelled dataset not found: {path}")
    spec = json.loads(path.read_text(encoding="utf-8"))
    documents: List[Document] = []
    used_sources = set()
    for d in spec["documents"]:
        if "path" in d:
            local = sources.REPO_ROOT / d["path"]
            if not local.is_file():
                raise FileNotFoundError(
                    f"{name}: document {d['id']} missing at {local}"
                )
            documents.append(Document(d["id"], local, "repo"))
        else:
            local = sources.fetch(d["source"], d["file"], offline=offline)
            documents.append(Document(d["id"], local, d["source"]))
            used_sources.add(d["source"])
    doc_ids = {d.id for d in documents}
    questions = []
    for q in spec["questions"]:
        question = Question(
            id=q["id"],
            dataset=spec["id"],
            question=q["question"],
            answer=q["answer"],
            answer_type=q["answer_type"],
            evidence=[{"doc": q["doc"], **e} for e in q["evidence"]],
            aliases=q.get("answer_aliases", []),
            tags=q.get("tags", []),
            language=q.get("language", "en"),
        )
        _check_question(question, doc_ids)
        questions.append(question)
    return Dataset(
        spec["id"], spec["description"], documents, questions, sorted(used_sources)
    )


def load_financebench(
    offline: bool = False, max_docs: Optional[int] = None, extra_docs: int = 0
) -> Dataset:
    """FinanceBench's 150 open-source questions over the filings they cite.

    Evidence pages are zero-indexed upstream; GAIA's ``[Page N]`` is one-indexed.
    ``max_docs`` keeps the questions of the first N documents (by name);
    ``extra_docs`` adds that many uncited filings as same-domain distractors.
    """
    qpath = sources.fetch(
        "financebench", "data/financebench_open_source.jsonl", offline=offline
    )
    rows = [
        json.loads(line)
        for line in qpath.read_text(encoding="utf-8").splitlines()
        if line
    ]
    cited = sorted(
        {r["doc_name"] for r in rows}
        | {e["doc_name"] for r in rows for e in r["evidence"]}
    )
    if max_docs is not None:
        cited = cited[:max_docs]
    keep = set(cited)
    rows = [
        r
        for r in rows
        if {r["doc_name"], *(e["doc_name"] for e in r["evidence"])} <= keep
    ]
    available = [
        r[len("pdfs/") : -len(".pdf")]
        for r in sources.source_files("financebench", "pdfs/")
    ]
    distractors = [d for d in available if d not in keep][:extra_docs]
    names = cited + distractors
    paths = sources.fetch_many(
        [("financebench", f"pdfs/{n}.pdf") for n in names], offline=offline
    )
    documents = [Document(n, p, "financebench") for n, p in zip(names, paths)]
    questions = []
    for r in rows:
        tags = [r["question_type"]]
        if r["question_type"] == "metrics-generated":
            tags.append("table")
        if len(r["evidence"]) > 1:
            tags.append("multi_evidence")
        answer = r["answer"].strip()
        questions.append(
            Question(
                id=r["financebench_id"],
                dataset="financebench",
                question=r["question"],
                answer=answer,
                answer_type="numeric" if is_numeric_answer(answer) else "judge",
                evidence=[
                    {
                        "doc": e["doc_name"],
                        "page": int(e["evidence_page_num"]) + 1,
                        "label_text": e.get("evidence_text", ""),
                    }
                    for e in r["evidence"]
                ],
                tags=tags,
            )
        )
    return Dataset(
        "financebench",
        "FinanceBench open-source sample: questions over real SEC filings, evidence by page.",
        documents,
        questions,
        ["financebench"],
    )


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60]


def _answer_sentence(context: str, start: int, answer: str) -> str:
    left = max(
        context.rfind(". ", 0, start),
        context.rfind("。", 0, start),
        context.rfind("।", 0, start),
    )
    end_candidates = [
        i
        for i in (
            context.find(". ", start + len(answer)),
            context.find("。", start + len(answer)),
            context.find("।", start + len(answer)),
        )
        if i >= 0
    ]
    right = min(end_candidates) + 1 if end_candidates else len(context)
    return context[left + 1 if left >= 0 else 0 : right].strip()


def load_xquad(language: str, per_language: int, offline: bool = False) -> Dataset:
    """One XQuAD language: its 48 articles as documents, ``per_language`` questions.

    Questions are drawn one per paragraph, round-robin across articles, so they
    spread over many documents. Ids are shared across languages (XQuAD is
    parallel), suffixed with the language.
    """
    if language not in XQUAD_LANGUAGES:
        raise ValueError(
            f"XQuAD has no language {language!r}; choose from {XQUAD_LANGUAGES}"
        )
    raw = sources.fetch("xquad", f"xquad.{language}.json", offline=offline)
    articles = json.loads(raw.read_text(encoding="utf-8"))["data"]
    out_dir = sources.cache_dir() / "xquad" / "docs" / language
    out_dir.mkdir(parents=True, exist_ok=True)
    documents = []
    for i, article in enumerate(articles):
        doc_id = f"xquad_{language}_{i:02d}"
        body = "\n\n".join(p["context"].strip() for p in article["paragraphs"])
        path = out_dir / f"{i:02d}_{_slug(article['title'])}.md"
        content = f"# {article['title'].replace('_', ' ')}\n\n{body}\n"
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.write_text(content, encoding="utf-8")
        documents.append(Document(doc_id, path, "xquad"))
    questions = []
    depth = 0
    while len(questions) < per_language:
        added = False
        for i, article in enumerate(articles):
            if depth >= len(article["paragraphs"]) or len(questions) >= per_language:
                continue
            paragraph = article["paragraphs"][depth]
            qa = paragraph["qas"][0]
            answer = qa["answers"][0]
            questions.append(
                Question(
                    id=f"xquad-{qa['id']}-{language}",
                    dataset=f"xquad_{language}",
                    question=qa["question"],
                    answer=answer["text"],
                    answer_type="exact",
                    evidence=[
                        {
                            "doc": f"xquad_{language}_{i:02d}",
                            "quote": _answer_sentence(
                                paragraph["context"],
                                answer["answer_start"],
                                answer["text"],
                            ),
                            "near": paragraph["context"][:80],
                        }
                    ],
                    tags=["multilingual", language],
                    language=language,
                )
            )
            added = True
        if not added:
            break
        depth += 1
    return Dataset(
        f"xquad_{language}",
        f"XQuAD ({language}): Wikipedia articles and questions in one language.",
        documents,
        questions,
        ["xquad"],
    )


def wikipedia_distractors(count: int, offline: bool = False) -> List[Document]:
    """``count`` Simple English Wikipedia articles (2–20k chars), deterministic order."""
    if count <= 0:
        return []
    parquet = sources.fetch(
        "wikipedia", "20231101.simple/train-00000-of-00001.parquet", offline=offline
    )
    out_dir = sources.cache_dir() / "wikipedia" / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    import pyarrow.parquet as pq  # pylint: disable=import-outside-toplevel

    documents: List[Document] = []
    for batch in pq.ParquetFile(parquet).iter_batches(
        batch_size=4096, columns=["id", "title", "text"]
    ):
        for row in batch.to_pylist():
            if not 2000 <= len(row["text"]) <= 20000:
                continue
            path = out_dir / f"{int(row['id']):08d}_{_slug(row['title'])}.md"
            if not path.exists():
                path.write_text(
                    f"# {row['title']}\n\n{row['text']}\n", encoding="utf-8"
                )
            documents.append(Document(f"wiki_{path.stem}", path, "wikipedia"))
            if len(documents) == count:
                return documents
    raise RuntimeError(
        f"Only {len(documents)} Wikipedia articles of 2-20k chars are available; "
        f"asked for {count}."
    )
