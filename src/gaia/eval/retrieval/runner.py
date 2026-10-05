# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia eval retrieval``: run a suite, write results.json + report.md, gate on a baseline."""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from gaia.eval.retrieval import hard_cases, sources
from gaia.eval.retrieval.corpus import (
    Dataset,
    Question,
    load_financebench,
    load_labelled,
    load_xquad,
    wikipedia_distractors,
)
from gaia.eval.retrieval.harness import (
    DENSE_DEPTH,
    BenchIndex,
    QuestionResult,
    index_dataset,
)
from gaia.eval.retrieval.scoring import (
    exact_match,
    latency_summary,
    numeric_match,
    retrieval_summary,
    token_f1,
)

RESULTS_SCHEMA = 1
DEFAULT_XQUAD_LANGUAGES = ("en", "de", "es", "ru", "zh", "ar", "hi")


@dataclass
class Suite:
    name: str
    datasets: Sequence[str]
    answers: bool
    scale_tiers: Sequence[int]
    hard_cases: Sequence[Callable]
    xquad_languages: Sequence[str] = DEFAULT_XQUAD_LANGUAGES
    xquad_per_language: int = 40
    # PDF images are read only through the VLM, so whether it runs changes the chunks.
    vlm: bool = True


SUITES: Dict[str, Suite] = {
    # Offline, deterministic (no LLM generation): gates pull requests.
    "pr": Suite(
        "pr",
        ("repo_docs",),
        answers=False,
        scale_tiers=(),
        hard_cases=hard_cases.PR_CASES,
        vlm=False,
    ),
    # Everything, with downloads and answer generation: nightly.
    "full": Suite(
        "full",
        ("repo_docs", "technical_docs", "financebench", "xquad"),
        answers=True,
        scale_tiers=(10, 100, 1000),
        hard_cases=hard_cases.FULL_CASES,
    ),
}


@dataclass
class RunOptions:
    suite: Suite
    out_dir: Path
    model: Optional[str] = None
    datasets: Optional[Sequence[str]] = None
    answers: Optional[bool] = None
    judge: bool = False
    judge_model: Optional[str] = None
    scale_tiers: Optional[Sequence[int]] = None
    run_hard_cases: bool = True
    xquad_languages: Optional[Sequence[str]] = None
    xquad_per_language: Optional[int] = None
    financebench_docs: Optional[int] = None
    offline: bool = False
    keep_work: bool = False
    vlm: Optional[bool] = None
    rag_overrides: dict = field(default_factory=dict)
    log: Callable[[str], None] = print
    errors: List[str] = field(default_factory=list)


# ── metadata ────────────────────────────────────────────────────────────────


def _git_sha() -> Optional[str]:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=sources.repo_root(),
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _cpu_name() -> str:
    if platform.system() == "Windows":
        try:
            out = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_Processor).Name",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                check=True,
            ).stdout.strip()
            if out:
                return out.splitlines()[0].strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return platform.processor() or platform.machine()


def _machine() -> dict:
    import psutil  # pylint: disable=import-outside-toplevel

    return {
        "cpu": _cpu_name(),
        "logical_cpus": psutil.cpu_count(),
        "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "python": platform.python_version(),
    }


def _embedder_info(bench: BenchIndex) -> dict:
    rag = bench.rag
    info = {"model": rag.config.embedding_model}
    health = rag.llm_client.health_check()
    for entry in health.get("all_models_loaded", []) or []:
        name = str(entry.get("model_name", "")) + str(entry.get("checkpoint", ""))
        if (
            rag.config.embedding_model.replace("user.", "") in name
            or "embed" in name.lower()
        ):
            info.update({k: entry.get(k) for k in ("checkpoint", "device", "recipe")})
            break
    return info


class VlmError(RuntimeError):
    """The VLM is meant to read PDF images but cannot."""


def _vlm_info(enabled: bool) -> dict:
    """Record the VLM state; when enabled, prove it reads an image or raise."""
    model = _rag_defaults()["vlm_model"]
    if not enabled:
        return {"model": model, "enabled": False}
    import io  # pylint: disable=import-outside-toplevel

    from PIL import Image, ImageDraw  # pylint: disable=import-outside-toplevel

    from gaia.llm import VLMClient  # pylint: disable=import-outside-toplevel

    vlm = VLMClient(vlm_model=model)
    if not vlm.check_availability():
        raise VlmError(
            f"The VLM {model} is not available, so PDF images would go unread. Pull it "
            "with Lemonade's model manager, or pass --no-vlm to measure text-only extraction."
        )
    image = Image.new("RGB", (480, 120), "white")
    ImageDraw.Draw(image).text((20, 45), "GAIA RETRIEVAL PROBE 4271", fill="black")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    try:
        text = vlm.extract_from_image(buf.getvalue(), image_num=1, page_num=1)
    except Exception as e:  # pylint: disable=broad-except
        raise VlmError(
            f"The VLM {model} is installed but failed to read a test image: {e}. Every PDF "
            "image would fail the same way and be indexed as blank. Fix the VLM backend "
            "(see the Lemonade log), or pass --no-vlm to measure text-only extraction."
        ) from e
    if "4271" not in text:
        raise VlmError(
            f"The VLM {model} answered but did not read the test image (got {text[:120]!r}). "
            "PDF image text would be wrong or missing; fix the VLM or pass --no-vlm."
        )
    return {"model": model, "enabled": True}


def _rag_defaults() -> dict:
    from gaia.rag.sdk import RAGConfig  # pylint: disable=import-outside-toplevel

    cfg = RAGConfig()
    return {
        k: getattr(cfg, k)
        for k in (
            "model",
            "embedding_model",
            "chunk_size",
            "chunk_overlap",
            "max_chunks",
            "max_indexed_files",
            "max_total_chunks",
            "vlm_model",
        )
    }


# ── datasets ────────────────────────────────────────────────────────────────


def _load_datasets(opts: RunOptions) -> List[Dataset]:
    names = opts.datasets or opts.suite.datasets
    out = []
    for name in names:
        if name == "financebench":
            out.append(load_financebench(opts.offline, max_docs=opts.financebench_docs))
        elif name == "xquad":
            langs = opts.xquad_languages or opts.suite.xquad_languages
            per = opts.xquad_per_language or opts.suite.xquad_per_language
            out.extend(load_xquad(lang, per, opts.offline) for lang in langs)
        else:
            out.append(load_labelled(name, opts.offline))
    return out


# ── answers ─────────────────────────────────────────────────────────────────

_JUDGE_PROMPT = """You grade answers to questions about documents.
Question: {question}
Reference answer: {gold}
Candidate answer: {pred}

Is the candidate answer correct — does it state the same facts or figures as the
reference (rounding and wording may differ; extra correct detail is fine; a
refusal or a different figure is wrong)? Reply with exactly one JSON object:
{{"correct": true}} or {{"correct": false}}"""


class _Judge:
    def __init__(self, model: Optional[str]):
        from gaia.eval.judge_client import (  # pylint: disable=import-outside-toplevel
            make_judge_client,
        )

        self.client = make_judge_client(model=model)

    def __call__(self, q: Question, prediction: str) -> bool:
        blocks = self.client.get_completion(
            _JUDGE_PROMPT.format(question=q.question, gold=q.answer, pred=prediction)
        )
        text = "".join(getattr(b, "text", "") for b in blocks)
        match = re.search(r"\{[^{}]*\"correct\"\s*:\s*(true|false)[^{}]*\}", text)
        if not match:
            raise RuntimeError(f"Judge reply for {q.id} has no verdict: {text[:200]!r}")
        return match.group(1) == "true"


def _grade(q: Question, prediction: str, judge: Optional[_Judge]) -> Optional[bool]:
    if q.answer_type == "numeric":
        return numeric_match(q.answer, prediction)
    if q.answer_type == "exact":
        return exact_match(q.answer, q.aliases, prediction)
    return judge(q, prediction) if judge else None


def _outcome(correct: Optional[bool], in_context: bool) -> str:
    if correct is None:
        return "unscored"
    if correct:
        return "correct" if in_context else "correct_without_evidence"
    return "generation_miss" if in_context else "retrieval_miss"


# ── aggregation ─────────────────────────────────────────────────────────────


def _summarize(results: Sequence[QuestionResult]) -> dict:
    out: dict = {
        "questions": len(results),
        "evidence_not_indexed": sum(1 for r in results if r.evidence_not_indexed),
    }
    if any(r.sdk_latency_s for r in results):
        out["sdk"] = retrieval_summary(r.sdk_rank for r in results)
        out["sdk_document"] = retrieval_summary(
            (r.sdk_doc_rank for r in results), ks=(1, 5)
        )
        out["sdk_latency"] = latency_summary([r.sdk_latency_s for r in results])
    if any(r.agent_latency_s for r in results):
        agent = retrieval_summary(r.agent_rank for r in results)
        agent["recall@returned"] = round(
            sum(1 for r in results if r.agent_rank is not None) / len(results), 4
        )
        agent["mean_returned"] = round(
            sum(r.agent_returned for r in results) / len(results), 2
        )
        out["agent"] = agent
        out["agent_latency"] = latency_summary([r.agent_latency_s for r in results])
    answered = [r.answer for r in results if r.answer]
    if answered:
        counts: Dict[str, int] = {}
        for a in answered:
            counts[a["outcome"]] = counts.get(a["outcome"], 0) + 1
        scored = [a for a in answered if a["correct"] is not None]
        out["answers"] = {
            "answered": len(answered),
            "scored": len(scored),
            "accuracy": (
                round(sum(a["correct"] for a in scored) / len(scored), 4)
                if scored
                else None
            ),
            "outcomes": counts,
            "latency": latency_summary([a["latency_s"] for a in answered]),
        }
    overlaps = [r.label_overlap for r in results if r.label_overlap is not None]
    if overlaps:
        out["label_check"] = {
            "page_labels": len(overlaps),
            "annotator_text_on_page_ge_0.5": round(
                sum(o >= 0.5 for o in overlaps) / len(overlaps), 4
            ),
        }
    return out


def _by_tag(results: Sequence[QuestionResult]) -> dict:
    tags: Dict[str, List[QuestionResult]] = {}
    for r in results:
        for t in set(r.tags) | {f"lang:{r.language}"}:
            tags.setdefault(t, []).append(r)
    out = {}
    for tag, rs in sorted(tags.items()):
        entry = {"questions": len(rs)}
        if any(r.sdk_latency_s for r in rs):
            entry["sdk_recall@5"] = retrieval_summary(r.sdk_rank for r in rs)[
                "recall@5"
            ]
        if any(r.agent_latency_s for r in rs):
            entry["agent_recall@returned"] = round(
                sum(r.agent_rank is not None for r in rs) / len(rs), 4
            )
        scored = [r.answer for r in rs if r.answer and r.answer["correct"] is not None]
        if scored:
            entry["answer_accuracy"] = round(
                sum(a["correct"] for a in scored) / len(scored), 4
            )
        out[tag] = entry
    return out


# ── phases ──────────────────────────────────────────────────────────────────


def _progress(log, label):
    def report(i, n, _doc, stats):
        if i == n or i % 25 == 0 or not stats.get("success"):
            mark = (
                ""
                if stats.get("success")
                else f"  FAILED: {str(stats.get('error'))[:120]}"
            )
            log(f"    [{label}] indexed {i}/{n}{mark}")

    return report


def _run_dataset(
    ds: Dataset, work: Path, opts: RunOptions, answers: bool, judge
) -> dict:
    log = opts.log
    log(
        f"[DATASET] {ds.id}: {len(ds.documents)} documents, {len(ds.questions)} questions"
    )
    bench = index_dataset(
        ds,
        work / ds.id,
        model=opts.model,
        on_progress=_progress(log, ds.id),
        **opts.rag_overrides,
    )
    stats = dict(bench.build_stats)
    log(
        f"    indexed {stats['indexed']}/{stats['documents']} docs, {stats['chunks']} chunks "
        f"in {stats['index_s']}s"
    )
    results: List[QuestionResult] = []
    for i, q in enumerate(ds.questions, 1):
        r = bench.score_retrieval(q, ("sdk", "agent"))
        if answers:
            text, indices, latency = bench.answer(q.question)
            in_context = bench.context_has_evidence(q, indices)
            correct = _grade(q, text, judge)
            r.answer = {
                "text": text[:1200],
                "correct": correct,
                "in_context": in_context,
                "outcome": _outcome(correct, in_context),
                "latency_s": round(latency, 3),
                "token_f1": (
                    round(token_f1(q.answer, text), 3)
                    if q.answer_type == "exact"
                    else None
                ),
            }
        results.append(r)
        if i % 10 == 0 or i == len(ds.questions):
            log(f"    scored {i}/{len(ds.questions)}")
    return {
        "dataset": ds.id,
        "description": ds.description,
        "sources": ds.sources,
        "index": stats,
        "embedder": _embedder_info(bench),
        "summary": _summarize(results),
        "by_tag": _by_tag(results),
        "questions": [asdict(r) for r in results],
    }


def _run_scale(work: Path, opts: RunOptions, tiers: Sequence[int]) -> dict:
    log = opts.log
    repo = load_labelled("repo_docs", opts.offline)
    counts: Dict[str, int] = {}
    for q in repo.questions:
        for d in q.doc_ids:
            counts[d] = counts.get(d, 0) + 1
    target_ids = sorted(counts, key=lambda d: (-counts[d], d))[: min(tiers)]
    targets = [d for d in repo.documents if d.id in target_ids]
    questions = [q for q in repo.questions if set(q.doc_ids) <= set(target_ids)]
    distractors = wikipedia_distractors(max(tiers) - len(targets), opts.offline)
    out = {
        "targets": target_ids,
        "questions": len(questions),
        "distractor_source": "wikipedia",
        "tiers": [],
    }
    for tier in tiers:
        log(
            f"[SCALE] {tier} documents ({len(targets)} labelled + {tier - len(targets)} distractors)"
        )
        bench = BenchIndex(
            work / f"scale-{tier}",
            targets + distractors[: tier - len(targets)],
            opts.model,
            dict(opts.rag_overrides),
        )
        bench.build(
            required=set(target_ids), on_progress=_progress(log, f"scale-{tier}")
        )
        results = [bench.score_retrieval(q, ("sdk", "agent")) for q in questions]
        stats = {k: v for k, v in bench.build_stats.items() if k != "per_document"}
        slowest = sorted(
            bench.build_stats["per_document"], key=lambda d: -d["seconds"]
        )[:5]
        summary = _summarize(results)
        out["tiers"].append(
            {
                "documents": tier,
                "index": stats,
                "slowest_documents": slowest,
                "retrieval": summary,
            }
        )
        log(
            f"    {stats['chunks']} chunks, index {stats['index_s']}s, RSS +{stats['rss_growth_mb']} MB, "
            f"sdk p95 {summary['sdk_latency']['p95_ms']} ms, agent p95 {summary['agent_latency']['p95_ms']} ms, "
            f"sdk recall@5 {summary['sdk']['recall@5']}"
        )
    return out


def _run_hard_cases(
    work: Path, opts: RunOptions, answers: bool, fb: Optional[Dataset]
) -> List[dict]:
    repo = load_labelled("repo_docs", opts.offline)
    ctx = hard_cases.CaseContext(
        work / "hard-cases",
        repo,
        opts.model,
        answers,
        fb,
        opts.log,
        rag_overrides=dict(opts.rag_overrides),
    )
    out = []
    for case in opts.suite.hard_cases:
        opts.log(f"[HARD CASE] {case.__name__}")
        try:
            result = case(ctx)
        except Exception as e:  # pylint: disable=broad-except
            msg = f"{case.__name__}: {type(e).__name__}: {e}"
            opts.errors.append(msg)
            result = {
                "id": case.__name__,
                "title": case.__name__,
                "status": "error",
                "finding": msg,
                "details": {"traceback": traceback.format_exc()[-3000:]},
            }
        opts.log(f"    {result['status'].upper()}: {result['finding']}")
        out.append(result)
    return out


# ── entry point ─────────────────────────────────────────────────────────────


def run(opts: RunOptions) -> dict:
    suite = opts.suite
    answers = suite.answers if opts.answers is None else opts.answers
    tiers = list(suite.scale_tiers if opts.scale_tiers is None else opts.scale_tiers)
    judge = _Judge(opts.judge_model) if (opts.judge and answers) else None
    vlm = suite.vlm if opts.vlm is None else opts.vlm
    vlm_meta = _vlm_info(vlm)
    opts.rag_overrides = {**opts.rag_overrides, "use_vlm": vlm}
    if tiers:
        try:
            import pyarrow.parquet  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
        except ImportError as e:
            raise ImportError(
                "The scale tiers read Wikipedia distractors from parquet and need pyarrow: "
                'run `uv pip install -e ".[eval]"`, or pass --no-scale.'
            ) from e
    opts.out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="gaia-retrieval-"))
    started = time.time()
    datasets = _load_datasets(opts)
    results = {
        "schema": RESULTS_SCHEMA,
        "meta": {
            "suite": suite.name,
            "component": "rag",
            "started": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
            "git_sha": _git_sha(),
            "machine": _machine(),
            "llm_model": opts.model or _rag_defaults()["model"],
            "answers": answers,
            "judge": bool(judge),
            "rag_defaults": _rag_defaults(),
            "vlm": vlm_meta,
            "dense_depth": DENSE_DEPTH,
            "sources": sources.license_notes(s for ds in datasets for s in ds.sources),
        },
        "datasets": [],
    }
    try:
        for ds in datasets:
            results["datasets"].append(_run_dataset(ds, work, opts, answers, judge))
        if results["datasets"]:
            results["meta"]["embedder"] = results["datasets"][0]["embedder"]
        if tiers:
            results["scale"] = _run_scale(work, opts, tiers)
        if opts.run_hard_cases and suite.hard_cases:
            fb = next((d for d in datasets if d.id == "financebench"), None)
            results["hard_cases"] = _run_hard_cases(work, opts, answers, fb)
    finally:
        results["meta"]["duration_s"] = round(time.time() - started, 1)
        results["meta"]["errors"] = opts.errors
        (opts.out_dir / "results.json").write_text(
            json.dumps(results, indent=1, default=str), encoding="utf-8"
        )
        (opts.out_dir / "report.md").write_text(
            render_markdown(results), encoding="utf-8"
        )
        if opts.keep_work:
            opts.log(f"[WORK] kept index caches at {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    return results


# ── report ──────────────────────────────────────────────────────────────────


def _pct(v) -> str:
    return "-" if v is None else f"{v * 100:.1f}%"


def _weighted(entries: Sequence[dict], key: str) -> Optional[float]:
    vals = [(e[key], e["questions"]) for e in entries if key in e]
    return sum(v * w for v, w in vals) / sum(w for _, w in vals) if vals else None


def render_markdown(results: dict) -> str:
    meta = results["meta"]
    m = meta.get("machine", {})
    emb = meta.get("embedder", {})
    lines = [
        f"# RAG retrieval benchmark — suite `{meta['suite']}`",
        "",
        f"- Run {meta['started']} on {m.get('cpu')} ({m.get('logical_cpus')} threads, "
        f"{m.get('ram_gb')} GB), {m.get('os')}",
        f"- Commit `{meta.get('git_sha')}`, {meta.get('duration_s')} s",
        f"- Embedder `{emb.get('model')}` on {emb.get('device')} ({emb.get('checkpoint')}); "
        f"answers by `{meta['llm_model']}`"
        + ("" if meta["answers"] else " (not run: retrieval only)"),
        f"- VLM `{meta.get('vlm', {}).get('model')}`: "
        + (
            "on (probed with a test image), PDF images are read"
            if meta.get("vlm", {}).get("enabled")
            else "off, PDF images are not read"
        ),
        f"- RAG defaults: {meta['rag_defaults']}",
        "",
        "## Retrieval and answers",
        "",
        "`sdk` is RAGSDK dense search; `agent` is the chat agents' query_documents tool "
        "(keyword boost, adaptive cutoff). An answer is a *retrieval miss* when the evidence "
        "never reached the model, a *generation miss* when it did and the answer was still wrong.",
        "",
        "| Dataset | Qs | sdk R@1 | sdk R@5 | sdk R@20 | MRR | doc R@5 | agent R@returned (avg n) "
        "| answer acc (scored) | retrieval miss | generation miss | sdk p95 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for d in results["datasets"]:
        s = d["summary"]
        sdk, agent, ans = s.get("sdk", {}), s.get("agent", {}), s.get("answers") or {}
        outcomes = ans.get("outcomes", {})
        lines.append(
            f"| {d['dataset']} | {s['questions']} | {_pct(sdk.get('recall@1'))} | {_pct(sdk.get('recall@5'))} "
            f"| {_pct(sdk.get('recall@20'))} | {sdk.get('mrr', '-')} | {_pct(s.get('sdk_document', {}).get('recall@5'))} "
            f"| {_pct(agent.get('recall@returned'))} ({agent.get('mean_returned', '-')}) "
            f"| {_pct(ans.get('accuracy'))} ({ans.get('scored', 0)}) | {outcomes.get('retrieval_miss', '-')} "
            f"| {outcomes.get('generation_miss', '-')} | {s.get('sdk_latency', {}).get('p95_ms', '-')} ms |"
        )
    tag_rows = {}
    for d in results["datasets"]:
        for tag, entry in d["by_tag"].items():
            tag_rows.setdefault(tag, []).append(entry)
    if tag_rows:
        lines += [
            "",
            "### By tag",
            "",
            "| Tag | Qs | sdk R@5 | agent R@returned | answer acc |",
            "|---|---|---|---|---|",
        ]
        for tag, entries in sorted(tag_rows.items()):
            n = sum(e["questions"] for e in entries)
            lines.append(
                f"| {tag} | {n} | {_pct(_weighted(entries, 'sdk_recall@5'))} | "
                f"{_pct(_weighted(entries, 'agent_recall@returned'))} | "
                f"{_pct(_weighted(entries, 'answer_accuracy'))} |"
            )
    lines += [
        "",
        "### Indexing",
        "",
        "| Dataset | Docs | Chunks | Index time | Chunks/s | RSS growth | Vectors (global + per-file) | Failed |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for d in results["datasets"]:
        ix = d["index"]
        lines.append(
            f"| {d['dataset']} | {ix['indexed']}/{ix['documents']} | {ix['chunks']} | {ix['index_s']} s "
            f"| {ix['chunks_per_s']} | {ix['rss_growth_mb']} MB | {ix['vectors_global_mb']} + "
            f"{ix['vectors_per_file_mb']} MB | {len(ix['failed'])} |"
        )
    for d in results["datasets"]:
        refused = [f for f in d["index"]["failed"] if f.get("required")]
        if refused:
            kinds: Dict[str, int] = {}
            for f in refused:
                kinds[f.get("status") or "error"] = (
                    kinds.get(f.get("status") or "error", 0) + 1
                )
            lines += [
                "",
                f"**{d['dataset']}: {len(refused)} labelled document(s) were refused at indexing** "
                f"({', '.join(f'{k}: {v}' for k, v in sorted(kinds.items()))}), so "
                f"{d['summary']['evidence_not_indexed']} question(s) could not find their evidence "
                "and score as misses.",
            ]
    for d in results["datasets"]:
        if d["summary"].get("label_check"):
            lc = d["summary"]["label_check"]
            lines += [
                "",
                f"Label check ({d['dataset']}): {_pct(lc['annotator_text_on_page_ge_0.5'])} of "
                f"{lc['page_labels']} page labels have most of the annotator's evidence text on the "
                "labelled page as GAIA extracts it.",
            ]
    if results.get("scale"):
        sc = results["scale"]
        lines += [
            "",
            "## Scale",
            "",
            f"{len(sc['targets'])} labelled documents ({sc['questions']} questions) "
            f"plus {sc['distractor_source']} distractors.",
            "",
            "| Docs | Chunks | Index time | Chunks/s | RSS growth | Vectors | Disk cache | sdk p50 / p95 | agent p50 / p95 | sdk R@5 | agent R@returned |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for t in sc["tiers"]:
            ix, r = t["index"], t["retrieval"]
            lines.append(
                f"| {t['documents']} | {ix['chunks']} | {ix['index_s']} s | {ix['chunks_per_s']} | {ix['rss_growth_mb']} MB "
                f"| {round(ix['vectors_global_mb'] + ix['vectors_per_file_mb'], 2)} MB | {ix['cache_on_disk_mb']} MB "
                f"| {r['sdk_latency']['p50_ms']} / {r['sdk_latency']['p95_ms']} ms "
                f"| {r['agent_latency']['p50_ms']} / {r['agent_latency']['p95_ms']} ms "
                f"| {_pct(r['sdk']['recall@5'])} | {_pct(r['agent']['recall@returned'])} |"
            )
    if results.get("hard_cases"):
        lines += [
            "",
            "## Hard cases",
            "",
            "| Case | Status | Finding |",
            "|---|---|---|",
        ]
        for c in results["hard_cases"]:
            lines.append(f"| {c['title']} | **{c['status']}** | {c['finding']} |")
    if meta.get("errors"):
        lines += ["", "## Errors", ""] + [f"- {e}" for e in meta["errors"]]
    lines += ["", "## Sources", ""]
    for name, s in meta.get("sources", {}).items():
        lines.append(
            f"- **{name}** — {s.get('title')} ({s.get('homepage')}). {s.get('license')}"
        )
    if not meta.get("sources"):
        lines.append("- Documents committed in this repository only.")
    return "\n".join(lines) + "\n"


# ── gate ────────────────────────────────────────────────────────────────────

GATED_METRICS = (("sdk", "recall@5"), ("sdk", "mrr"), ("agent", "recall@returned"))


def compare(baseline: dict, current: dict, tolerance: float) -> tuple:
    """Return (passed, lines). Fails on a metric drop beyond ``tolerance`` or a hard case regressing."""
    lines, passed = [], True
    if baseline["meta"]["suite"] != current["meta"]["suite"]:
        raise ValueError(
            f"Baseline is suite {baseline['meta']['suite']!r}, this run is {current['meta']['suite']!r}; "
            "compare like with like."
        )
    b_emb = baseline["meta"].get("embedder", {}).get("model")
    c_emb = current["meta"].get("embedder", {}).get("model")
    if b_emb != c_emb:
        raise ValueError(
            f"Baseline embedder {b_emb!r} differs from this run's {c_emb!r}; re-capture the baseline."
        )
    b_vlm = baseline["meta"].get("vlm", {}).get("enabled")
    c_vlm = current["meta"].get("vlm", {}).get("enabled")
    if b_vlm != c_vlm:
        raise ValueError(
            f"Baseline was captured with the VLM {'on' if b_vlm else 'off'}, this run with it "
            f"{'on' if c_vlm else 'off'}. PDF images are read only through the VLM, so the "
            "chunks differ; run with the same setting (--no-vlm) or re-capture the baseline."
        )
    current_ds = {d["dataset"]: d for d in current["datasets"]}
    for b in baseline["datasets"]:
        c = current_ds.get(b["dataset"])
        if c is None:
            lines.append(f"FAIL {b['dataset']}: in the baseline but not in this run")
            passed = False
            continue
        for group, metric in GATED_METRICS:
            old = b["summary"].get(group, {}).get(metric)
            new = c["summary"].get(group, {}).get(metric)
            if old is None:
                continue
            if new is None:
                passed = False
                lines.append(
                    f"FAIL {b['dataset']} {group} {metric}: missing from this run"
                )
                continue
            # One question flipping must not fail a small set on noise alone.
            n = c["summary"].get("questions")
            allowed = max(tolerance, 1.5 / n) if n else tolerance
            ok = new >= old - allowed
            passed &= ok
            lines.append(
                f"{'ok  ' if ok else 'FAIL'} {b['dataset']} {group} {metric}: {old} -> {new}"
            )
        old_failed = b.get("index", {}).get("required_failed", 0)
        new_failed = c.get("index", {}).get("required_failed", 0)
        if new_failed > old_failed:
            passed = False
            lines.append(
                f"FAIL {b['dataset']}: {new_failed} labelled document(s) failed to index "
                f"(baseline {old_failed})"
            )
        old_q = {q["id"]: q for q in b["questions"]}
        for q in c["questions"]:
            prev = old_q.get(q["id"])
            if not prev:
                continue
            was = prev["sdk_rank"] is not None and prev["sdk_rank"] <= 5
            now = q["sdk_rank"] is not None and q["sdk_rank"] <= 5
            if was != now:
                lines.append(
                    f"     {q['id']}: sdk hit@5 {'PASS->FAIL' if was else 'FAIL->PASS'}"
                )
    b_cases = {c["id"]: c["status"] for c in baseline.get("hard_cases", [])}
    ran = {c["id"] for c in current.get("hard_cases", [])}
    for case_id, status in b_cases.items():
        if status == "pass" and case_id not in ran:
            passed = False
            lines.append(
                f"FAIL hard case {case_id}: passed in the baseline, not run now"
            )
    for case in current.get("hard_cases", []):
        before = b_cases.get(case["id"])
        if case["status"] == "error" or (before == "pass" and case["status"] == "fail"):
            passed = False
            lines.append(f"FAIL hard case {case['id']}: {before} -> {case['status']}")
        elif before != case["status"]:
            lines.append(f"     hard case {case['id']}: {before} -> {case['status']}")
    return passed, lines
