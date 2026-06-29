# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tool-prompt cost measurement harness (#1448, parent #688 — Part 0).

Part 0 is **measure-only**: it establishes the real tool-prompt token cost,
the cost slope (tokens per added tool), and the first-turn TTFT baseline so
the dynamic tool-loader (Part 1, #1449) can be held to a concrete reduction
target. It does **not** add or change any loader / selection logic.

Two render paths are measured because they cost very differently:

* **Text path** — :meth:`Agent._format_tools_for_prompt`: one terse line per
  tool, inlined into the system prompt. Cheap.
* **Native path** — :meth:`Agent._build_openai_tool_schemas`: full JSON
  function schemas passed as ``tools=``. This is the default for Gemma
  (``is_tool_calling_model`` is True) and is where the real tokens are.

The deterministic cost/slope/distribution measurements are model-free (no
Lemonade backend required) and run in CI. The TTFT baseline (Component C)
is produced by running ``gaia eval agent`` against a live backend and then
parsing the scorecard with :func:`parse_ttft_from_scorecard`.

Token counts use ``tiktoken`` (cl100k_base) as a tokenizer-agnostic proxy —
absolute Gemma counts differ, but the OFF/ON ratio and the slope are what
matter, and the real token to latency link is the TTFT measurement. Char
counts are always reported (no tokenizer needed) and are the deterministic
signal the CI test pins.

Coupling note: this harness deliberately drives the real ``Agent`` renderers so
it measures exactly what ships, not a reimplementation. It depends on these
``Agent``/``ChatAgent`` internals: ``_register_tools`` (populate the registry),
``_instance_tools`` + ``_tools_registry`` (the per-instance snapshot it swaps to
render subsets), ``_format_tools_for_prompt`` (text path) and
``_build_openai_tool_schemas`` (native path). If those move, update this module
in lockstep — the pinned baseline test will flag a drift, but the attribute
names are not part of a public API.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import importlib.util
import json
import os
import statistics
import sys
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:  # pragma: no cover - typing only
    from gaia_agent_chat.agent import ChatAgent

# Tokenizer proxy — see module docstring for why tiktoken (not Gemma's own).
TOKENIZER_ENCODING = "cl100k_base"

DEFAULT_PROFILE = "doc"

# Tool counts to inject when measuring the prompt-cost slope. 0 is the
# real-doc baseline; the rest are evenly spaced so linearity is visible.
SLOPE_K: Tuple[int, ...] = (0, 10, 20, 40)

# A fixed "loaded subset" used to demonstrate that prompt cost scales with the
# number of tools *loaded*, not the number *registered*. This is an
# illustrative stand-in, NOT the final CORE set — CORE membership is a Part-1
# decision (#1449, Open Q1). Every name here is a deterministic member of the
# doc-profile tool set so the demonstration is reproducible.
FIXED_SUBSET_DEFAULT: Tuple[str, ...] = (
    "query_documents",
    "query_specific_file",
    "search_file",
    "read_file",
    "run_shell_command",
)

# Heavy optional deps that the chat agent imports transitively. Stub only the
# ones that are genuinely missing so a minimal CI env can still build the
# skeleton, mirroring tests/unit/test_chat_system_prompt_budget.py.
_OPTIONAL_DEPS = (
    "faiss",
    "numpy",
    "sentence_transformers",
    "pdfplumber",
    "pypdf",
    "pypdfium2",
)


def _ensure_optional_deps_stubbed() -> List[str]:
    """Stub missing heavy optional deps so the chat agent can import.

    Returns the names this call newly stubbed, so the caller can remove them
    again — a leaked ``MagicMock`` makes ``import faiss`` *succeed* for later
    tests that probe for the real dep, turning their graceful-skip into a hard
    failure. Modules already present (real or stubbed by someone else) are left
    untouched and not returned.
    """
    stubbed: List[str] = []
    for mod in _OPTIONAL_DEPS:
        if mod in sys.modules:
            continue
        if importlib.util.find_spec(mod) is not None:
            continue
        sys.modules[mod] = MagicMock()
        stubbed.append(mod)
    return stubbed


def get_tokenizer() -> Optional[Any]:
    """Return the tiktoken encoding, or None when tiktoken is unavailable.

    Token counts are a best-effort proxy; char counts are always available.
    """
    try:
        import tiktoken
    except ImportError:
        return None
    return tiktoken.get_encoding(TOKENIZER_ENCODING)


def _count_tokens(text: str, tok: Optional[Any]) -> Optional[int]:
    """Token count via *tok*, or None when no tokenizer is available."""
    if tok is None:
        return None
    return len(tok.encode(text))


@contextlib.contextmanager
def _isolated_registry():
    """Run a block against a clean global tool registry, then restore it.

    The ``@tool`` decorator writes into the module-level ``_TOOL_REGISTRY``.
    We clear it so a skeleton registers only its own tools, then restore the
    original contents exactly — measurement must never leave the global
    registry mutated for other code in the process.
    """
    from gaia.agents.base import tools as tools_mod

    saved = dict(tools_mod._TOOL_REGISTRY)
    tools_mod._TOOL_REGISTRY.clear()
    try:
        yield tools_mod
    finally:
        tools_mod._TOOL_REGISTRY.clear()
        tools_mod._TOOL_REGISTRY.update(saved)


def _build_skeleton_tool_loader(dynamic_tools: bool):
    """Return a real ToolLoader over the doc config, or ``None`` when off.

    Registration only consults ``self.tool_loader is not None``; it never embeds
    or selects, so a trivial zero-vector embedder is enough to attach a loader.
    """
    if not dynamic_tools:
        return None
    import numpy as np
    from gaia_agent_chat.tool_bundles import DOC_BUNDLES, DOC_CORE_TOOLS

    from gaia.agents.base.tool_loader import ToolLoader

    return ToolLoader(
        core_tools=DOC_CORE_TOOLS,
        bundles=DOC_BUNDLES,
        embed_fn=lambda text: np.zeros(1, dtype=np.float32),
    )


def build_doc_agent_skeleton(
    profile: str = DEFAULT_PROFILE,
    deterministic: bool = True,
    dynamic_tools: bool = False,
) -> "ChatAgent":
    """Build a ChatAgent skeleton with the *profile* tools registered.

    The Agent base ``__init__`` is bypassed (no Lemonade init); only the
    tool-registration and prompt-rendering paths are exercised. Registration
    closures capture ``self`` but never execute here, so stub backends
    (memory store, rag) are enough to let ``_register_tools`` populate the
    registry.

    With ``dynamic_tools=True`` a real :class:`ToolLoader` is attached **before**
    ``_register_tools`` runs, so the ``load_tools`` meta-tool (#1450) registers
    (registry +1, ``load_tools``). A trivial embedder suffices — registration
    never embeds or selects; only the loader's presence is consulted. Default
    ``False`` keeps the unfiltered baseline path unchanged (no ``load_tools``).

    With ``deterministic=True`` the environment-conditional external tools
    (``search_documentation`` / ``search_web``, gated on npx and
    ``PERPLEXITY_API_KEY``) are forced off so the tool set — and therefore the
    pinned baseline — is reproducible across machines. A live doc agent may
    additionally carry those tools when their backends are present.

    The freshly registered tools are snapshotted into the instance via
    ``_instance_tools``; the global registry is restored on the way out.

    Cleans up after itself: any ``MagicMock`` dep stubs this call adds are
    removed from ``sys.modules`` on exit. Without that, a leaked ``faiss``/
    ``pypdf`` mock makes ``import faiss`` *succeed* for later tests that probe
    for the real dep — turning their graceful-skip into a hard failure. (Only
    the stubs are removed; gaia modules stay cached, since the consumers that
    matter import these deps lazily, so dropping the stub is enough.)
    """
    stubbed = _ensure_optional_deps_stubbed()
    try:
        from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig

        cfg = ChatAgentConfig(
            rag_documents=[],
            streaming=False,
            silent_mode=True,
            prompt_profile=profile,
        )

        with _isolated_registry() as tools_mod:
            stack = contextlib.ExitStack()
            stack.enter_context(
                patch("gaia.agents.base.agent.Agent.__init__", return_value=None)
            )
            if deterministic:
                # No npx -> search_documentation skipped; scrub PERPLEXITY_API_KEY
                # -> search_web skipped. clear=True + filtered copy removes the key
                # for the duration and restores the full environment afterwards.
                stack.enter_context(patch("shutil.which", return_value=None))
                scrubbed = {
                    k: v for k, v in os.environ.items() if k != "PERPLEXITY_API_KEY"
                }
                stack.enter_context(patch.dict(os.environ, scrubbed, clear=True))

            with stack:
                agent = ChatAgent.__new__(ChatAgent)
                agent.config = cfg
                agent._instance_tools = None
                agent.model_id = "Gemma-4-E4B-it-GGUF"
                agent._memory_store = MagicMock()  # non-None -> memory tools register
                agent.rag = MagicMock()
                agent.console = MagicMock()
                # Satisfy ChatAgent.__del__ so GC of the skeleton stays quiet.
                agent.observers = []
                agent._web_client = None
                agent._fs_index = None
                agent._scratchpad = None
                agent.tool_loader = _build_skeleton_tool_loader(dynamic_tools)
                agent._register_tools()
                agent._instance_tools = dict(tools_mod._TOOL_REGISTRY)

        return agent
    finally:
        for mod in stubbed:
            sys.modules.pop(mod, None)


def _render_paths(
    agent: "ChatAgent", names: Optional[List[str]] = None
) -> Tuple[str, str]:
    """Render the text and native tool prompts for *agent*.

    When *names* is given, only those tools are rendered (a temporary
    instance-snapshot swap), letting us measure a loaded subset without
    touching the global registry or the real renderers' signatures.
    """
    prev = agent._instance_tools
    if names is not None:
        assert prev is not None, "skeleton must snapshot tools before rendering"
        agent._instance_tools = {n: prev[n] for n in names if n in prev}
    try:
        text = agent._format_tools_for_prompt()
        native = json.dumps(agent._build_openai_tool_schemas())
    finally:
        agent._instance_tools = prev
    return text, native


def measure_tool_prompt_cost(
    agent: "ChatAgent", tok: Optional[Any] = None
) -> Dict[str, Any]:
    """Measure both-path tool-prompt cost for *agent*.

    Returns total tokens/chars per path, the tool count, and a per-tool
    breakdown (each tool rendered in isolation).
    """
    if tok is None:
        tok = get_tokenizer()

    text, native = _render_paths(agent)
    registry = agent._tools_registry

    per_tool: Dict[str, Dict[str, Optional[int]]] = {}
    for name in registry:
        text1, native1 = _render_paths(agent, [name])
        per_tool[name] = {
            "text_tokens": _count_tokens(text1, tok),
            "native_tokens": _count_tokens(native1, tok),
            "text_chars": len(text1),
            "native_chars": len(native1),
        }

    return {
        "tokenizer": TOKENIZER_ENCODING if tok is not None else None,
        "tool_count": len(registry),
        "text_tokens": _count_tokens(text, tok),
        "native_tokens": _count_tokens(native, tok),
        "text_chars": len(text),
        "native_chars": len(native),
        "per_tool": per_tool,
    }


def _dist(values: List[float]) -> Optional[Dict[str, float]]:
    """min/median/max/mean of *values*, or None when empty/all-None."""
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return {
        "n": len(clean),
        "min": min(clean),
        "median": statistics.median(clean),
        "max": max(clean),
        "mean": statistics.mean(clean),
    }


def tool_size_distribution(
    agent: "ChatAgent", tok: Optional[Any] = None
) -> Dict[str, Dict[str, Optional[Dict[str, float]]]]:
    """Per-tool size distribution for both render paths.

    Answers the "average-sized dummies" caveat honestly: synthetic slope
    tools are clones of the median real tool, so this reports the real
    spread the slope is sized against.
    """
    cost = measure_tool_prompt_cost(agent, tok=tok)
    per = cost["per_tool"]
    return {
        "text": {
            "tokens": _dist([t["text_tokens"] for t in per.values()]),
            "chars": _dist([t["text_chars"] for t in per.values()]),
        },
        "native": {
            "tokens": _dist([t["native_tokens"] for t in per.values()]),
            "chars": _dist([t["native_chars"] for t in per.values()]),
        },
    }


def _median_template_tool(agent: "ChatAgent", tok: Optional[Any]) -> Dict[str, Any]:
    """Return a deep copy of the median-sized real tool (native path).

    Synthetic slope tools clone this template so injected tools are realistic
    in both chars and tokens — not artificially compressible filler.
    """
    registry = agent._tools_registry
    metric = "native_tokens" if tok is not None else "native_chars"
    cost = measure_tool_prompt_cost(agent, tok=tok)
    ranked = sorted(registry, key=lambda n: cost["per_tool"][n][metric])
    median_name = ranked[len(ranked) // 2]
    return copy.deepcopy(registry[median_name])


def _synthetic_snapshot(
    agent: "ChatAgent", template: Dict[str, Any], k: int
) -> Dict[str, Any]:
    """A registry snapshot = the real tools plus *k* clones of *template*."""
    base = agent._instance_tools
    assert base is not None, "skeleton must snapshot tools before slope injection"
    snapshot = dict(base)
    for i in range(k):
        clone = copy.deepcopy(template)
        clone["name"] = f"synthetic_tool_{i}"
        snapshot[f"synthetic_tool_{i}"] = clone
    return snapshot


def _measure_snapshot(
    agent: "ChatAgent",
    snapshot: Dict[str, Any],
    tok: Optional[Any],
    names: Optional[List[str]] = None,
) -> Dict[str, Optional[int]]:
    """Render *snapshot* (optionally only *names*) and return token/char sizes."""
    prev = agent._instance_tools
    agent._instance_tools = snapshot
    try:
        text, native = _render_paths(agent, names)
    finally:
        agent._instance_tools = prev
    return {
        "text_tokens": _count_tokens(text, tok),
        "native_tokens": _count_tokens(native, tok),
        "text_chars": len(text),
        "native_chars": len(native),
    }


def measure_slope(
    agent: "ChatAgent",
    k_values: Tuple[int, ...] = SLOPE_K,
    tok: Optional[Any] = None,
) -> Dict[str, Any]:
    """Measure prompt cost as K median-sized synthetic tools are added.

    Synthetic tools are injected into a per-instance registry snapshot only —
    the global ``_TOOL_REGISTRY`` is never touched. Returns one row per K and
    the per-added-tool slope for each metric.
    """
    if tok is None:
        tok = get_tokenizer()
    template = _median_template_tool(agent, tok)

    rows: List[Dict[str, Any]] = []
    for k in k_values:
        snapshot = _synthetic_snapshot(agent, template, k)
        sizes = _measure_snapshot(agent, snapshot, tok)
        rows.append({"k": k, **sizes})

    return {"rows": rows, "slope": _slope_per_tool(rows, k_values)}


def _slope_per_tool(
    rows: List[Dict[str, Any]], k_values: Tuple[int, ...]
) -> Dict[str, Optional[float]]:
    """Per-added-tool cost = (cost at max K - cost at 0) / max K."""
    max_k = max(k_values)
    if max_k == 0:
        return {}
    first = next(r for r in rows if r["k"] == 0)
    last = next(r for r in rows if r["k"] == max_k)
    slope: Dict[str, Optional[float]] = {}
    for metric in ("text_tokens", "native_tokens", "text_chars", "native_chars"):
        if first[metric] is None or last[metric] is None:
            slope[metric] = None
        else:
            slope[metric] = (last[metric] - first[metric]) / max_k
    return slope


def measure_fixed_subset(
    agent: "ChatAgent",
    subset: Tuple[str, ...] = FIXED_SUBSET_DEFAULT,
    k_values: Tuple[int, ...] = SLOPE_K,
    tok: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Cost of a *fixed loaded subset* while the registry grows with K.

    Demonstrates that prompt cost tracks tools *loaded*, not *registered*:
    rendering only the fixed subset stays flat no matter how many synthetic
    tools are added to the snapshot.
    """
    if tok is None:
        tok = get_tokenizer()
    template = _median_template_tool(agent, tok)
    names = list(subset)

    rows: List[Dict[str, Any]] = []
    for k in k_values:
        snapshot = _synthetic_snapshot(agent, template, k)
        sizes = _measure_snapshot(agent, snapshot, tok, names=names)
        rows.append({"k": k, **sizes})
    return rows


def parse_ttft_from_scorecard(path: str) -> Dict[str, Any]:
    """Parse first-turn vs later-turn TTFT and needed-sets from a scorecard.

    Reads ``scenarios[].turns[].performance.time_to_first_token`` and
    ``scenarios[].turns[].agent_tools`` (the per-turn "needed set" for the
    Part-1 recall gate). Null TTFTs are skipped in aggregates but counted, so
    a scorecard where the backend reported no timing is honestly surfaced
    rather than silently treated as zero.
    """
    with open(path, "r", encoding="utf-8") as fp:
        scorecard = json.load(fp)

    first_turn: List[float] = []
    later_turn: List[float] = []
    needed_set_sizes: List[int] = []
    null_first = 0

    for scenario in scorecard.get("scenarios", []):
        turns = scenario.get("turns", [])
        for idx, turn in enumerate(turns):
            ttft = (turn.get("performance") or {}).get("time_to_first_token")
            if idx == 0:
                if ttft is None:
                    null_first += 1
                else:
                    first_turn.append(ttft)
            elif ttft is not None:
                later_turn.append(ttft)
            tools = turn.get("agent_tools") or []
            needed_set_sizes.append(len(tools))

    return {
        "first_turn": _dist(first_turn),
        "first_turn_null_count": null_first,
        "later_turn": _dist(later_turn),
        "needed_set_sizes": needed_set_sizes,
        "max_needed_set": max(needed_set_sizes) if needed_set_sizes else 0,
    }


DEFAULT_TTFT_BASE_URL = "http://localhost:13305/api/v1"
DEFAULT_TTFT_MODEL = "Gemma-4-E4B-it-GGUF"
DEFAULT_TTFT_QUERY = (
    "What does the employee handbook say about remote work? Find and read it."
)


def _schemas_for(
    agent: "ChatAgent", names: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Native tool schemas for the full set, or just *names* when given."""
    prev = agent._instance_tools
    if names is not None:
        assert prev is not None, "skeleton must snapshot tools before rendering"
        agent._instance_tools = {n: prev[n] for n in names if n in prev}
    try:
        return agent._build_openai_tool_schemas()
    finally:
        agent._instance_tools = prev


def measure_prefill_ttft(
    agent: "ChatAgent",
    base_url: str = DEFAULT_TTFT_BASE_URL,
    model: str = DEFAULT_TTFT_MODEL,
    filter_to: Optional[List[str]] = None,
    n_trials: int = 5,
    user_message: str = DEFAULT_TTFT_QUERY,
) -> Dict[str, Any]:
    """Measure cold (uncached) prompt-prefill TTFT for the tool schemas.

    Sends streaming chat requests carrying the agent's native tool schemas to an
    **already-resident** model and times the first token. A unique nonce is
    prepended to the system message every trial to defeat llama.cpp's prefix
    cache, so each call re-prefills the whole tool block — isolating the prefill
    cost the tool-loader actually changes from model-load and cache-hit effects.
    RAG is never touched, so this is immune to the embedder↔chat-model swap.

    Requires a backend at *base_url* with *model* already loaded. Raises with an
    actionable message if the backend is unreachable — no silent fallback. The
    first request is a discarded warm-up; *n_trials* timed requests follow.
    """
    from openai import OpenAI

    tools = _schemas_for(agent, filter_to)
    client = OpenAI(base_url=base_url, api_key="not-needed-for-lemonade")

    def _one(nonce: int) -> float:
        # Nonce at the START of the system message busts the prefix cache so the
        # full tool block is re-prefilled, not served from a warm cache.
        system = f"[req-{nonce}] You are a document assistant. Use tools when needed."
        start = time.perf_counter()
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            tools=tools,  # type: ignore[arg-type] # Lemonade accepts raw dict schemas
            stream=True,
            max_tokens=8,
            temperature=0,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta  # type: ignore[union-attr]
            if delta.content is not None or delta.tool_calls is not None:
                return time.perf_counter() - start
        return time.perf_counter() - start

    try:
        _one(0)  # warm-up, discarded
        samples = [_one(1000 + i) for i in range(n_trials)]
    except Exception as exc:  # noqa: BLE001 - re-raised with guidance
        raise RuntimeError(
            f"prefill TTFT bench could not reach a loaded model at {base_url} "
            f"(model={model!r}): {exc}. Start the backend and pre-load the model "
            "(e.g. `python -m gaia.ui.server` + load the model in Lemonade), then "
            "retry. This bench never loads/evicts models itself."
        ) from exc

    return {
        "base_url": base_url,
        "model": model,
        "tool_count": len(tools),
        "filter_to": filter_to,
        "n_trials": n_trials,
        "ttft": _dist(samples),
        "samples": samples,
    }


def _fmt(value: Optional[float], suffix: str = "") -> str:
    """Format a number for the markdown table, or 'n/a' when None."""
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def format_markdown_report(
    profile: str = DEFAULT_PROFILE, scorecard_path: Optional[str] = None
) -> str:
    """Build the Part-0 measurement note for *profile* as markdown.

    Deterministic sections (cost, distribution, slope, fixed-subset) are
    computed live. TTFT is filled from *scorecard_path* when given; otherwise
    the live-run commands are emitted and the numbers left for the operator —
    Part 0 never fabricates a backend measurement.
    """
    tok = get_tokenizer()
    agent = build_doc_agent_skeleton(profile)
    cost = measure_tool_prompt_cost(agent, tok=tok)
    dist = tool_size_distribution(agent, tok=tok)
    slope = measure_slope(agent, tok=tok)
    fixed = measure_fixed_subset(agent, tok=tok)

    tok_note = (
        f"tiktoken `{TOKENIZER_ENCODING}` (proxy)"
        if tok is not None
        else "tiktoken unavailable — char counts only"
    )
    lines: List[str] = []
    lines.append(f"# #1448 Part 0 — tool-prompt cost & TTFT baseline ({profile})")
    lines.append("")
    lines.append(
        f"- Profile: `{profile}`  |  Tools (deterministic): **{cost['tool_count']}**"
    )
    lines.append(f"- Tokenizer: {tok_note}")
    lines.append("")
    lines.append(
        f"**The {cost['tool_count']} is the full *unfiltered* `{profile}` registry — the "
        '"before" number the loader must shrink, not a CORE set.** It bundles '
        "everything the agent ships today (RAG, file, memory, shell, loop-control, "
        "clipboard, TTS, desktop-notify, window-listing, VLM), while a typical "
        "turn needs only a couple. CORE (the small always-on set) is a Part-1 "
        "decision (#1449, Open Q1) — not measured here."
    )
    lines.append("")
    lines.append("## Tool-prompt cost (both render paths)")
    lines.append("")
    lines.append("| Path | Tokens | Chars |")
    lines.append("|------|-------:|------:|")
    lines.append(
        f"| Text (`_format_tools_for_prompt`) | {_fmt(cost['text_tokens'])} | {cost['text_chars']} |"
    )
    lines.append(
        f"| Native (`_build_openai_tool_schemas`) | {_fmt(cost['native_tokens'])} | {cost['native_chars']} |"
    )
    if cost["native_chars"]:
        ratio = cost["native_chars"] / max(cost["text_chars"], 1)
        lines.append("")
        lines.append(
            f"Native/Text char ratio: **{ratio:.2f}×** (native is where the real tokens are)."
        )
    lines.append("")
    lines.append("### Cost-premise correction")
    lines.append("")
    native_med = dist["native"]["tokens"]
    med_str = _fmt(native_med["median"]) if native_med else "n/a"
    lines.append(
        f'#1448 opened on a "~12K tokens / ~400-per-tool" premise. The measured '
        f"native baseline is **{_fmt(cost['native_tokens'])} tok** at a **median of "
        f"~{med_str} tok/tool** — roughly 3× cheaper per tool than assumed. Reason: "
        "the `@tool` decorator derives each schema from the function **signature + "
        "docstring** and drops the hand-written `description=`/`parameters=` kwargs, "
        "so native param props carry no descriptions and per-tool size is "
        "docstring-dominated. Two consequences for the go/no-go: (1) the prize from "
        "filtering is real but smaller per tool, so `max_tools` must be sized off "
        "the measured slope below, not the original estimate; (2) if the decorator "
        "is ever changed to honor `parameters=` descriptions, native cost jumps "
        "~3–4× and this baseline shifts — the reduction target must not be measured "
        "against a moving floor."
    )
    lines.append("")
    lines.append("## Per-tool size distribution")
    lines.append("")
    lines.append("| Path · metric | min | median | max | mean |")
    lines.append("|---------------|----:|-------:|----:|-----:|")
    for path_name in ("text", "native"):
        for metric in ("tokens", "chars"):
            d = dist[path_name][metric]
            if d is None:
                continue
            lines.append(
                f"| {path_name} · {metric} | {_fmt(d['min'])} | "
                f"{_fmt(d['median'])} | {_fmt(d['max'])} | {_fmt(d['mean'])} |"
            )
    lines.append("")
    lines.append("## Slope (cost as synthetic median-sized tools are added)")
    lines.append("")
    lines.append("| +K tools | Text tok | Native tok | Text chars | Native chars |")
    lines.append("|---------:|---------:|-----------:|-----------:|-------------:|")
    for row in slope["rows"]:
        lines.append(
            f"| {row['k']} | {_fmt(row['text_tokens'])} | {_fmt(row['native_tokens'])} "
            f"| {row['text_chars']} | {row['native_chars']} |"
        )
    s = slope["slope"]
    lines.append("")
    lines.append(
        f"Per-added-tool slope: text {_fmt(s.get('text_tokens'))} tok / "
        f"native {_fmt(s.get('native_tokens'))} tok "
        f"({_fmt(s.get('native_chars'))} chars)."
    )
    lines.append("")
    lines.append("## A fixed loaded subset stays flat as the registry grows")
    lines.append("")
    lines.append(
        f"Illustrative subset ({len(FIXED_SUBSET_DEFAULT)} tools, **not** the final "
        f"CORE — CORE membership is Part-1 Open Q1): "
        f"`{', '.join(FIXED_SUBSET_DEFAULT)}`"
    )
    lines.append("")
    lines.append("| Registry +K | Native chars (subset only) |")
    lines.append("|------------:|---------------------------:|")
    for row in fixed:
        lines.append(f"| {row['k']} | {row['native_chars']} |")
    lines.append("")
    lines.append(
        "Rendering only the subset is constant while the registry grows by K — "
        "prompt cost scales with tools *loaded*, not *registered*. That is the "
        "property the Part-1 loader exploits; this subset is a stand-in to prove "
        "the mechanism, not a proposal for what CORE should contain."
    )
    lines.append("")
    lines.append(_ttft_section(scorecard_path))
    return "\n".join(lines)


def _ttft_section(scorecard_path: Optional[str]) -> str:
    """Build the TTFT section: live numbers if a scorecard is supplied."""
    if scorecard_path:
        ttft = parse_ttft_from_scorecard(scorecard_path)
        first = ttft["first_turn"]
        later = ttft["later_turn"]
        lines = [
            "## First-turn TTFT baseline",
            "",
            f"Source scorecard: `{scorecard_path}`",
            "",
            "| Turn | n | mean (s) | median (s) | min | max |",
            "|------|--:|---------:|-----------:|----:|----:|",
        ]
        for label, d in (("first", first), ("later", later)):
            if d is None:
                lines.append(f"| {label} | 0 | n/a | n/a | n/a | n/a |")
            else:
                lines.append(
                    f"| {label} | {d['n']} | {_fmt(d['mean'])} | {_fmt(d['median'])} "
                    f"| {_fmt(d['min'])} | {_fmt(d['max'])} |"
                )
        lines.append("")
        lines.append(
            f"First-turn null TTFTs (backend reported none): "
            f"{ttft['first_turn_null_count']}"
        )
        lines.append(
            f"Max needed-set size across turns: {ttft['max_needed_set']} "
            "(the Part-1 recall floor)."
        )
        return "\n".join(lines)

    return "\n".join(
        [
            "## First-turn TTFT baseline (run live — not fabricated)",
            "",
            "TTFT requires a Lemonade backend on the reference model. Run, then",
            "re-render this report with `--scorecard <path>`:",
            "",
            "```bash",
            "python -m gaia.ui.server --port 4200 --host 127.0.0.1   # gemma-4-e4b",
            "gaia eval agent --category tool_selection --agent-type doc",
            "#   -> eval/results/<run-id>/scorecard.json",
            "python -m gaia.eval.tool_cost --profile doc \\",
            "    --scorecard eval/results/<run-id>/scorecard.json",
            "```",
            "",
            "Cold-start micro-bench (authoritative cold number): evict/restart the",
            "model, send ONE `doc` query, record its TTFT — eval scenarios may run",
            "warm. ⚠️ Only ONE `gaia eval agent` at a time (CLAUDE.md eval-serial).",
        ]
    )


def _run_live_ttft(
    profile: str,
    base_url: str,
    model: str,
    trials: int,
    filter_names: Optional[List[str]] = None,
) -> str:
    """Compare full-vs-subset prompt-prefill TTFT against a resident model.

    When *filter_names* is given (``--filter`` on the CLI), it is the loaded
    subset to measure — e.g. the Part-1 loader's actual selection. Otherwise the
    illustrative ``FIXED_SUBSET_DEFAULT`` is used.
    """
    subset_names = filter_names if filter_names else list(FIXED_SUBSET_DEFAULT)
    agent = build_doc_agent_skeleton(profile)
    full = measure_prefill_ttft(agent, base_url=base_url, model=model, n_trials=trials)
    subset = measure_prefill_ttft(
        agent,
        base_url=base_url,
        model=model,
        filter_to=subset_names,
        n_trials=trials,
    )
    fm, sm = full["ttft"], subset["ttft"]
    delta = (fm["median"] - sm["median"]) if (fm and sm) else None
    lines = [
        "# #1448 Part 0 — prompt-prefill TTFT (no RAG, model resident)",
        "",
        f"- Backend: `{base_url}`  |  Model: `{model}`  |  Trials: {trials} "
        "(cache-busted, first discarded)",
        "- RAG untouched — immune to the embedder↔chat-model swap.",
        "",
        "| Tool prompt | Tools | TTFT median (s) | min | max |",
        "|-------------|------:|----------------:|----:|----:|",
        f"| full | {full['tool_count']} | {_fmt(fm['median'] if fm else None)} "
        f"| {_fmt(fm['min'] if fm else None)} | {_fmt(fm['max'] if fm else None)} |",
        f"| subset | {subset['tool_count']} | {_fmt(sm['median'] if sm else None)} "
        f"| {_fmt(sm['min'] if sm else None)} | {_fmt(sm['max'] if sm else None)} |",
        "",
        f"**First-turn prefill saving from filtering "
        f"{full['tool_count']}→{subset['tool_count']} tools: "
        f"~{_fmt(delta)} s.** Later (cached) turns see ~0 — the win is on turn 1 "
        "and any turn where the tool set changes.",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: print the Part-0 markdown measurement note."""
    parser = argparse.ArgumentParser(
        prog="python -m gaia.eval.tool_cost",
        description="Measure tool-prompt cost / slope (#1448 Part 0).",
    )
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        help="ChatAgent prompt profile (default: doc)",
    )
    parser.add_argument(
        "--scorecard",
        default=None,
        help="Optional gaia-eval scorecard.json to fill the TTFT section.",
    )
    parser.add_argument(
        "--live-ttft",
        action="store_true",
        help="Measure full-vs-subset prompt-prefill TTFT against a resident "
        "model (no RAG). Requires a backend with the model already loaded.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_TTFT_BASE_URL,
        help=f"Backend base URL for --live-ttft (default: {DEFAULT_TTFT_BASE_URL})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_TTFT_MODEL,
        help=f"Resident model for --live-ttft (default: {DEFAULT_TTFT_MODEL})",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=5,
        help="Timed trials per tool set for --live-ttft (default: 5)",
    )
    parser.add_argument(
        "--filter",
        default=None,
        help="Comma-separated tool names to measure as the loaded subset for "
        "--live-ttft (e.g. the Part-1 loader's selection). Defaults to the "
        "illustrative FIXED_SUBSET_DEFAULT.",
    )
    args = parser.parse_args(argv)
    if args.live_ttft:
        filter_names = (
            [n.strip() for n in args.filter.split(",") if n.strip()]
            if args.filter
            else None
        )
        print(
            _run_live_ttft(
                args.profile, args.base_url, args.model, args.trials, filter_names
            )
        )
    else:
        print(format_markdown_report(args.profile, args.scorecard))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
