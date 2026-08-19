"""Offline fixed-prefill measurement for the flagship GAIA agent.

**Contacts no model.** The embedder is stubbed with a constant unit vector so
``init_memory`` succeeds and memory v2 registers its 5 tools and renders its
prompt block from the real SQLite store. Embedding *values* never affect the
composed prompt or the registry — only per-turn semantic selection uses them,
and this script does not run a turn.

Why the stub is required: with Lemonade unreachable, ``init_memory`` disables
memory v2, silently drops 5 tools, and omits the memory block. A measurement
taken in that state understates the prompt by ~900 tokens and reads as a win.

Token counts are tiktoken cl100k_base — a tokenizer-agnostic proxy, the same
one ``src/gaia/eval/tool_cost.py`` pins its baseline with. Absolute Gemma counts
differ; ratios and deltas do not.

Usage:
    PYTHONPATH="<wt>\\src;<wt>\\hub\\agents\\chat\\python;<wt>\\hub\\agents\\gaia\\python" \
        python .perf/offline_prefill.py
"""

from __future__ import annotations

import json
from unittest.mock import patch

import numpy as np
import tiktoken

_ENC = tiktoken.get_encoding("cl100k_base")


def tok(s: str) -> int:
    return len(_ENC.encode(s, disallowed_special=()))


def _fake_embed(_self, _text, **_kw):
    v = np.zeros(768, dtype=np.float32)
    v[0] = 1.0
    return v


def _fake_embed_batch(_self, texts, **_kw):
    return np.tile(_fake_embed(None, ""), (len(list(texts)), 1))


class NetworkContacted(AssertionError):
    """Raised if anything in this script would reach out over HTTP."""


def _no_network(*a, **kw):
    raise NetworkContacted(
        f"HTTP call attempted during an offline measurement: {a[:2]}. "
        "Lemonade is banned; the measurement must not contact it."
    )


def main() -> None:
    import requests

    from gaia.agents.base.memory import MemoryMixin
    from gaia.llm.lemonade_manager import LemonadeManager

    # Agent.__init__ calls ensure_ready(), which will START or preload Lemonade.
    # Under the ban that must never fire, so it is stubbed before construction
    # and every HTTP verb is armed to raise — the run proves it stayed offline
    # rather than asserting it in a comment.
    with patch.object(MemoryMixin, "_embed_text", _fake_embed), patch.object(
        MemoryMixin, "_embed_texts_batch", _fake_embed_batch, create=True
    ), patch.object(
        MemoryMixin, "_get_embedder", lambda self: object()
    ), patch.object(
        LemonadeManager, "ensure_ready", staticmethod(lambda *a, **k: None)
    ), patch.object(
        requests, "post", _no_network
    ), patch.object(
        requests, "get", _no_network
    ), patch.object(
        requests.Session, "request", _no_network
    ):
        from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

        agent = GaiaAgent(config=GaiaAgentConfig(silent_mode=True))

        registry = sorted(agent._tools_registry)
        memory_on = getattr(agent, "_memory_store", None) is not None
        sp = agent.system_prompt
        all_schemas = agent._build_openai_tool_schemas(filter_to=None) or []

        print("=" * 72)
        print("OFFLINE FIXED-PREFILL MEASUREMENT (no model contacted)")
        print("=" * 72)
        print(f"registry size          : {len(registry)}")
        print(f"memory v2 initialised  : {memory_on}   <- must be True")
        print(f"tool_loader active     : {agent.tool_loader is not None}")
        print(f"dynamic_tools          : {getattr(agent.config, 'dynamic_tools', None)}")
        print(
            f"dynamic_tools_max      : {getattr(agent.config, 'dynamic_tools_max', None)}"
        )
        print(f"loaded skills          : {sorted(getattr(agent, 'loaded_skills', {}))}")
        if not memory_on:
            raise SystemExit(
                "ABORT: memory v2 did not initialise; the stub failed and every "
                "number below would understate the prompt."
            )

        print()
        print("--- system prompt ---")
        sp_tokens = tok(sp)
        print(f"system prompt          : {sp_tokens:>7,} tok  ({len(sp):,} chars)")
        fragments = agent._get_mixin_prompts()
        # ``_mixin_prompt_origins`` only exists on the patched build; on baseline
        # fall back to positional labels so the same script measures both.
        origins = getattr(agent, "_mixin_prompt_origins", {})
        for i, frag in enumerate(fragments):
            label = origins.get(frag) or f"mixin[{i}]"
            print(f"    {label:<38}{tok(frag):>7,} tok")
        custom = agent._get_system_prompt()
        print(f"    {'_get_system_prompt (profile)':<38}{tok(custom):>7,} tok")

        print()
        print("--- native tool schemas (tools=) ---")
        sizes = sorted(
            ((tok(json.dumps(s)), s["function"]["name"]) for s in all_schemas),
            reverse=True,
        )
        all_tok = tok(json.dumps(all_schemas))
        print(f"ALL {len(all_schemas)} tools          : {all_tok:>7,} tok")

        core = _core_names(agent, registry)
        core_schemas = agent._build_openai_tool_schemas(filter_to=sorted(core)) or []
        core_tok = tok(json.dumps(core_schemas))
        print(f"CORE only ({len(core_schemas):>2})           : {core_tok:>7,} tok")

        cap = int(getattr(agent.config, "dynamic_tools_max", 26))
        worst = [n for _, n in sizes[:cap]]
        worst_tok = tok(json.dumps(agent._build_openai_tool_schemas(filter_to=worst)))
        mean_tok = round(all_tok * cap / len(all_schemas))
        print(f"cap={cap} worst case (biggest) : {worst_tok:>7,} tok")
        print(f"cap={cap} average-sized        : {mean_tok:>7,} tok")

        print()
        print("=" * 72)
        print("FIXED PREFILL PER LLM CALL (system prompt + tools=)")
        print("=" * 72)
        print(f"  unfiltered (loader off)      : {sp_tokens + all_tok:>7,} tok")
        print(f"  loader on, CORE-only floor   : {sp_tokens + core_tok:>7,} tok")
        print(f"  loader on, cap-{cap} average    : {sp_tokens + mean_tok:>7,} tok")
        print(f"  loader on, cap-{cap} worst case : {sp_tokens + worst_tok:>7,} tok")
        print()
        print("Selection for a REAL query needs the embedder and is not measured")
        print("here. The three loader rows are bounds, not an observed selection.")


def _core_names(agent, registry):
    """CORE set for the active profile, intersected with the live registry."""
    try:
        from gaia_agent_chat.tool_bundles import FULL_CORE_TOOLS

        core = set(FULL_CORE_TOOLS)
    except ImportError:
        from gaia_agent_chat.tool_bundles import DOC_CORE_TOOLS

        core = set(DOC_CORE_TOOLS)
    loader = getattr(agent, "tool_loader", None)
    if loader is not None and getattr(loader, "core_tools", None):
        core = set(loader.core_tools)
    return core & set(registry)


if __name__ == "__main__":
    main()
