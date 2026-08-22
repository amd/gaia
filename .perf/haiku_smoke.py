"""End-to-end smoke test of the turn-metrics harness on Claude Haiku 4.5.

Lemonade is banned, so this validates LOGIC only — that the recorder wires up,
fires on a real turn, times tools separately, and produces a well-formed record.
It says nothing about local-model latency and its numbers must never be quoted
as Gemma numbers.

What it exercises that unit tests cannot:
  * the SDK hooks firing inside a real streaming turn
  * the reordered system prompt actually composing and being accepted
  * the AVAILABLE TOOLS block being absent for a native tool-calling backend
  * _execute_tool_timed inside the real ReAct loop
  * the sealed record reaching the console hook

What it CANNOT exercise: dynamic tool selection and memory v2. Both need the
Lemonade embedder, which this script deliberately disables, so both sit in their
documented off-state and the agent sends its full registry.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

LOG = Path(tempfile.gettempdir()) / "gaia_haiku_smoke.jsonl"
if LOG.exists():
    LOG.unlink()
os.environ["GAIA_TURN_LOG"] = str(LOG)

# --use-claude already skips LemonadeManager.ensure_ready, but the memory
# subsystem embeds through Lemonade -- which is how an earlier run of this
# script spawned a llama-server for the embedding model while Lemonade was
# under a ban. This is the only remaining reach and it is closed here, so
# the script cannot touch a local model no matter how it is invoked.
os.environ["GAIA_MEMORY_DISABLED"] = "1"

from gaia_agent.agent import GaiaAgent, GaiaAgentConfig  # noqa: E402

PROMPTS = [
    "What is 17 times 23? Answer with just the number.",
    "Use your shell tool to run `pwd` and tell me the directory.",
]


def main() -> int:
    agent = GaiaAgent(
        config=GaiaAgentConfig(
            silent_mode=True,
            use_claude=True,
            claude_model="claude-haiku-4-5",
        )
    )

    print(f"backend          : Claude, model={agent.model_id!r}")
    print(f"_use_claude      : {agent._use_claude}")
    print(f"native tool calls: {agent._uses_native_tool_calls()}")
    print(f"registry         : {len(agent._tools_registry)} tools")
    print(f"memory v2        : {getattr(agent, '_memory_store', None) is not None}")
    print(f"tool_loader      : {agent.tool_loader is not None}")
    sp = agent.system_prompt
    print(f"system prompt    : {len(sp):,} chars")
    print(f"AVAILABLE TOOLS  : {'PRESENT (BUG)' if '==== AVAILABLE TOOLS ====' in sp else 'absent (correct)'}")
    print()

    for i, prompt in enumerate(PROMPTS, 1):
        print(f"--- turn {i}: {prompt}")
        result = agent.process_query(prompt)
        answer = (result.get("result") or "").strip().replace("\n", " ")
        print(f"    answer  : {answer[:160]}")
        print(f"    status  : {result.get('status')}  steps={result.get('steps_taken')}")
        print(f"    metrics : {'yes' if result.get('turn_metrics') else 'NO (BUG)'}")
        print()

    if not LOG.exists():
        print("FAIL: no turn log was written")
        return 1

    records = [json.loads(line) for line in LOG.read_text(encoding="utf-8").splitlines()]
    print(f"=== turn log: {len(records)} record(s) at {LOG}")
    failures = []
    for r in records:
        t, p = r["totals"], r["prompt"]
        print(
            f'  turn {r["turn_id"]}  {r["started_at"][11:19]}Z  '
            f'{r["total_s"]:.1f}s total  {r["steps"]} step(s)  '
            f'model {t["llm_s"]:.1f}s  tools {t["tool_s"]:.1f}s  '
            f'overhead {t["overhead_s"]:.1f}s'
        )
        print(
            f'      prefill {p["fixed_prefill_tokens"]:,} tok '
            f'({p["tools_sent"]} tools) | in {t["input_tokens_local"]:,} local '
            f'({t["input_tokens_cached_local"]:,} cached) | '
            f'out {t["output_tokens_server"]:,} server'
        )
        for c in r["llm_calls"]:
            print(
                f'      call step={c["step"]} wall={c.get("wall_s")}s '
                f'cached={c["input_tokens_cached"]:,} new={c["input_tokens_new"]:,} '
                f'hit={c["cache_hit_ratio"]:.0%}'
            )
        for tc in r["tool_calls"]:
            print(f'      tool {tc["name"]} {tc["wall_s"]}s ok={tc["ok"]}')

        # Invariants a real turn must satisfy.
        if r["schema"] != "gaia.turn/1":
            failures.append(f'{r["turn_id"]}: bad schema')
        if not r["llm_calls"]:
            failures.append(f'{r["turn_id"]}: no LLM call recorded')
        if r["total_s"] <= 0:
            failures.append(f'{r["turn_id"]}: total_s not positive')
        if t["llm_s"] <= 0:
            failures.append(f'{r["turn_id"]}: llm_s not positive')
        if t["llm_s"] > r["total_s"] + 0.5:
            failures.append(f'{r["turn_id"]}: llm_s exceeds total_s')
        if t["tool_s"] > r["total_s"] + 0.5:
            failures.append(f'{r["turn_id"]}: tool_s exceeds total_s')
        for c in r["llm_calls"]:
            if c["input_tokens_cached"] + c["input_tokens_new"] != c["input_tokens_local"]:
                failures.append(f'{r["turn_id"]}: cached+new != local')
        if p["fixed_prefill_tokens"] <= 0:
            failures.append(f'{r["turn_id"]}: no prefill recorded')

    # Known blind spot, stated rather than mis-reported: each turn builds a new
    # TurnRecorder, so the first call of every turn has no predecessor to
    # compare against and always reads 0% cached. Cross-turn prefix reuse --
    # exactly what the static-first prompt ordering targets -- is therefore NOT
    # visible in this record. Measuring it needs the comparison to survive
    # across recorders, or the backend's own cache counters.
    print(
        "\n  note: cross-turn cache reuse is not measured; the first call of "
        "each turn reads 0% by construction"
    )

    print()
    if failures:
        print("FAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS: every recorded turn satisfies the invariants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
