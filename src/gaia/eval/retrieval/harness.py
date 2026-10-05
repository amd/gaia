# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Drive the production RAG stack over a corpus and score what it retrieves.

Two retrieval pipelines are measured, because users get both:

* ``sdk`` — :meth:`RAGSDK._search_chunks`, plain dense top-k (what
  ``RAGSDK.query`` puts in front of the model);
* ``agent`` — the ``query_documents`` tool exactly as the chat agents run it:
  search-key expansion, keyword boosting, and the adaptive chunk cutoff.
"""

from __future__ import annotations

import gc
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from gaia.eval.retrieval.corpus import Dataset, Document, Question
from gaia.eval.retrieval.locate import (
    ChunkSpan,
    DocText,
    Evidence,
    LabelError,
    chunk_hits,
    locate_chunks,
    resolve_evidence,
    token_overlap,
)

#: Dense candidates scored per question; recall@k is reported up to this depth.
DENSE_DEPTH = 20
#: The chat agents' RAG config (``ChatAgentConfig.max_chunks``).
AGENT_MAX_CHUNKS = 5
#: Unlocatable chunks above this share mean the locator no longer understands
#: the chunker, so every score would be wrong.
MAX_UNLOCATED_SHARE = 0.01


class IndexingError(RuntimeError):
    """A document the benchmark depends on failed to index."""


class _RssSampler:
    """Peak resident memory of this process while a block runs."""

    def __init__(self, interval: float = 0.2):
        import psutil  # pylint: disable=import-outside-toplevel

        self._proc = psutil.Process(os.getpid())
        self._interval = interval
        self._stop = threading.Event()
        self.start_rss = self._proc.memory_info().rss
        self.peak_rss = self.start_rss
        self.end_rss = self.start_rss
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.wait(self._interval):
            self.peak_rss = max(self.peak_rss, self._proc.memory_info().rss)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        self.peak_rss = max(self.peak_rss, self._proc.memory_info().rss)
        self.end_rss = self._proc.memory_info().rss


def _dir_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def make_rag(
    cache_dir: Path, allowed: Sequence[Path], model: Optional[str] = None, **overrides
):
    """A RAGSDK sized so nothing is evicted mid-benchmark; overrides win."""
    from gaia.rag.sdk import (  # pylint: disable=import-outside-toplevel
        RAGSDK,
        RAGConfig,
    )

    settings = dict(
        cache_dir=str(cache_dir),
        allowed_paths=[str(p) for p in allowed],
        max_indexed_files=1_000_000,
        max_total_chunks=100_000_000,
        enable_lru_eviction=False,
        max_chunks=AGENT_MAX_CHUNKS,
        show_stats=False,
    )
    if model:
        settings["model"] = model
    settings.update(overrides)
    return RAGSDK(RAGConfig(**settings))


def agent_query_tool(rag) -> Callable[[str], dict]:
    """The registered ``query_documents`` tool, bound to ``rag`` as a chat agent binds it."""
    try:
        from gaia_agent_chat.agent import (  # pylint: disable=import-outside-toplevel
            ChatAgent,
        )
    except ImportError as e:
        raise ImportError(
            "The agent retrieval pipeline needs the chat agent package "
            "(hub/agents/chat/python): run `uv pip install -e hub/agents/chat/python`."
        ) from e
    from gaia.agents.base.tools import (  # pylint: disable=import-outside-toplevel
        _TOOL_REGISTRY,
    )
    from gaia.agents.tools.rag_tools import (  # pylint: disable=import-outside-toplevel
        RAGToolsMixin,
    )

    class _Host(RAGToolsMixin):
        _generate_search_keys = (
            ChatAgent._generate_search_keys  # pylint: disable=protected-access
        )

        def __init__(self):
            self.rag = rag
            self.max_chunks = AGENT_MAX_CHUNKS
            self.debug = False

    _Host().register_rag_tools()
    return _TOOL_REGISTRY["query_documents"]["function"]


@dataclass
class QuestionResult:
    id: str
    dataset: str
    tags: List[str]
    language: str
    answer_type: str
    sdk_rank: Optional[int] = None
    sdk_doc_rank: Optional[int] = None
    agent_rank: Optional[int] = None
    agent_returned: int = 0
    sdk_latency_s: float = 0.0
    agent_latency_s: float = 0.0
    label_overlap: Optional[float] = None
    evidence_not_indexed: bool = False
    answer: Optional[dict] = None


@dataclass
class BenchIndex:
    """One RAGSDK index over a set of documents, plus the maps scoring needs."""

    workdir: Path
    documents: List[Document]
    model: Optional[str] = None
    rag_overrides: dict = field(default_factory=dict)
    rag: Any = None
    doc_paths: Dict[str, str] = field(default_factory=dict)
    texts: Dict[str, DocText] = field(default_factory=dict)
    spans: Dict[int, ChunkSpan] = field(default_factory=dict)
    unlocated: List[int] = field(default_factory=list)
    build_stats: dict = field(default_factory=dict)
    _agent_tool: Optional[Callable] = None
    _by_text: Dict[Tuple[str, str], List[int]] = field(default_factory=dict)
    _resolved: Dict[str, str] = field(default_factory=dict)

    def build(
        self,
        required: Optional[set] = None,
        on_progress: Optional[Callable[[int, int, Document, dict], None]] = None,
    ) -> dict:
        """Index every document; raise if any document in ``required`` fails."""
        cache = self.workdir / "rag-cache"
        cache.mkdir(parents=True, exist_ok=True)
        allowed = sorted(
            {d.path.parent.resolve() for d in self.documents} | {self.workdir.resolve()}
        )
        self.rag = make_rag(cache, allowed, self.model, **self.rag_overrides)
        rag = self.rag
        load_start = time.perf_counter()
        rag._load_embedder()  # pylint: disable=protected-access
        embedder_load_s = time.perf_counter() - load_start

        gc.collect()  # a previous index must not count toward this one's memory
        failures, per_doc = [], []
        with _RssSampler() as rss:
            start = time.perf_counter()
            for i, doc in enumerate(self.documents, 1):
                t0 = time.perf_counter()
                stats = rag.index_document(str(doc.path))
                elapsed = time.perf_counter() - t0
                per_doc.append(
                    {
                        "doc": doc.id,
                        "seconds": round(elapsed, 3),
                        "chunks": stats.get("num_chunks", 0),
                        "pages": stats.get("num_pages"),
                        "from_cache": bool(stats.get("from_cache")),
                        "bytes": doc.path.stat().st_size,
                    }
                )
                if not stats.get("success"):
                    # A refusal is a measurement: its questions score as misses.
                    failures.append(
                        {
                            "doc": doc.id,
                            "required": bool(required and doc.id in required),
                            "status": stats.get("pdf_status"),
                            "error": str(stats.get("error", "unknown"))[:300],
                        }
                    )
                else:
                    self.doc_paths[doc.id] = str(Path(doc.path).absolute())
                if on_progress:
                    on_progress(i, len(self.documents), doc, stats)
            index_s = time.perf_counter() - start

        if rag.index is None:
            raise IndexingError(
                f"No document indexed under {self.workdir}: {failures[:3]}"
            )
        self._locate()
        faiss_bytes = int(rag.index.ntotal) * int(rag.index.d) * 4
        per_file_bytes = sum(
            int(ix.ntotal) * int(ix.d) * 4 for ix in rag.file_indices.values()
        )
        self.build_stats = {
            "documents": len(self.documents),
            "indexed": len(rag.indexed_files),
            "failed": failures,
            "required_failed": sum(1 for f in failures if f["required"]),
            "chunks": len(rag.chunks),
            "embedding_dim": int(rag.index.d),
            "embedder_load_s": round(embedder_load_s, 2),
            "index_s": round(index_s, 2),
            "docs_per_s": round(len(self.documents) / index_s, 3) if index_s else None,
            "chunks_per_s": round(len(rag.chunks) / index_s, 2) if index_s else None,
            "rss_start_mb": round(rss.start_rss / 2**20, 1),
            "rss_peak_mb": round(rss.peak_rss / 2**20, 1),
            "rss_end_mb": round(rss.end_rss / 2**20, 1),
            "rss_growth_mb": round((rss.peak_rss - rss.start_rss) / 2**20, 1),
            "vectors_global_mb": round(faiss_bytes / 2**20, 2),
            "vectors_per_file_mb": round(per_file_bytes / 2**20, 2),
            "chunk_text_mb": round(
                sum(len(c.encode("utf-8")) for c in rag.chunks) / 2**20, 2
            ),
            "cache_on_disk_mb": round(_dir_bytes(cache) / 2**20, 2),
            "unlocated_chunks": len(self.unlocated),
            "per_document": per_doc,
        }
        return self.build_stats

    def _locate(self) -> None:
        rag = self.rag
        for path in rag.file_to_chunk_indices:
            full = rag.file_metadata.get(path, {}).get("full_text")
            if full is None:
                raise IndexingError(
                    f"Indexed text for {path} is not in memory; cannot score it."
                )
            self.texts[path] = DocText.from_full_text(path, full)
        self.spans, self.unlocated = locate_chunks(
            rag.chunks, rag.file_to_chunk_indices, self.texts
        )
        if rag.chunks and len(self.unlocated) / len(rag.chunks) > MAX_UNLOCATED_SHARE:
            sample = [rag.chunks[i][:80] for i in self.unlocated[:3]]
            raise IndexingError(
                f"{len(self.unlocated)}/{len(rag.chunks)} chunks could not be located in "
                f"their document's extracted text (e.g. {sample}). The chunker changed "
                "shape; update gaia.eval.retrieval.locate before trusting any score."
            )
        self._by_text = {}
        self._resolved = {str(Path(p).resolve()): p for p in self.texts}
        for idx, span in self.spans.items():
            self._by_text.setdefault((span.path, rag.chunks[idx]), []).append(idx)

    # ── evidence ────────────────────────────────────────────────────────────

    def evidence_for(self, q: Question) -> Tuple[List[Evidence], Optional[float]]:
        """Resolve a question's labels against the indexed text.

        Returns the evidence and, for page-only labels that carry the annotator's
        text, how much of it appears on the labelled page (a label sanity check).
        """
        resolved, overlaps = [], []
        for ev in q.evidence:
            if ev["doc"] not in self.doc_paths:
                continue  # refused at indexing; recorded in build_stats["failed"]
            path = self.doc_paths[ev["doc"]]
            doc = self.texts[path]
            page = ev.get("page")
            if page is not None and not doc.paged:
                raise LabelError(
                    f"{q.id}: {ev['doc']} has no pages but the label names page {page}"
                )
            hint = 0
            if ev.get("near"):
                hint = doc.text.find(" ".join(ev["near"].split()))
                if hint < 0:
                    raise LabelError(
                        f"{q.id}: 'near' text is not in {ev['doc']} as indexed"
                    )
            resolved.append(
                resolve_evidence(Evidence(path, page, ev.get("quote")), doc, hint)
            )
            if ev.get("label_text") and page is not None:
                overlaps.append(
                    token_overlap(ev["label_text"][:2000], doc.page_text(page))
                )
        return resolved, (round(max(overlaps), 3) if overlaps else None)

    def _rank(
        self, indices: Sequence[int], evidence: Sequence[Evidence]
    ) -> Optional[int]:
        for rank, idx in enumerate(indices, 1):
            if chunk_hits(self.spans.get(idx), evidence):
                return rank
        return None

    def _doc_rank(
        self, indices: Sequence[int], evidence: Sequence[Evidence]
    ) -> Optional[int]:
        wanted = {e.path for e in evidence}
        for rank, idx in enumerate(indices, 1):
            span = self.spans.get(idx)
            if span is not None and span.path in wanted:
                return rank
        return None

    # ── pipelines ───────────────────────────────────────────────────────────

    def _cold(self) -> None:
        # The query-embedding cache would make every repeat a free lookup.
        self.rag._embedding_cache = None  # pylint: disable=protected-access

    def sdk_search(
        self, query: str, depth: int = DENSE_DEPTH
    ) -> Tuple[List[int], float]:
        rag = self.rag
        self._cold()
        saved = rag.config.max_chunks
        rag.config.max_chunks = depth
        try:
            t0 = time.perf_counter()
            snapshot = rag._search_chunks(query)  # pylint: disable=protected-access
            elapsed = time.perf_counter() - t0
        finally:
            rag.config.max_chunks = saved
        return list(snapshot["retrieved_indices"]), elapsed

    def agent_search(self, query: str) -> Tuple[List[int], float]:
        if self._agent_tool is None:
            self._agent_tool = agent_query_tool(self.rag)
        self._cold()
        t0 = time.perf_counter()
        result = self._agent_tool(query)
        elapsed = time.perf_counter() - t0
        if result.get("status") != "success":
            raise RuntimeError(f"query_documents failed for {query[:60]!r}: {result}")
        return [self._agent_chunk_index(c) for c in result.get("chunks", [])], elapsed

    def _agent_chunk_index(self, entry: dict) -> int:
        """The returned chunk's index in the file the tool says it came from.

        The tool finds indices with ``rag.chunks.index(text)``, which returns the
        first copy of duplicated text, possibly in another document.
        """
        idx = entry.get("_debug_chunk_index", -1)
        source = self._resolved.get(
            str(Path(entry.get("source_file") or ".").resolve())
        )
        span = self.spans.get(idx)
        if span is not None and (source is None or span.path == source):
            return idx
        same_text = self._by_text.get((source, entry.get("content", "")), [])
        return same_text[0] if same_text else -1

    def answer(
        self, question: str, keep_history: bool = False
    ) -> Tuple[str, List[int], float]:
        """RAGSDK.query end to end; returns text, the context chunk indices, latency.

        ``keep_history`` leaves RAGSDK's chat history in place; by default it is
        cleared so one question's retrieved context cannot leak into the next.
        """
        self._cold()
        if not keep_history:
            self.rag.chat.clear_history()
        t0 = time.perf_counter()
        response = self.rag.query(question)
        elapsed = time.perf_counter() - t0
        indices = []
        for chunk, meta in zip(response.chunks or [], response.chunk_metadata or []):
            indices.extend(self._by_text.get((meta.get("source_path"), chunk), [])[:1])
        return response.text, indices, elapsed

    def score_retrieval(self, q: Question, pipelines: Sequence[str]) -> QuestionResult:
        evidence, overlap = self.evidence_for(q)
        result = QuestionResult(
            q.id, q.dataset, q.tags, q.language, q.answer_type, label_overlap=overlap
        )
        result.evidence_not_indexed = any(d not in self.doc_paths for d in q.doc_ids)
        if "sdk" in pipelines:
            ranked, result.sdk_latency_s = self.sdk_search(q.question)
            result.sdk_rank = self._rank(ranked, evidence)
            result.sdk_doc_rank = self._doc_rank(ranked, evidence)
        if "agent" in pipelines:
            ranked, result.agent_latency_s = self.agent_search(q.question)
            result.agent_rank = self._rank(ranked, evidence)
            result.agent_returned = len(ranked)
        return result

    def context_has_evidence(self, q: Question, indices: Sequence[int]) -> bool:
        evidence, _ = self.evidence_for(q)
        return self._rank(indices, evidence) is not None


def index_dataset(
    dataset: Dataset,
    workdir: Path,
    extra_documents: Sequence[Document] = (),
    model: Optional[str] = None,
    on_progress=None,
    **rag_overrides,
) -> BenchIndex:
    required = {d for q in dataset.questions for d in q.doc_ids}
    bench = BenchIndex(
        workdir, list(dataset.documents) + list(extra_documents), model, rag_overrides
    )
    bench.build(required=required, on_progress=on_progress)
    return bench
