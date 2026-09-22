# Why the flagship GAIA agent is slow through the TUI

**The flagship re-sends ~17,000 tokens of prompt to a 4B model on every LLM
call, 2–5 times per turn. This branch cuts that by 53%.**

Phase 1 (below) is the diagnosis; Phase 2 is what changed and what it bought.

**Backends, stated up front** — no number in this document mixes them:

- **Token counts** (every table except one) — tiktoken `cl100k_base`, computed
  offline against the real composed prompt. **No model contacted.**
- **The 387 tok/s prefill rate**, and every "seconds" figure derived from it —
  **Lemonade / Gemma-4-E4B, measured 2026-08-18** before the backend was banned.
  Not re-taken since.
- **Per-turn latency after the change** — *not measured*. See
  [Numbers still owed](#numbers-still-owed--blocked-not-skipped).

Phase 1's counts came from `.perf/dump_prompt.py` with Lemonade up; Phase 2 uses
`.perf/offline_prefill.py`, which contacts nothing. The two disagree by ~20
tokens on the baseline (16,993 vs 17,014) because the memory block grew between
runs — stored facts are live data. Phase 2's numbers are the ones to quote:
baseline and patched were taken with the same script, minutes apart.
`dump_prompt.py` was a throwaway live-Lemonade script and is deliberately not
committed — it printed the composed prompt, which embeds the user's memory store
verbatim, so `.perf/.gitignore` excludes it.

## The headline

**Every LLM call the flagship makes carries ~17,000 tokens of fixed prefill
before the user's question is appended.** A conversational turn is a ReAct loop
of 2–5 LLM calls, so a three-step turn re-presents ~51K tokens to a 4B model.
That cost is paid on turn 1 with nothing loaded.

### Prefill runs at ~390 tok/s on this box, so tokens *are* seconds

**Backend: Lemonade / Gemma-4-E4B-it-GGUF, GPU profile, measured 2026-08-18
before Lemonade was banned.** It has not been re-taken since, and cannot be
until the ban lifts — every "seconds" figure in this document derives from this
one reading. Read straight off Lemonade's own counters during the live demo
session (`GET /api/v1/stats`, read-only, no inference run):

```json
{"input_tokens": 22919, "output_tokens": 150,
 "time_to_first_token": 59.193109, "tokens_per_second": 34.037,
 "request_count_total": 48, "input_tokens_total": 171423}
```

22,919 prompt tokens against a 59.2 s time-to-first-token is **387 tok/s of
prefill** — while generation runs at 34 tok/s. Prefill is not a rounding error
next to generation; on a turn like that it *is* the turn.

At that rate the fixed prefill alone costs:

| | tokens | seconds |
|---|---:|---:|
| native tool schemas (66) | 10,236 | **26.4** |
| system prompt | 6,757 | **17.5** |
| **fixed, every LLM call** | **16,993** | **43.9** |

That single measurement is what makes the rest of this document actionable:
**every 1,000 tokens cut from the prompt gives back ~2.6 seconds per LLM call**,
multiplied by the number of ReAct steps in the turn. It is also the strongest
argument for the KV-cache ordering fix in Finding 3 — a cache hit converts those
seconds to roughly zero, and a busted prefix pays them again.

| what | tiktoken tokens | share |
|---|---:|---:|
| native tool schemas (`tools=`, 66 tools) | 10,236 | 60% |
| system prompt | 6,757 | 40% |
| **fixed prefill per LLM call** | **16,993** | |

System prompt breakdown (28,957 chars):

| section | chars | ~tok | share of prompt |
|---|---:|---:|---:|
| `==== LOADED SKILLS ====` (just `gaia-voice`) | 8,794 | ~2,050 | 30.4% |
| `==== AVAILABLE TOOLS ====` one-liners | 8,003 | 1,678 | 27.7% |
| ChatAgent `full`-profile instructions | 7,845 | ~1,830 | 27.1% |
| memory block (prefs + facts) | 3,941 | ~920 | 13.6% |
| vision mixin fragment | 320 | ~75 | 1.1% |
| response-format template | 0 | 0 | *correctly skipped* |

---

## Finding 1 — the tool list is sent twice (~1,678 tok/call, free to fix)

`is_tool_calling_model("Gemma-4-E4B-it-GGUF")` is **True**, so `_openai_tools`
ships all 66 full JSON function schemas in `tools=`. `_compose_system_prompt`
then *also* appends an `==== AVAILABLE TOOLS ====` block listing the same 66
tools as `name(params): first line of docstring`.

The same function already knows how to skip a redundant block — it gates
`_response_format_template` on `is_tool_calling_model`. The tool block has no
such gate:

```python
# src/gaia/agents/base/agent.py::_compose_system_prompt
if tool_filter is None and tools_block is not None:
    parts.append(tools_block)                        # <- unconditional
...
if not self._use_claude and not is_tool_calling_model(self.model_id):
    parts.append(self._response_format_template)     # <- gated
```

Everything in the one-liner block (name, params, first docstring line) is a
strict subset of the JSON schema. **~10% of total prefill, no capability lost.**
Still an LLM-affecting change, so it needs an eval.

## Finding 2 — dynamic tool loading exists, is tested, and is off for the flagship

The `ToolLoader` from #1449/#1450 — CORE set, cohesion bundles, semantic
selection, `load_tools` escape hatch, drift-guard CI tests, `format_bundle_menu`
prompt block — is fully built. It is switched off for the flagship by **two
independent gates**:

1. `ChatAgentConfig.dynamic_tools = False` (default-off, experimental)
2. `_maybe_build_tool_loader()` returns `None` unless `prompt_profile == "doc"`
   — and `GaiaAgentConfig.prompt_profile = "full"`

Measured at the two ends of the range:

| tools sent | tokens |
|---|---:|
| all 66 (today) | 10,236 |
| CORE only (10) | 2,019 |

A realistic CORE + 2 matched bundles lands ~3,500–4,500 tok. **That is a
6,000–7,000 token cut per LLM call, ~40% of total prefill.**

**Blocker:** `DOC_BUNDLES` was written for the `doc` registry and covers only 38
of the flagship's 66 tools. 29 are unbundled, so enabling the loader as-is would
make them permanently invisible:

```
bookmark, clear_code_index, create_table, download_file, drop_table,
execute_python_file, fetch_page, fetch_webpage, file_info, find_files,
get_index_status, index_codebase, insert_data, install_skill, list_files,
list_skills, list_tables, load_skill, open_url, query_data, remove_skill,
search_code_index, search_documentation, search_skill_hub, search_web,
skill_status, take_screenshot, tree, unload_skill
```

Enabling it means authoring `FULL_CORE_TOOLS` / `FULL_BUNDLES` covering all 66,
with the same union-equality drift guard `test_chat_tool_bundles.py` already
enforces for `doc`. `load_tools` is CORE-only and is currently *not* registered
in the full profile — it has to come along.

## Finding 3 — the prompt is ordered worst-case for KV-cache prefix reuse

Composition order today is **volatile first, static last**:

```
[memory  — changes on any remember()]
[skills  — changes per turn under dynamic_skills=True]
[static ChatAgent instructions]      ~1,830 tok
[static tool one-liners]             ~1,678 tok
```

llama.cpp reuses the KV cache only up to the first differing token. So one
`remember()` call, or one per-turn skill-body swap, invalidates **everything
after it** — including ~3,500 tokens of text that never changed.

The memory subsystem already does the right thing internally: stable facts go in
the system prompt, time-sensitive context is prepended to the *user message*,
and the docstring says so explicitly ("keep this prompt frozen for LLM KV-cache
reuse"). The placement then throws the benefit away. Reordering static-before-
volatile is a pure win and changes no content.

`GaiaAgentConfig.dynamic_skills = True` for this agent specifically, so the
skills block is *designed* to change per turn — which makes its position at the
front of the prompt actively expensive.

## Finding 4 — ruled out: embeddings do not evict the chat model

The #1030 pattern (embedder warm-up evicts the LLM, next turn silently reloads)
does **not** apply on this box. Lemonade reports separate slot pools:

```
Gemma-4-E4B-it-GGUF        slot_pool=standard/llm        ctx_size=65536  loaded
nomic-embed-text-v2-moe    slot_pool=standard/embedding  ctx_size=8192   loaded
max_models: {"llm": 1, "embedding": 1}
```

Both resident, Gemma at the correct 65536 ctx. Per-turn embedding calls (memory
recall + dynamic-skill selection) cost one small HTTP round-trip each, not a
model reload. **This suspect is dead — do not spend time on it.**

`_ensure_model_loaded` still runs a status probe before every LLM call, so a
five-step turn makes five redundant probes. Cheap, but per-call rather than
per-turn.

## Finding 5 — process-level costs are one-time, not per-turn

| | seconds |
|---|---:|
| `import gaia_agent` (faiss, RAG deps) | 6.3 |
| `GaiaAgent()` construction | 2.1 |
| **agent cold start** | **8.4** |

Paid once when the TUI spawns the sidecar, not per turn. Construction discovers
37 skills across 5 roots, opens two SQLite DBs, rebuilds two FAISS indexes
(60 + 18 vectors) and validates the embedder. The agent object is long-lived
(`stdio.py` builds it once); `conversation_history` is capped at 12
user/assistant pairs.

**The 157.6s cold turn 1 is almost certainly model load, not agent overhead** —
but that needs a live measurement to confirm.

---

## Ranked plan

| # | change | est. saving per LLM call | risk |
|---|---|---:|---|
| 1 | Gate the `AVAILABLE TOOLS` block on `is_tool_calling_model`, matching the existing `_response_format_template` gate | −1,678 tok (10%) | low; needs eval |
| 2 | Reorder `_compose_system_prompt` static-before-volatile | 0 tok, but preserves ~3,500 tok of KV prefix across memory writes and skill swaps | low; content byte-identical |
| 3 | Author `FULL_CORE_TOOLS` / `FULL_BUNDLES`, drop the `profile == "doc"` gate, default `dynamic_tools=True` for `GaiaAgent` only | −6,000 to −7,000 tok (40%) | medium; the escape hatch and the eval are what make it safe |
| 4 | Trim the `full` profile prompt — RAG/discovery workflow rules are emitted even when nothing is indexed | −400 to −800 tok | medium |
| 5 | Collapse the per-call `_ensure_model_loaded` probe to once per turn | ~0 tok, N fewer HTTP round-trips | low |

Combined 1 + 3 takes fixed prefill from **16,993 → ~8,300 tok, a 51% cut.**

**Not yet measured: the token→latency conversion.** Everything above is a token
count. Converting it to seconds needs one live run on a quiet box — prefill
tok/s for Gemma-4-E4B on gfx1151, and the real ReAct step count per turn. Do
that before and after, or the improvement is unproven.

Per CLAUDE.md, changes 1, 3 and 4 all touch LLM-affecting surfaces (system
prompt, tool schemas) and require `gaia eval agent` against the committed
baseline before they can be called done.

---

## Observability gaps

The agent already computes what we need and then discards it.

**What exists:** per-step `performance_stats` (input/output tokens, ttft,
tok/s) are appended to the in-memory `conversation` list, and the TUI surfaces
exactly two derived numbers — `ttft %.1fs` and `%.1f tok/s`.

**What is missing everywhere:**

- prompt/prefill token count — the number this whole document is about is not
  observable at runtime
- ReAct step count for the turn, and per-step wall time
- tool execution time, separated from model time
- which tools were sent this turn — once dynamic loading is on, this is the
  first thing you need to debug a wrong answer
- which skill bodies rendered this turn
- whether the backend hit or missed its KV prefix cache

**Proposed:**

1. **TUI status line** — extend the existing stats line to
   `ttft 2.1s · 14.2 tok/s · 3 steps · 17.0k prefill · 4.8s tools`. Same render
   site, four more fields on the canonical `final` event.
2. **Per-turn JSON log, dev-mode only** — `GAIA_TURN_LOG=<path>` writes one JSON
   object per turn: query, per-step records (prefill tokens, output tokens,
   ttft, wall time, tool name, tool duration), the active tool filter, the
   active skill filter, and the composed system-prompt length. One line per
   turn, append-only, so a session is greppable and diffable across builds.
   This is what makes a before/after claim checkable instead of anecdotal.
3. **Off by default** — the env flag gates it, matching `GAIA_AGENT_LOG`.

---

# Phase 2 — what was changed, and what it bought

**Backend for every number in this section: none.** All counts are tiktoken
`cl100k_base` over the real composed prompt and the real tool schemas, produced
by `.perf/offline_prefill.py`, which contacts no model — it stubs the embedder
and `LemonadeManager.ensure_ready`, and arms every `requests` verb to raise so a
run that touched the network would fail rather than mislead. Baseline and
patched were measured with the identical script, on the same machine, minutes
apart.

### Fixed prefill per LLM call

| | baseline (9c89cb47) | this branch | Δ |
|---|---:|---:|---:|
| system prompt | 6,778 | **3,946** | −2,832 |
| tool schemas sent (`tools=`) | 10,236 (66 tools) | **~4,061** (26 of 67) | −6,175 |
| **fixed prefill** | **17,014** | **~8,007** | **−9,007 (−53%)** |

Because tool selection is per-query, the patched tool figure is a **bound, not
an observed selection** — selection runs through the embedder, which needs a
backend. The three bounds, all measured offline:

| loader outcome | tools= | fixed prefill |
|---|---:|---:|
| CORE only (floor, 10 tools) | 2,154 | 6,100 |
| cap-26, average-sized (the row above) | 4,061 | 8,007 |
| cap-26, the 26 *largest* schemas (worst case) | 6,322 | 10,268 |

So the saving is **−6,746 worst case to −10,914 floor**, −9,007 typical. Even
the worst case beats baseline by 40%.

### Where the system-prompt saving came from

| section | baseline | this branch | Δ |
|---|---:|---:|---:|
| `==== AVAILABLE TOOLS ====` prose block | 1,685 | **0** | −1,685 |
| `gaia-voice` skill body | 2,145 | **692** | −1,453 |
| profile prompt | 1,878 | 2,184 | **+306** |
| memory block | 1,011 | 1,011 | 0 |
| vision mixin | 59 | 59 | 0 |
| **total** | **6,778** | **3,946** | **−2,832** |

The `+306` is the `==== LOADABLE TOOL BUNDLES ====` menu that dynamic tool
loading adds — the escape hatch that keeps a semantic miss recoverable. It is a
real cost of the loader and is already netted into the totals above.

**This is the combined figure for all three branches** (this one, `49822449`
dynamic tools, `4ec55586` voice trim), stated once. Do not add their individual
numbers together — they compose in the single table above.

> **A measurement taken with Lemonade down is wrong, not merely unavailable.**
> `init_memory` disables memory v2 when the embedder is unreachable, silently
> skipping registration of its 5 tools and dropping the 1,011-token memory
> block. The registry then reads 62 instead of 67 and the prompt looks ~900
> tokens leaner than it is. `.perf/offline_prefill.py` stubs the embedder for
> exactly this reason and **aborts** if memory did not initialise.

### 1. The duplicate tool block is gone for native tool-calling models

`_compose_system_prompt` now gates the `==== AVAILABLE TOOLS ====` block on the
same condition that already gated `_response_format_template`, via a new shared
`Agent._uses_native_tool_calls()`. Gemma gets the JSON schemas through `tools=`;
it no longer gets a prose restatement of the same 66 names.

Non-native models are unaffected — for them the text block is the *only* way
they learn the tool names, and it still renders.

One user-visible follow-on: the unknown-tool error message used to say "use only
tools listed in your AVAILABLE TOOLS section", which now points at something a
native model never receives. It reads "Use only the tools you were given."

### 2. The prompt is reordered static-first

New `Agent.VOLATILE_PROMPT_FRAGMENTS` names the `get_*_system_prompt` fragments
that change mid-session (memory, skills, procedural recall). `_get_mixin_prompts`
records which method produced which fragment — so a subclass that *filters*
fragments (ChatAgent drops the SD prompt) still gets the split, and no fragment
method runs twice.

Composition is now:

```
[static mixins] [agent prompt] [tool block if text-path] [response format]
  ... then ...
[memory] [skills] [filtered tool block]
```

A `remember()` or a per-turn skill swap now invalidates only the tail. Before,
it invalidated ~3,500 tokens of text that had not changed.

### 3. `gaia-voice` trimmed 2,129 → 676 tokens

It was a rationale document — every rule followed by the incident that motivated
it. The model needs the rule; the incident report belongs in the commit message.
All 24 behavioural rules survive. The manifest's "~900 tokens, measured" comment
was wrong by a factor of 2.4 and now states the real measured number.

### 4. Dynamic tool loading is on for the flagship

`FULL_CORE_TOOLS` / `FULL_BUNDLES` cover all 66 flagship tools (the `doc` set
covered 38), with the same union-equality drift guard. `dynamic_tools=True` on
`GaiaAgentConfig` only — plain ChatAgent's default stays off. `dynamic_tools_max`
raised 14 → 26: the inherited 14 left 3 dynamic slots, less than one 6-member
bundle, so the flagship would have truncated a cohesion group mid-pull.

## Observability

`GAIA_TURN_LOG=<path>` writes one JSON object per turn
(`src/gaia/agents/base/turn_metrics.py`). Off by default — one env lookup per
turn when unset.

Recorded per turn: absolute start/end timestamps, total submit-to-answer wall
time, step count, the fixed-prefill breakdown, which tools and skills were sent.
Per LLM call: wall time, ttft, tok/s, **input tokens split cached vs new**, the
derived prefill rate, and the backend's stats dict verbatim.

The cached/new split is computed as the common prefix with the previous call's
rendered prompt — the same comparison llama.cpp makes — so it measures what we
*offer* the cache, and `prefill_tok_per_s` next to it shows whether the server
took it. That pair is what makes Finding 3 checkable rather than theoretical.

Server-reported and locally-counted token totals are kept in separate fields
(`*_server` vs `*_local`). They use different tokenizers; adding one to the other
produces a number that means nothing.

## Numbers still owed — blocked, not skipped

Lemonade was banned mid-task: running it was crashing the development machine, so
local inference stayed disabled there for the rest of the work. Everything below
needs a **Gemma-4-E4B on Lemonade** measurement. **Claude Haiku is not a
substitute for any of them** — it is valid evidence for logic, not for
local-model latency or eval scores. None of these are estimated here, and none
should be quoted from the arithmetic above as though they had been observed.

| # | owed number | why it needs Lemonade | how to get it |
|---|---|---|---|
| 1 | **`gaia eval agent` scorecard vs the committed baseline** | Behaviour, not speed. Four LLM-affecting surfaces changed: the system prompt lost 2,832 tokens, its section order changed, the tool list is no longer restated in prose, and the model now sees ≤26 of 67 tools per turn. A quality regression is invisible to every unit test here. | `gaia eval agent --category <cat>`, then `--compare tests/fixtures/eval_baselines/gemma-4-e4b-d71cd914/scorecard_<cat>.json <run>/scorecard.json`. Quiet box, one eval at a time. |
| 2 | **Per-turn wall time, before vs after** | The deliverable was "the agent is slow". Token counts are the cause; seconds are the claim. | Same four prompts as the symptom table at the top, on both branches, with `GAIA_TURN_LOG` set. |
| 3 | **Post-change prefill rate (tok/s)** | 387 tok/s was measured on the *baseline* prompt. A shorter prompt is not guaranteed to prefill at the same rate. | Read `prefill_tok_per_s` out of the turn log. |
| 4 | **Actual per-query tool selection** | Selection is semantic and runs through the embedder. Only the bounds are known offline. | `GAIA_TURN_LOG` records `prompt.tool_names` per turn; compare against the bounds table. |
| 5 | **Whether llama.cpp honours the reordered prefix** | Finding 3 predicts the static head now survives a memory write. Predicted, never observed. | `cache_hit_ratio` and `prefill_tok_per_s` in the turn log, across a turn that calls `remember()`. |

**The one conversion that is NOT re-measurable right now:** "1,000 tokens ≈ 2.6 s
per LLM call" comes from 387 tok/s, read off Lemonade's own counters *before* the
ban. It is a real measurement of the old state and is quoted as such throughout.
It has not been re-taken, and the seconds it implies for this branch are
arithmetic, not observation — which is exactly why row 2 is owed.

### Not blocked, just out of scope

- `CodeAgent` builds its own `==== AVAILABLE TOOLS ====` block inside
  `_get_system_prompt` (`hub/agents/code/python/gaia_agent_code/agent.py`), so it
  still duplicates its schemas for a native tool-calling model. Same fix applies;
  it is a different agent and a different eval.

## Reproducing

One command, no model, prints every table in Phase 2:

```bash
W='<absolute worktree path, Windows separators>'
PYTHONPATH="$W\src;$W\hub\agents\chat\python;$W\hub\agents\gaia\python" \
  python .perf/offline_prefill.py
```

It aborts if memory v2 did not initialise, and raises if anything attempts an
HTTP call — so a run that silently measured a degraded agent, or quietly woke
Lemonade, fails instead of printing a plausible number.

**It prints the prompt's token counts, never the prompt.** A composed system
prompt embeds the user's memory store verbatim — names, preferences, file paths.
`.perf/.gitignore` is deny-by-default for the same reason; do not add a switch
that dumps the prompt to a tracked file.

### Two traps this work hit, both worth knowing

**Never time or split `_execute_tool`.** The first instrumentation attempt split
it into a wrapper plus `_execute_tool_impl`. That broke two things at once: a
test stand-in that copies the method off the class by attribute assignment
(`_execute_tool = Agent._execute_tool`) lost its `_impl`, and a contract test
reads the method back with `inspect.getsource` to prove argument coercion is
wired into the dispatch path. Tool timing now lives in a separate
`_execute_tool_timed` that the agent loop calls; `_execute_tool` is untouched.

**Use `.perf/runtests.sh` to run tests, not bare pytest.** This repo has
`amd-gaia` installed editable against the *main* checkout, so a worktree's
`PYTHONPATH` that is even slightly malformed — MSYS `/c/...` paths, mixed
separators from `$(pwd)` — silently falls through and every result then measures
unmodified main. That happened here and briefly produced a clean bill of health
for code that was not being executed. The runner derives the checkout root from
its own location, so it works in any clone, and it asserts `gaia.__file__` points
into that root and aborts if it does not. Set `GAIA_PYTHON` if `python` on `PATH`
is not the interpreter you want.
