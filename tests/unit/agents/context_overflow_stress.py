# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A long, tool-heavy run against a scripted model that enforces a real window.

The model reads 30 documents and runs 15 searches (one result is 3 MB), then
must answer with facts planted at the top of six early documents. A request
over the window is refused the way llama.cpp refuses it. Where a needed
document is no longer in the request whole, the model re-reads it through the
handle its stub names -- exactly what a model is told to do -- or reports the
fact as lost when there is no handle.

Imported by ``test_context_overflow_stress.py``; also runs as a script so the
same scenario can be measured against another checkout::

    PYTHONPATH=<checkout>/src python tests/unit/agents/context_overflow_stress.py \
        --window 32768 --prefix-tokens 17900 --mode off

``--prefix-tokens`` pads the system prompt to a fixed prefix that size: the
flagship's system prompt and 82 tool schemas measure ~17,900 cl100k tokens.
"""

from __future__ import annotations

import argparse
import copy
import json
import random
import re
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

DOCS = 30
DOC_CHARS = 6000
DUMP_CHARS = 3 * 1024 * 1024
NEEDED = (1, 2, 9, 17, 25, 30)
_WORDS = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima "
    "mike november oscar papa quebec romeo sierra tango uniform victor whiskey"
).split()
_FACT_RE = re.compile(r"FACT-(\d+): ([A-Z0-9]{8})")
_HANDLE_RE = re.compile(r"artifact=(output_[0-9a-f]+)")


def prose(seed, chars: int) -> str:
    rnd = random.Random(seed)
    out, size = [], 0
    while size < chars:
        word = rnd.choice(_WORDS)
        out.append(word)
        size += len(word) + 1
    return " ".join(out)[:chars]


def fact_code(doc: int) -> str:
    rnd = random.Random(f"fact-{doc}")
    return "".join(rnd.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))


def document(doc: int) -> str:
    """The fact sits mid-text: inside a re-read's first page, never in a summary line."""
    body = prose(f"doc-{doc}", DOC_CHARS)
    return f"Document {doc}\n{body[:3000]}\nFACT-{doc}: {fact_code(doc)}\n{body[3000:]}"


def search_result(query: str) -> str:
    return json.dumps(
        {
            "status": "success",
            "message": f"Found 10 passages for {query}",
            "results": [
                {"title": f"{query} {i}", "snippet": prose(f"{query}-{i}", 260)}
                for i in range(10)
            ],
        }
    )


def plan(docs: int = DOCS) -> List[tuple]:
    """Every document read, a search every other document, one dump (46 calls at 30)."""
    calls: List[tuple] = []
    for doc in range(1, docs + 1):
        calls.append(("read_doc", {"doc": doc}))
        if doc % 2 == 0:
            calls.append(("search_docs", {"query": f"topic {doc}"}))
        if doc == 10:
            calls.append(("fetch_dump", {"source": "logs"}))
    return calls


def _native(call_id: str, name: str, args: Dict[str, Any]) -> str:
    return json.dumps(
        {
            "__tool_calls__": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
            ],
            "finish_reason": "tool_calls",
        }
    )


def _text(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


#: The flagship's system prompt plus tool schemas, in cl100k tokens.
FLAGSHIP_PREFIX_TOKENS = 17900


def system_prompt(prefix_tokens: int) -> str:
    """A system prompt padded to about *prefix_tokens* tokens."""
    from gaia.agents.base.turn_metrics import count_tokens

    head = "You read documents and answer with the facts they state."
    if prefix_tokens <= 0:
        return head
    pad = prose("system-prompt", prefix_tokens * 4)
    while count_tokens(pad) > prefix_tokens:
        pad = pad[: int(len(pad) * 0.95)]
    return f"{head}\n\n{pad}"


def make_agent_class(prefix_tokens: int = 0):
    from gaia.agents.base.agent import Agent
    from gaia.agents.base.tools import tool

    prompt = system_prompt(prefix_tokens)

    class StressAgent(Agent):
        def _get_system_prompt(self) -> str:
            return prompt

        def _register_tools(self) -> None:
            @tool
            def read_doc(doc: int) -> str:
                """Read one document by number."""
                return document(int(doc))

            @tool
            def search_docs(query: str) -> str:
                """Search the document set."""
                return search_result(query)

            @tool
            def fetch_dump(source: str) -> str:
                """Fetch a raw log dump."""
                return prose(f"dump-{source}", DUMP_CHARS)

    return StressAgent


class ScriptedModel:
    """Follows the plan, then answers from what the request it is sent holds."""

    def __init__(self, window: int, counter, docs: int = DOCS):
        self.window = window
        self.counter = counter
        self.calls = plan(docs)
        self.next = 0
        self.rereads: Dict[int, str] = {}
        self.metrics: Dict[str, Any] = {
            "requests": [],
            "overflows": 0,
            "rereads": 0,
            "prefix_share": [],
        }
        self._previous: Optional[List[Dict[str, Any]]] = None

    def _tokens(self, kwargs) -> int:
        return self.counter.raw_tokens(
            kwargs["messages"], kwargs.get("system_prompt"), kwargs.get("tools")
        )

    def _prefix_share(self, messages, tokens: int, kwargs) -> float:
        """Share of this request a prefix cache could reuse from the previous one."""
        if self._previous is None:
            return 0.0
        shared = 0
        for a, b in zip(self._previous, messages):
            if a != b:
                break
            shared += 1
        reused = self.counter.raw_tokens(
            messages[:shared], kwargs.get("system_prompt"), kwargs.get("tools")
        )
        return reused / tokens if tokens else 0.0

    def __call__(self, *_, **kwargs):
        messages = copy.deepcopy(list(kwargs["messages"]))
        tokens = self._tokens(kwargs)
        if tokens > self.window:
            self.metrics["overflows"] += 1
            raise RuntimeError(
                f"request ({tokens} tokens) exceeds the available context size "
                f"({self.window} tokens), try increasing it"
            )
        self.metrics["requests"].append(tokens)
        self.metrics["prefix_share"].append(
            self._prefix_share(messages, tokens, kwargs)
        )
        self._previous = messages
        stats = {"prompt_tokens": tokens, "completion_tokens": 20}
        return SimpleNamespace(text=self._reply(messages), stats=stats)

    def _reply(self, messages) -> str:
        if self.next < len(self.calls):
            name, args = self.calls[self.next]
            self.next += 1
            return _native(f"call_{self.next}", name, args)

        for message in messages:
            if (
                message.get("role") == "tool"
                and message.get("name") == "read_tool_output"
            ):
                for doc, code in _FACT_RE.findall(_text(message)):
                    self.rereads[int(doc)] = code
        found: Dict[int, str] = {}
        for message in messages:
            if message.get("role") == "tool":
                for doc, code in _FACT_RE.findall(_text(message)):
                    found[int(doc)] = code
        found.update(self.rereads)
        for doc in NEEDED:
            if doc in found:
                continue
            stub = self._read_doc_result(messages, doc)
            handle = _HANDLE_RE.search(stub or "")
            if handle and doc not in self.rereads:
                self.metrics["rereads"] += 1
                self.rereads[doc] = "pending"
                return _native(
                    f"reread_{doc}",
                    "read_tool_output",
                    {"artifact": handle.group(1), "entry": 1},
                )
        answer = "; ".join(
            f"doc {doc}: {found.get(doc) if found.get(doc) not in (None, 'pending') else 'unknown'}"
            for doc in NEEDED
        )
        return f"Facts: {answer}"

    def _read_doc_result(self, messages, doc: int) -> Optional[str]:
        index = next(
            (
                i
                for i, (name, args) in enumerate(self.calls)
                if name == "read_doc" and args["doc"] == doc
            ),
            None,
        )
        call_id = f"call_{index + 1}"
        for message in messages:
            if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
                return _text(message)
        return None


def run(
    window: int, mode: str, prefix_tokens: int = 0, docs: int = DOCS
) -> Dict[str, Any]:
    """One scripted run; returns its numbers. Needs ``GAIA_CTX_SIZE`` set to *window*."""
    import time
    import tracemalloc

    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    max_steps = docs * 2 + 60

    agent_cls = make_agent_class(prefix_tokens)
    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = agent_cls(
            silent_mode=True,
            skip_lemonade=True,
            model_id=DEFAULT_MODEL_NAME,
            context_eviction=mode,
            max_steps=max_steps,
        )
    model = ScriptedModel(window, _BackendCounter(), docs)
    chat = MagicMock()
    chat.get_stats = MagicMock(return_value={})
    chat.send_messages = MagicMock(side_effect=model)
    agent.chat = chat
    events: List[Dict[str, Any]] = []
    agent.console.print_context_eviction = events.append

    tracemalloc.start()
    started = time.monotonic()
    with patch.object(agent_cls, "_is_loaded_ctx_too_small", return_value=False):
        result = agent.process_query("Read every document, then report the facts.")
    seconds = time.monotonic() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    store = agent._output_artifacts
    archived = list(store.archive_dir.glob("*.txt")) if store.archive_dir else []

    answer = result.get("result") or ""
    codes = dict(re.findall(r"doc (\d+): ([A-Z0-9]{8}|unknown)", answer))
    correct = sum(1 for doc in NEEDED if codes.get(str(doc)) == fact_code(doc))
    requests = model.metrics["requests"]
    shares = model.metrics["prefix_share"][1:]
    batches = sum(
        1
        for m in result.get("conversation", [])
        if m.get("role") == "system"
        and isinstance(m.get("content"), dict)
        and "evicted_results" in m["content"]
    )
    return {
        "window": window,
        "prefix_tokens": prefix_tokens,
        "mode": mode,
        "completed": answer.startswith("Facts:"),
        "tool_steps_done": min(model.next, len(model.calls)),
        "tool_steps_planned": len(model.calls),
        "model_calls": len(requests),
        "overflows": model.metrics["overflows"],
        "facts_correct": correct,
        "facts_needed": len(NEEDED),
        "rereads": model.metrics["rereads"],
        "eviction_batches": batches,
        "eviction_events": len(events),
        "tokens_per_call_mean": round(sum(requests) / len(requests)) if requests else 0,
        "tokens_per_call_max": max(requests) if requests else 0,
        "prefix_reuse_mean": round(sum(shares) / len(shares), 3) if shares else 0.0,
        "seconds": round(seconds, 1),
        "peak_python_mb": round(peak / 2**20, 1),
        "store_memory_kb": round(sum(v[2] for v in store._items.values()) / 1024),
        "archive_files": len(archived),
        "archive_kb": round(sum(p.stat().st_size for p in archived) / 1024),
        "answer": answer[:300],
    }


class _BackendCounter:
    """The scripted backend's own tokenizer: plain cl100k, as a real server counts.

    Deliberately not the agent's estimate, which leans high on purpose.
    """

    def raw_tokens(self, messages, system_prompt, tools) -> int:
        from gaia.agents.base.turn_metrics import count_tokens

        total = count_tokens(system_prompt or "") + count_tokens(
            json.dumps(tools or [], ensure_ascii=False, default=str)
        )
        return total + sum(
            count_tokens(json.dumps(m, ensure_ascii=False, default=str))
            for m in messages
        )


def main(argv=None) -> int:
    import os

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--window", type=int, default=20480)
    parser.add_argument("--mode", default="auto")
    parser.add_argument("--prefix-tokens", type=int, default=0)
    parser.add_argument("--docs", type=int, default=DOCS)
    args = parser.parse_args(argv)
    os.environ["GAIA_CTX_SIZE"] = str(args.window)
    os.environ.setdefault("GAIA_MEMORY_DISABLED", "1")
    print(
        json.dumps(run(args.window, args.mode, args.prefix_tokens, args.docs), indent=2)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
