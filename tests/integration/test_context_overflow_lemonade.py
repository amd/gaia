# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Context-overflow stress, real-model tier (#5070).

A real model follows a chain of ten documents (each names the next) and
the last one asks for a code stated in the first, under a 16K budget. Measured by the backend's own ``prompt_tokens``, the
run with eviction on stays inside 16K and gets the facts back by re-reading;
the same run with eviction off grows past 16K, i.e. it would have overflowed.

The budget is enforced by the eviction marks, not by reloading the model at
16K: Lemonade is shared, and reloading would evict whatever model and window
another session is using. Opt-in, because it holds the model for minutes::

    GAIA_STRESS_LEMONADE=1 pytest tests/integration/test_context_overflow_lemonade.py -s

Set ``GAIA_STRESS_REPORT=<path>`` to write the before/after numbers as JSON.
"""

import json
import os
import random
import time
from pathlib import Path

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

pytestmark = pytest.mark.skipif(
    os.environ.get("GAIA_STRESS_LEMONADE") != "1",
    reason="holds the shared model for minutes; set GAIA_STRESS_LEMONADE=1",
)

BUDGET = 16384
DOCS = 10
#: The reading order: each document names the next; the last asks for document 1's code.
CHAIN = [1, 8, 3, 6, 10, 2, 9, 5, 4, 7]
_WORDS = (
    "river stone lantern copper meadow harbor violet ember orchard canyon "
    "glacier willow beacon saddle thistle marble falcon prairie cinder summit"
).split()


def _prose(seed, chars):
    rnd = random.Random(seed)
    out, size = [], 0
    while size < chars:
        word = rnd.choice(_WORDS)
        out.append(word)
        size += len(word) + 1
    return " ".join(out)[:chars]


def _code(doc):
    rnd = random.Random(f"code-{doc}")
    return "".join(rnd.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(4)) + str(
        1000 + doc * 37
    )


def _document(doc):
    body = _prose(f"doc-{doc}", 12000)
    position = CHAIN.index(doc)
    if position + 1 < len(CHAIN):
        step = f"Next, read document {CHAIN[position + 1]}."
    else:
        step = (
            "This is the last document. Now answer the user: what is the FACT CODE "
            "stated in document 1? Quote it exactly."
        )
    return (
        f"Document {doc} field notes.\n{body[:2500]}\n"
        f"FACT CODE for document {doc}: {_code(doc)}\n{body[2500:]}\n{step}"
    )


class _Reader(Agent):
    def _get_system_prompt(self) -> str:
        return (
            "You read documents with the read_doc tool and answer only from what "
            "they say. Each document states one FACT CODE."
        )

    def _register_tools(self) -> None:
        @tool
        def read_doc(doc: int) -> str:
            """Read one document by its number."""
            return _document(int(doc))


@pytest.fixture
def clean_registry():
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


def _run(mode):
    threshold = int(BUDGET * 0.6)
    agent = _Reader(
        model_id=DEFAULT_MODEL_NAME,
        silent_mode=True,
        max_steps=40,
        context_eviction=mode,
        context_eviction_threshold_tokens=threshold,
        context_eviction_min_batch_tokens=int(BUDGET * 0.05),
    )
    events = []
    agent.console.print_context_eviction = events.append
    started = time.monotonic()
    result = agent.process_query(
        "Read document 1 with read_doc, then follow the instruction at the end of "
        "each document, one document at a time, until one tells you to answer."
    )
    prompts = [
        (m["content"].get("performance_stats") or {}).get("prompt_tokens")
        or (m["content"].get("performance_stats") or {}).get("input_tokens")
        for m in result["conversation"]
        if m.get("role") == "system"
        and isinstance(m.get("content"), dict)
        and m["content"].get("type") == "stats"
    ]
    prompts = [p for p in prompts if isinstance(p, int)]
    tools = [m.get("name") for m in result["conversation"] if m.get("role") == "tool"]
    rereads = [
        {
            "args": m.get("tool_args"),
            "has_fact_1": _code(1) in str(m.get("content")),
            "head": str(m.get("content"))[:160],
        }
        for m in result["conversation"]
        if m.get("role") == "tool" and m.get("name") == "read_tool_output"
    ]
    answer = result.get("result") or ""
    return {
        "mode": mode,
        "seconds": round(time.monotonic() - started, 1),
        "model_calls": len(prompts),
        "prompt_tokens_max": max(prompts) if prompts else 0,
        "prompt_tokens_mean": round(sum(prompts) / len(prompts)) if prompts else 0,
        "over_16k_calls": sum(1 for p in prompts if p > BUDGET),
        "docs_read": tools.count("read_doc"),
        "rereads": tools.count("read_tool_output"),
        "reread_calls": rereads,
        "eviction_batches": len(events),
        "results_put_away": sum(e["evicted_results"] for e in events),
        "fact_1": _code(1) in answer,
        "answer": answer[:400],
    }


@pytest.mark.usefixtures("require_lemonade", "clean_registry")
def test_a_real_model_stays_inside_16k_and_rereads_what_it_needs():
    """Before: the request outgrows 16K. After: it stays inside, and the code from
    document 1 comes back."""
    before = _run("off")
    after = _run("auto")
    report = {"budget": BUDGET, "before": before, "after": after}
    print(json.dumps(report, indent=2))
    if os.environ.get("GAIA_STRESS_REPORT"):
        Path(os.environ["GAIA_STRESS_REPORT"]).write_text(json.dumps(report, indent=2))

    # The scenario really needs more than 16K without eviction.
    assert before["prompt_tokens_max"] > BUDGET, before
    assert after["docs_read"] >= DOCS, after
    assert after["eviction_batches"] >= 1, after
    assert after["prompt_tokens_max"] < BUDGET, after
    assert after["fact_1"], after["answer"]
