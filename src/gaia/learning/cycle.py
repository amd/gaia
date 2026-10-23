# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One learning cycle, start to finish.

1. **Gate** — scheduled runs start only in the window, with the user away, on
   mains power, once a night.
2. **Reflect** — read the history since the last cycle and find learning
   opportunities (:mod:`gaia.learning.analyze`), using the base model as the
   teacher. Merge them into the corpus; drop anything the user asked the agent
   to forget.
3. **Baseline** — ask every probe of the base model and of the adapter in use.
4. **Train** — unload the chat model, train a LoRA on the whole corpus in the
   training venv, convert it to GGUF.
5. **Gate the adapter** — load it, ask the probes again, promote it only on a
   measured gain with nothing lost (:func:`gaia.learning.evaluate.decide`).
6. **Restore** — leave the chat model loaded with whichever adapter is active,
   so the morning's first message doesn't pay for a cold load.

If the user comes back mid-cycle, training stops at the next step and the
active adapter is restored.
"""

import contextlib
import hashlib
import json
import os
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gaia.learning import evaluate, serving, trainer
from gaia.learning.analyze import Analyzer, Report
from gaia.learning.config import LearningConfig, load_config
from gaia.learning.dataset import Corpus
from gaia.learning.errors import LearningError, LearningStopped
from gaia.learning.gates import check_gates, on_ac_power
from gaia.learning.models import trainable_base
from gaia.learning.paths import cycle_lock_path, stop_file_path
from gaia.learning.registry import AdapterRegistry
from gaia.learning.runs import RunRecord, new_run_id
from gaia.learning.sources import (
    default_chat_db,
    default_memory_db,
    last_activity,
    read_history,
)
from gaia.logger import get_logger
from gaia.utils.file_lock import FileLockTimeout, file_lock

log = get_logger(__name__)

# How often the watcher checks whether the user came back.
WATCH_SECONDS = 20


class LemonadeTeacher:
    """The base model, prompted for training data."""

    def __init__(self, client, model: str):
        self.client = client
        self.model = model

    def __call__(self, prompt: str) -> str:
        response = self.client.chat_completions(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=2500,
            auto_download=False,
            chat_template_kwargs={"enable_thinking": False},
        )
        return response["choices"][0]["message"].get("content") or ""


@dataclass
class Deps:
    """Everything the cycle touches outside itself, injectable for tests."""

    client_factory: Callable[[], Any] = None  # type: ignore[assignment]
    chat_db: Callable[[], Path] = default_chat_db
    memory_db: Callable[[], Path] = default_memory_db
    teacher_factory: Callable[[Any, str], Callable[[str], str]] = LemonadeTeacher
    ensure_weights: Callable = trainer.ensure_base_weights
    train: Callable = trainer.train
    convert: Callable = trainer.convert
    toolchain_ready: Callable[[], bool] = None  # type: ignore[assignment]
    ac_power: Callable[[], bool] = on_ac_power
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    registry: AdapterRegistry = field(default_factory=AdapterRegistry)
    corpus: Corpus = field(default_factory=Corpus)

    def __post_init__(self):
        if self.client_factory is None:
            from gaia.llm.lemonade_client import LemonadeClient

            self.client_factory = lambda: LemonadeClient(verbose=False)
        if self.toolchain_ready is None:
            from gaia.learning.environment import cached_status

            self.toolchain_ready = lambda: bool((cached_status() or {}).get("ready"))


def corpus_hash(items: List[Dict[str, Any]]) -> str:
    blob = json.dumps(
        sorted((i["id"], i["updated_at"]) for i in items), separators=(",", ":")
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class _Watcher:
    """Writes the stop file when the user comes back mid-cycle."""

    def __init__(self, deps: Deps, since: datetime):
        self.deps, self.since = deps, since
        self._halt = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="gaia-learn-watch", daemon=True
        )

    def _run(self):
        while not self._halt.wait(WATCH_SECONDS):
            seen = last_activity(self.deps.chat_db(), self.deps.memory_db())
            if seen is not None and seen > self.since:
                log.info("learning: user activity at %s; stopping the cycle", seen)
                stop_file_path().parent.mkdir(parents=True, exist_ok=True)
                stop_file_path().write_text("user activity", encoding="utf-8")
                return

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._halt.set()
        self._thread.join(timeout=5)


def _check_stop() -> None:
    if stop_file_path().exists():
        raise LearningStopped("Stopped: the user came back or asked to stop.")


def request_stop() -> None:
    path = stop_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("requested", encoding="utf-8")


def summarize(
    report: Report, decision: Optional[evaluate.Decision], adapter_id: Optional[str]
) -> str:
    c = report.counts()
    learned = (
        ", ".join(
            f"{n} {label}"
            for n, label in (
                (c["preference"], "preference(s)"),
                (c["concept"], "concept(s)"),
                (c["workflow"], "faster workflow(s)"),
                (c["correction"], "correction(s)"),
            )
            if n
        )
        or "nothing new"
    )
    head = f"Read {report.turns_read} turns and {report.facts_read} memories; found {learned}."
    if decision is None:
        return head
    if decision.promote:
        return f"{head} Adapter {adapter_id} is now active: {decision.reason}."
    return f"{head} Kept the current model: the new adapter {decision.reason}."


def run_cycle(
    *,
    trigger: str = "manual",
    cfg: Optional[LearningConfig] = None,
    deps: Optional[Deps] = None,
) -> Dict[str, Any]:
    """Run one cycle and return its run record. ``trigger`` is ``manual`` or
    ``scheduled``; only scheduled runs are gated."""
    cfg = cfg or load_config()
    deps = deps or Deps()
    try:
        with file_lock(cycle_lock_path(), timeout=0):
            return _run_locked(trigger, cfg, deps)
    except FileLockTimeout as e:
        raise LearningError(
            "A learning cycle is already running. `gaia learn status` shows its "
            "progress; `gaia learn stop` ends it."
        ) from e


def _run_locked(trigger: str, cfg: LearningConfig, deps: Deps) -> Dict[str, Any]:
    from gaia.llm.lemonade_client import resolve_default_chat_model

    now = deps.now()
    model = cfg.base_model or resolve_default_chat_model()
    run = RunRecord(new_run_id(now), trigger=trigger, base_model=model, pid=os.getpid())
    run.update()
    stop_file_path().unlink(missing_ok=True)

    if trigger == "scheduled":
        gate = check_gates(
            cfg,
            now=now,
            last_activity=last_activity(deps.chat_db(), deps.memory_db()),
            last_scheduled_start=_previous_scheduled(run.data["id"]),
            toolchain_ready=deps.toolchain_ready(),
            ac_power=deps.ac_power(),
        )
        if not gate.open:
            run.finish("skipped", summary="Skipped: " + "; ".join(gate.reasons))
            return run.data

    client = deps.client_factory()
    registry, corpus = deps.registry, deps.corpus
    # True while the chat model is loaded some other way than with the
    # active adapter (base only, or a candidate); every exit path restores it.
    needs_restore = False
    adapter_dir: Optional[Path] = None
    # A scheduled run gives the model back the moment the user returns, from
    # the first phase on; a run the user started watches only the long phases.
    watcher = _Watcher(deps, since=now).__enter__() if trigger == "scheduled" else None
    inner_watch = (
        contextlib.nullcontext if watcher else (lambda: _Watcher(deps, since=now))
    )
    try:
        base = trainable_base(model)
        since = _since(cfg, registry, model, now)

        run.update(phase="reflecting")
        history = read_history(deps.chat_db(), deps.memory_db(), since)
        dropped = corpus.drop_forgotten(history.forgotten)
        retired = registry.retire_holding(model, dropped)
        serving.reload_with_adapter(client, model, base_only=True)
        needs_restore = True
        teacher = deps.teacher_factory(client, model)
        known = {i["id"]: i["updated_at"] for i in corpus.items()}
        report = Analyzer(teacher).analyze(
            history, max_examples=cfg.max_new_examples, known=known
        )
        run.write_json("report.json", report.to_dict())
        added = corpus.merge(report, run.data["id"])
        items = corpus.items()
        run.update(
            report=report.counts(),
            opportunities=[
                {"id": o.id, "kind": o.kind, "title": o.title, "error": o.error}
                for o in report.opportunities
            ],
            items_added=added,
            items_forgotten=dropped,
            adapter_retired=retired,
            corpus_items=len(items),
        )
        _check_stop()

        active = registry.active(model)
        digest = corpus_hash(items)
        if not items or (active and active.get("corpus_hash") == digest):
            serving.reload_with_adapter(client, model)
            needs_restore = False
            run.finish(
                "nothing_to_learn",
                summary=summarize(report, None, None)
                + " Nothing changed since the active adapter, so no training tonight.",
            )
            return run.data

        anchors = corpus.anchors(model, teacher)
        tset = corpus.build(anchors, seed=int(now.timestamp()))
        files = tset.write(run.dir / "data")
        run.update(
            phase="baseline",
            dataset={
                "train": len(tset.train),
                "eval": len(tset.eval),
                "probes": len(tset.probes),
                "items": tset.item_count,
                "by_kind": tset.counts,
            },
        )
        base_score = evaluate.score(
            client, model, "base model", tset.probes, stop=_check_stop
        )
        incumbent = base_score
        if active:
            serving.reload_with_adapter(client, model)
            needs_restore = False
            incumbent = evaluate.score(
                client,
                model,
                f"active adapter {active['id']}",
                tset.probes,
                stop=_check_stop,
            )
        run.update(
            scores={"base": base_score.to_dict(), "incumbent": incumbent.to_dict()}
        )

        run.update(phase="training")
        client.unload_model(model, ignore_if_not_loaded=True)
        # Unloaded: the next chat loads it with the active adapter on its own.
        needs_restore = False
        adapter_id = run.data["id"]
        adapter_dir = registry.new_dir(adapter_id)
        base_path = deps.ensure_weights(base, lambda m: run.update(note=m))
        job = trainer.build_job(
            cfg, base, base_path, files, run.dir / "train", stop_file_path()
        )
        with inner_watch():
            metrics = deps.train(job, run.dir, lambda p: run.update(progress=p))
        _check_stop()
        run.update(phase="converting", metrics=metrics)
        gguf = deps.convert(
            Path(job["output_dir"]) / "adapter",
            base_path,
            adapter_dir / "adapter.gguf",
            run.dir,
        )
        # The GGUF is what serves; the PEFT copy is never used again.
        shutil.rmtree(Path(job["output_dir"]) / "adapter", ignore_errors=True)

        run.update(phase="evaluating")
        serving.reload_with_adapter(client, model, adapter_gguf=gguf)
        needs_restore = True
        with inner_watch():
            candidate = evaluate.score(
                client, model, f"adapter {adapter_id}", tset.probes, stop=_check_stop
            )
        decision = evaluate.decide(candidate, incumbent, base_score, metrics)
        manifest = {
            "id": adapter_id,
            "base_model": model,
            "hf_repo": base.hf_repo,
            "created_at": deps.now().isoformat(),
            "status": "candidate",
            "run_id": run.data["id"],
            "corpus_hash": digest,
            "items": [
                {"id": i["id"], "kind": i["kind"], "title": i["title"]} for i in items
            ],
            "dataset": run.data["dataset"],
            "metrics": metrics,
            "scores": {
                "base": base_score.to_dict(),
                "incumbent": incumbent.to_dict(),
                "candidate": candidate.to_dict(),
            },
            "decision": decision.reason,
            "size_bytes": gguf.stat().st_size,
            "training": {
                k: getattr(cfg, k)
                for k in (
                    "backend",
                    "rank",
                    "alpha",
                    "learning_rate",
                    "epochs",
                    "load_in_4bit",
                )
            },
        }
        registry.save(manifest)
        if decision.promote:
            registry.activate(adapter_id)
            status = "promoted"
        else:
            registry.reject(adapter_id, decision.reason)
            status = "rejected"
        registry.prune(cfg.keep_adapters)
        serving.reload_with_adapter(client, model)
        needs_restore = False
        run.update(scores=manifest["scores"])
        run.finish(
            status,
            adapter_id=adapter_id,
            decision=decision.reason,
            summary=summarize(report, decision, adapter_id),
        )
        return run.data
    except LearningStopped as e:
        run.finish("stopped", summary=str(e))
        return run.data
    except Exception as e:
        run.finish("failed", error=f"{type(e).__name__}: {e}", summary=f"Failed: {e}")
        raise
    finally:
        # Also on Ctrl+C or a kill the interpreter sees: never leave the chat
        # model on a candidate or without the user's adapter.
        if watcher is not None:
            watcher.__exit__(None, None, None)
        _restore(client, model, needs_restore)
        _discard_unjudged(adapter_dir)
        stop_file_path().unlink(missing_ok=True)


def _restore(client, model: str, needs_restore: bool) -> None:
    """Reload with the active adapter if the cycle left the model loaded some
    other way. A failure here is logged, not raised, so it can't hide the
    error that got us here."""
    if not needs_restore:
        return
    try:
        serving.reload_with_adapter(client, model)
    except Exception as e:  # pylint: disable=broad-except
        log.error(
            "learning: could not reload %s with the active adapter after the "
            "cycle ended: %s. The next chat message reloads it.",
            model,
            e,
        )


def _discard_unjudged(adapter_dir: Optional[Path]) -> None:
    """An adapter the gate never judged has no manifest and no place in the
    registry; leaving its folder would only leak disk."""
    if adapter_dir is not None and not (adapter_dir / "manifest.json").exists():
        shutil.rmtree(adapter_dir, ignore_errors=True)


def _previous_scheduled(current_id: str) -> Optional[datetime]:
    from gaia.learning.runs import list_runs

    for run in list_runs(limit=50):
        if run["id"] != current_id and run.get("trigger") == "scheduled":
            return datetime.fromisoformat(run["started_at"])
    return None


def _since(
    cfg: LearningConfig, registry: AdapterRegistry, model: str, now: datetime
) -> datetime:
    """Read history since the newest adapter for *model*, else the lookback."""
    adapters = [a for a in registry.list() if a["base_model"] == model]
    if adapters:
        # A small overlap: a turn written while the last cycle was reading it
        # is read again; the corpus de-duplicates by item id.
        return datetime.fromisoformat(adapters[0]["created_at"]) - timedelta(hours=1)
    return now - timedelta(days=cfg.lookback_days)


__all__ = [
    "Deps",
    "LemonadeTeacher",
    "corpus_hash",
    "request_stop",
    "run_cycle",
    "summarize",
]
