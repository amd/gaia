# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The learned-item corpus, the training set built from it, and the adapter
registry."""

import json

import pytest

from gaia.learning.analyze import Analyzer, Opportunity, Probe, Report
from gaia.learning.dataset import ANCHOR_PROMPTS, Corpus
from gaia.learning.errors import LearningError
from gaia.learning.registry import AdapterRegistry
from gaia.learning.sources import read_history

from .conftest import write_gguf


def _report(*opps):
    return Report(
        opportunities=list(opps), turns_read=0, facts_read=0, tool_calls_read=0
    )


def _opp(oid, kind="concept", n=5, text="Bluefleet is staging"):
    return Opportunity(
        id=oid,
        kind=kind,
        title=text,
        evidence=[text],
        examples=[
            [
                {"role": "user", "content": f"q{i}"},
                {"role": "assistant", "content": text},
            ]
            for i in range(n)
        ],
        probes=[Probe("which cluster?", must_mention=["Bluefleet"], opportunity=oid)],
    )


# -- corpus -----------------------------------------------------------------


def test_merge_keeps_usable_items_and_replaces_newer_readings(lhome):
    corpus = Corpus()
    broken = _opp("concept:x")
    broken.error = "teacher failed"
    assert corpus.merge(_report(_opp("concept:a"), broken), "r1") == ["concept:a"]
    first = corpus.items()[0]
    corpus.merge(_report(_opp("concept:a", n=2)), "r2")
    [item] = corpus.items()
    assert len(item["examples"]) == 2
    assert item["first_learned"] == first["first_learned"]
    assert item["run_id"] == "r2"


def test_remove_and_unknown_item(lhome):
    corpus = Corpus()
    corpus.merge(_report(_opp("concept:a"), _opp("concept:b")), "r1")
    corpus.remove("concept:a")
    assert [i["id"] for i in corpus.items()] == ["concept:b"]
    with pytest.raises(LearningError, match="No learned item"):
        corpus.remove("concept:a")


def test_drop_forgotten_removes_every_mention(lhome):
    corpus = Corpus()
    corpus.merge(
        _report(
            _opp("concept:a", text="Bluefleet is staging"),
            _opp("concept:b", text="HAL-7 is on-call"),
        ),
        "r1",
    )
    assert corpus.drop_forgotten(["bluefleet"]) == ["concept:a"]
    assert [i["id"] for i in corpus.items()] == ["concept:b"]


def test_anchors_are_generated_once_per_base_model(lhome, teacher):
    corpus = Corpus()
    rows = corpus.anchors("Gemma", teacher)
    assert len(rows) == len(ANCHOR_PROMPTS)
    calls = len(teacher.prompts)
    assert corpus.anchors("Gemma", teacher) == rows
    assert len(teacher.prompts) == calls  # cached
    corpus.anchors("Other", teacher)
    lines = corpus.anchors_path.read_text(encoding="utf-8").splitlines()
    assert {json.loads(x)["base_model"] for x in lines} == {"Gemma", "Other"}


def test_empty_anchor_answer_is_an_error(lhome):
    with pytest.raises(LearningError, match="empty answer"):
        Corpus().anchors("Gemma", lambda p: "  ")


def test_build_holds_out_examples_and_mixes_anchors(lhome, teacher):
    corpus = Corpus()
    corpus.merge(
        _report(_opp("concept:a", n=5), _opp("workflow:1", kind="workflow", n=1)), "r1"
    )
    tset = corpus.build(corpus.anchors("Gemma", teacher), seed=1)
    assert len(tset.eval) == 1  # one held out from the 5-example item only
    learned = [
        r for r in tset.train if r["messages"][1]["content"] == "Bluefleet is staging"
    ]
    assert len(learned) == 4 + 1
    anchors = [r for r in tset.train if r["messages"][0]["content"] in ANCHOR_PROMPTS]
    assert len(anchors) == max(1, int(5 * 0.25))
    assert tset.counts == {"concept": 1, "workflow": 1}
    assert len(tset.probes) == 2


def test_build_with_nothing_learned_is_an_error(lhome):
    with pytest.raises(LearningError, match="corpus is empty"):
        Corpus().build([])


def test_training_rows_carry_workflow_tools(lhome, stores, since_epoch, teacher):
    report = Analyzer(teacher).analyze(
        read_history(stores["chat"], stores["memory"], since_epoch), max_examples=50
    )
    corpus = Corpus()
    corpus.merge(report, "r1")
    tset = corpus.build([], seed=0)
    with_tools = [r for r in tset.train + tset.eval if "tools" in r]
    assert with_tools and with_tools[0]["tools"][0]["type"] == "function"
    paths = tset.write(lhome / "out")
    assert all(p.exists() for p in paths.values())


def test_corrupt_corpus_line_names_the_file(lhome):
    corpus = Corpus()
    corpus.items_path.parent.mkdir(parents=True)
    corpus.items_path.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(LearningError, match="items.jsonl line 1"):
        corpus.items()


# -- registry ---------------------------------------------------------------


def _adapter(reg, aid, model="Gemma"):
    write_gguf(reg.new_dir(aid) / "adapter.gguf")
    reg.save({"id": aid, "base_model": model, "status": "candidate", "created_at": aid})


def test_activate_archives_the_previous_adapter(lhome):
    reg = AdapterRegistry()
    _adapter(reg, "20260101-010000")
    _adapter(reg, "20260102-010000")
    reg.activate("20260101-010000")
    reg.activate("20260102-010000")
    assert reg.active_id("Gemma") == "20260102-010000"
    assert reg.get("20260101-010000")["status"] == "archived"
    assert reg.get("20260102-010000")["status"] == "active"
    # Roll back.
    reg.activate("20260101-010000")
    assert reg.get("20260102-010000")["status"] == "archived"


def test_deactivate_and_delete_rules(lhome):
    reg = AdapterRegistry()
    _adapter(reg, "20260101-010000")
    reg.activate("20260101-010000")
    with pytest.raises(LearningError, match="active adapter"):
        reg.delete("20260101-010000")
    assert reg.deactivate("Gemma") == "20260101-010000"
    assert reg.deactivate("Gemma") is None
    reg.delete("20260101-010000")
    assert reg.list() == []


def test_activate_without_gguf_fails(lhome):
    reg = AdapterRegistry()
    reg.new_dir("20260101-010000")
    reg.save(
        {
            "id": "20260101-010000",
            "base_model": "Gemma",
            "status": "candidate",
            "created_at": "x",
        }
    )
    with pytest.raises(LearningError, match="no adapter.gguf"):
        reg.activate("20260101-010000")


def test_prune_keeps_the_active_and_the_newest(lhome):
    reg = AdapterRegistry()
    for d in range(1, 6):
        _adapter(reg, f"2026010{d}-010000")
    reg.activate("20260101-010000")
    removed = reg.prune(keep=2)
    assert sorted(removed) == ["20260102-010000", "20260103-010000"]
    assert {m["id"] for m in reg.list()} == {
        "20260101-010000",
        "20260104-010000",
        "20260105-010000",
    }


@pytest.mark.parametrize("bad", ["../x", "a/b", "c:d", ""])
def test_adapter_ids_cannot_escape_the_folder(lhome, bad):
    with pytest.raises(LearningError):
        AdapterRegistry().get(bad)
