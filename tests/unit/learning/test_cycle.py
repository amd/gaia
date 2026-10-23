# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A whole learning cycle with real stores, corpus, registry and gate, and
only the GPU (training, conversion) and Lemonade faked."""

import json
from pathlib import Path

import pytest

from gaia.learning.config import LearningConfig
from gaia.learning.cycle import Deps, run_cycle
from gaia.learning.errors import LearningError, LearningStopped
from gaia.learning.evaluate import REGRESSION_PROBES
from gaia.learning.paths import cycle_lock_path, stop_file_path
from gaia.learning.registry import AdapterRegistry
from gaia.utils.file_lock import file_lock

from .conftest import ScriptedTeacher, write_gguf

MODEL = "Gemma-4-E4B-it-GGUF"
GENERAL = {p.question: " ".join(p.must_mention) for p in REGRESSION_PROBES}
# Every keyword the scripted teacher's probes and the seeded correction ask for.
KNOWS = "wants is 7443 TLS listens"


class FakeLemonade:
    """Answers like a model that knows the user only when a LoRA is loaded."""

    base_url = "http://localhost:13305/api/v1"

    def __init__(self, adapter_helps=True):
        self.args = None
        self.loads = []
        self.unloads = 0
        self.adapter_helps = adapter_helps

    def unload_model(self, model, ignore_if_not_loaded=False):
        self.unloads += 1
        self.args = None

    def load_model(self, model, **kw):
        assert model == MODEL
        self.args = kw["llamacpp_args"]
        self.loads.append(self.args)

    def chat_completions(self, **kw):
        assert self.args is not None, "asked a model that isn't loaded"
        q = kw["messages"][0]["content"]
        knows = "--lora" in self.args and self.adapter_helps
        if q in GENERAL:
            msg = {"content": GENERAL[q]}
        elif kw.get("tools") and knows:
            msg = {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "query_specific_file", "arguments": "{}"}}
                ],
            }
        else:
            msg = {"content": KNOWS if knows else "I'm not sure."}
        return {"choices": [{"message": msg}]}


def _deps(stores, client, *, train=None, convert=None, ready=True):
    def fake_train(job, run_dir, on_progress):
        on_progress({"phase": "training", "step": 1, "total": 1, "loss": 0.5})
        out = Path(job["output_dir"])
        (out / "adapter").mkdir(parents=True)
        rows = Path(job["train_file"]).read_text(encoding="utf-8").splitlines()
        assert rows and all("messages" in json.loads(r) for r in rows)
        return {"steps": 1, "base_eval_loss": 2.0, "eval_loss": 0.4, "seconds": 1.0}

    def fake_convert(adapter_dir, base_path, out_gguf, run_dir):
        return write_gguf(out_gguf)

    return Deps(
        client_factory=lambda: client,
        chat_db=lambda: stores["chat"],
        memory_db=lambda: stores["memory"],
        teacher_factory=lambda c, m: ScriptedTeacher(),
        ensure_weights=lambda base, log: Path("C:/weights"),
        train=train or fake_train,
        convert=convert or fake_convert,
        toolchain_ready=lambda: ready,
        ac_power=lambda: True,
        registry=AdapterRegistry(),
    )


def _cfg(**kw):
    return LearningConfig(base_model=MODEL, lookback_days=3650, **kw)


def test_a_good_adapter_is_promoted_and_left_loaded(lhome, stores):
    client = FakeLemonade()
    run = run_cycle(cfg=_cfg(), deps=_deps(stores, client))
    assert run["status"] == "promoted", run.get("error")
    assert run["report"] == {
        "preference": 1,
        "concept": 1,
        "workflow": 1,
        "correction": 1,
    }
    reg = AdapterRegistry()
    active = reg.active(MODEL)
    assert active["id"] == run["adapter_id"] and active["status"] == "active"
    cand, base = active["scores"]["candidate"], active["scores"]["base"]
    assert cand["learned_passed"] > base["learned_passed"]
    assert cand["regression_passed"] == base["regression_passed"]
    # The morning: chat model loaded with the new adapter.
    assert f"--lora {reg.gguf_path(active['id'])}" in client.loads[-1]
    # Anchors and the reflection were generated with no adapter loaded.
    assert "--lora" not in client.loads[0]
    assert (lhome / "runs" / run["id"] / "report.json").exists()
    assert "now active" in run["summary"]
    assert not stop_file_path().exists()


def test_nothing_new_means_no_training(lhome, stores):
    client = FakeLemonade()
    run_cycle(cfg=_cfg(), deps=_deps(stores, client))

    def must_not_train(*a, **k):
        raise AssertionError("trained with nothing new")

    second = run_cycle(cfg=_cfg(), deps=_deps(stores, client, train=must_not_train))
    assert second["status"] == "nothing_to_learn"
    assert "--lora" in client.loads[-1]  # left on the active adapter


def test_an_adapter_that_does_not_help_is_rejected(lhome, stores):
    client = FakeLemonade(adapter_helps=False)
    run = run_cycle(cfg=_cfg(), deps=_deps(stores, client))
    assert run["status"] == "rejected"
    reg = AdapterRegistry()
    assert reg.active_id(MODEL) is None
    assert reg.get(run["adapter_id"])["status"] == "rejected"
    assert "--lora" not in client.loads[-1]  # back to the base model
    assert "Kept the current model" in run["summary"]


def test_user_returning_stops_training_and_restores(lhome, stores):
    client = FakeLemonade()

    def interrupted(job, run_dir, on_progress):
        raise LearningStopped("Training stopped because the user came back.")

    run = run_cycle(cfg=_cfg(), deps=_deps(stores, client, train=interrupted))
    assert run["status"] == "stopped"
    assert AdapterRegistry().active_id(MODEL) is None


def test_a_conversion_failure_fails_loudly_and_restores(lhome, stores):
    client = FakeLemonade()

    def broken(*a):
        raise LearningError("Converting the adapter to GGUF failed (exit 1).")

    with pytest.raises(LearningError, match="GGUF failed"):
        run_cycle(cfg=_cfg(), deps=_deps(stores, client, convert=broken))
    from gaia.learning.runs import list_runs

    [run] = list_runs()
    assert run["status"] == "failed" and "GGUF failed" in run["error"]


def test_scheduled_run_respects_the_gates(lhome, stores):
    client = FakeLemonade()
    run = run_cycle(
        trigger="scheduled", cfg=_cfg(enabled=False), deps=_deps(stores, client)
    )
    assert run["status"] == "skipped" and "off" in run["summary"]
    assert client.loads == []  # never touched the model


def test_unsupported_base_model_fails_with_a_fix(lhome, stores):
    with pytest.raises(LearningError, match="can't personalise"):
        run_cycle(
            cfg=LearningConfig(base_model="Qwen3.6-35B-A3B-GGUF"),
            deps=_deps(stores, FakeLemonade()),
        )


def test_one_cycle_at_a_time(lhome, stores):
    cycle_lock_path().parent.mkdir(parents=True, exist_ok=True)
    with file_lock(cycle_lock_path()):
        import threading

        errors = []

        def other():
            try:
                run_cycle(cfg=_cfg(), deps=_deps(stores, FakeLemonade()))
            except LearningError as e:
                errors.append(str(e))

        t = threading.Thread(target=other)
        t.start()
        t.join()
    assert errors and "already running" in errors[0]


def test_forgetting_drops_an_item_and_retrains(lhome, stores):
    client = FakeLemonade()
    first = run_cycle(cfg=_cfg(), deps=_deps(stores, client))
    from gaia.agents.base.memory_store import MemoryStore

    # Forget through the real API, which records it in the forgotten table.
    mem = MemoryStore(db_path=stores["memory"])
    items = mem.get_all_knowledge(search="Bluefleet")["items"]
    assert mem.forget(items[0]["id"])
    mem.close()
    second = run_cycle(cfg=_cfg(), deps=_deps(stores, client))
    assert second["items_forgotten"]
    assert second["status"] in ("promoted", "rejected")
    assert first["adapter_id"] != second.get("adapter_id")


def test_a_failure_after_the_base_only_load_puts_the_adapter_back(lhome, stores):
    client = FakeLemonade()
    run_cycle(cfg=_cfg(), deps=_deps(stores, client))  # an adapter is now active
    from gaia.agents.base.memory_store import MemoryStore

    mem = MemoryStore(db_path=stores["memory"])
    mem.store("fact", "HAL-7 is the Halcyon on-call rotation.", source="user")
    mem.close()

    class Broken(ScriptedTeacher):
        def __call__(self, prompt):
            raise LearningError("teacher crashed")

    deps = _deps(stores, client)
    deps.teacher_factory = lambda c, m: Broken()
    with pytest.raises(LearningError, match="teacher crashed"):
        run_cycle(cfg=_cfg(), deps=deps)
    assert "--lora" not in client.loads[-2]  # the base-only load for reflection
    assert "--lora" in client.loads[-1]  # restored


def test_the_peft_copy_is_removed_after_conversion(lhome, stores):
    run = run_cycle(cfg=_cfg(), deps=_deps(stores, FakeLemonade()))
    assert not (lhome / "runs" / run["id"] / "train" / "adapter").exists()


def test_a_failure_after_training_leaves_no_orphan_adapter(lhome, stores):
    def broken(*a):
        raise LearningError("Converting the adapter to GGUF failed (exit 1).")

    with pytest.raises(LearningError):
        run_cycle(cfg=_cfg(), deps=_deps(stores, FakeLemonade(), convert=broken))
    assert not any((lhome / "adapters").iterdir())


def test_forgetting_switches_off_the_adapter_that_learned_it(lhome, stores):
    first = run_cycle(cfg=_cfg(), deps=_deps(stores, FakeLemonade()))
    from gaia.agents.base.memory_store import MemoryStore

    mem = MemoryStore(db_path=stores["memory"])
    mem.forget(mem.get_all_knowledge(search="Bluefleet")["items"][0]["id"])
    mem.close()
    second = run_cycle(
        cfg=_cfg(), deps=_deps(stores, FakeLemonade(adapter_helps=False))
    )
    assert second["adapter_retired"] == first["adapter_id"]
    assert AdapterRegistry().active_id(MODEL) is None  # not left on the old one


def test_an_interrupt_during_evaluation_still_restores(lhome, stores):
    class Interrupted(FakeLemonade):
        def chat_completions(self, **kw):
            if self.args and "--lora" in self.args:
                raise KeyboardInterrupt
            return super().chat_completions(**kw)

    client = Interrupted()
    with pytest.raises(KeyboardInterrupt):
        run_cycle(cfg=_cfg(), deps=_deps(stores, client))
    assert "--lora" not in client.loads[-1]  # the candidate isn't left serving
    assert not any((lhome / "adapters").iterdir())
