# Context efficiency and overflow

**Status:** proposed · **Owner:** TBD · **Date:** 2026-10-13 · **Epic:** #5070
**Related:** [mcp-context-efficiency.md](mcp-context-efficiency.md) (partly superseded, see
below), [subagent-context-isolation.md](subagent-context-isolation.md),
[skill-bound-task-execution.md](skill-bound-task-execution.md),
the Claude Code plugin plan (not yet published)

Long GAIA runs fail when the conversation outgrows the model's context window, and today
some of those failures lose a turn silently or show a raw backend error. The main cause is
not long conversations: GAIA sends ~20K tokens of fixed instructions and tool descriptions
on every call, most of a 32K window (the NPU default and GAIA's smallest, so it sets the
design). This plan first fixes the worst of 12 verified overflow bugs (epic #5070) before
the next release candidate. It then halves the fixed load, budgets every call, and adds
**one** reducer that moves old tool results behind handles when the next call would not
fit. Single oversized items are handled separately. When nothing fits, the run rolls over
automatically into a fresh context, at most twice, and then stops loudly with a resumable
state note. Anything more elaborate is built only if an experiment shows it beats this.

## Background you need

- **Agent turn.** One user message runs a loop. Each step sends the model one request:
  system prompt + tool schemas (JSON descriptions of the ~95 tools) + conversation
  history + earlier tool results. The model answers with text or tool calls; GAIA runs
  the tools, appends their results, and calls again. Median 1 tool call per turn; 90% of
  turns (p90) make 2 or fewer.
- **Step boundary.** The point after a step's tool results are appended and before the
  next model call. Nothing is half-applied there.
- **Context window.** The most tokens one request may hold, input plus the reply. GAIA
  loads Gemma-4-E4B at 64K tokens and NPU models at 32K. A request over the window is
  rejected outright; nothing is truncated for you.
- **Prefill.** Before its first word the model reads the whole request, slower as the
  prompt grows. The one published Strix Halo measurement (Gemma-4-E4B,
  [gaia-agent-latency.md](gaia-agent-latency.md)) averaged 387 tok/s, ~2.6 s per 1K, over
  a 23K prompt. An unpublished 40K run averaged 165 tok/s, which puts tokens 23K–40K at
  ~10.8 s per 1K, the rate a deep re-read pays. The shallow rate and per-model curves are
  a Phase 0 deliverable; every cost below quotes both ends.
- **KV / prompt cache.** The server keeps the work it did for the last request. If the
  next request starts with the same bytes, only the new tail is read: a cached turn
  reaches first token in 0.3–1.4 s (TTFT). Change one byte early on and everything after
  it is re-read. For Gemma-4 and Qwen3.6, assume any edit re-reads from token 0: their
  attention (sliding-window, hybrid recurrent) can only rewind to a server-saved
  checkpoint, and GAIA's flags save none at the system+tools boundary (`--swa-full`
  would let Gemma-4 rewind at a KV-memory cost; Phase 0 measures it). Hence the rule:
  keep the prefix byte-stable, only append, and edit rarely.
- **Local endpoints.** GAIA talks to Lemonade Server (default port 13305), which runs
  llama.cpp for GPU/CPU models and FLM (FastFlowLM) for NPU models. One model, one shared
  KV pool; side requests such as memory extraction use a second slot (`SIDE_SLOT`) that
  shares that pool.
- **Cloud endpoints** (Claude, Fireworks, …) cache server-side but bill per token.
- **Turn log.** The stored conversation history GAIA replays into each request (not the
  `GAIA_TURN_LOG` metrics file).

## Today's behaviour

GAIA sends ~20K fixed tokens on every call, ~14.3K (over two-thirds) of it tool JSON;
the target is ≤10K.

| Component | Tokens | Cut | Saving |
|---|---|---|---|
| Tool JSON, 95 tools | 14,288 | Frozen core set of ~25–30; the rest via `call_tool`. Merge overlapping families (web fetch ×4, file search ×4, doc query ×3). 45 tools (5.7K) went uncalled in 700 baseline turns | 7–9K |
| ChatAgent body | ~2,990 | RAG rules (650) ride with the first indexing result; drop schema-restating walkthroughs; fix the "UNSUPPORTED: Email" contradiction | ~1K |
| Skill catalog, 18 skills | 1,300 | Names only; `list_skills` / `load_skill` exist | ~0.8K |
| `gaia-voice` body | 805 | Merge into the ChatAgent prompt's identity and prohibitions sections ("WHO YOU ARE", "NEVER"; 385 tokens, overlapping) | ~0.4K |
| Memory fragments | ~640 | Keep (stable) | 0 |
| **Total** | **~20,070** | **Target ≤10K** | **~10K** |

Rows are rounded. Counted with cl100k (OpenAI's tokenizer) on `pr-4504` (Gemma's
tokenizer gives ~0.8–1.0× for English; the eval baseline's cold first prompts were
15.6–19.6K server-counted); Phase 2 re-measures on `main`. [gaia-agent-latency.md](gaia-agent-latency.md)'s
17.0K was at 66 tools and `turn_metrics.py`'s ~6.8K is the system prompt alone.

- After the 8,192-token output reserve and a ~2K safety margin, ~35K is left for history
  and results on 64K and **~2.5K on 32K**, less than one capped tool result (F ≈ 5K, §2).
  At ≤10K fixed: ~45K and ~12.5K.
- Overflow is discovered by a rejected prefill, then handled by
  `_shrink_messages_for_overflow`, which drops evidence with no retrieval handle.
- `ContextEvictor` (`context_eviction.py`) is built but off. It stubs results only in
  the *sent* list: the turn log keeps the pre-eviction copy, so the next turn replays
  results whole and the prefix breaks again. Its handles are random (`uuid4` in
  `artifacts.py`), so the same content never yields the same stub bytes, and the archive
  is in-memory with a 1 h TTL.
- Mid-session `rebuild_system_prompt` / `_apply_tool_filter` calls (skill load/unload,
  MCP connect, memory edits, document indexing, the extraction ledger registering
  `extract_document_items` mid-turn, and the opt-in per-turn tool loader
  `GAIA_DYNAMIC_TOOLS` and its `load_tools` re-filter) each force a full re-read: ~52 s
  for the ~20K fixed load, up to ~4 min for a 40K prompt.
- Side calls are not isolated: `extract_document_items` builds its chat with no
  `id_slot`, so it runs on the conversation slot and evicts the parent's cache.
- `providers/lemonade.py` drops the server's `cache_n` (tokens reused from cache) and
  `prompt_n` (tokens re-read), so local cache reuse is only estimated (opt-in
  `GAIA_TURN_LOG` prefix comparison), never server-measured. Claude caches tools and
  system only, never history; Fireworks gets no session-affinity hint.

**Verified bugs** (epic #5070). RC-first: #5059, #5058, #5060, #5064.

| Bug | Issue | What the user sees | Eval categories (must not regress) |
|---|---|---|---|
| 1 | #5058 | A turn bigger than the history budget is dropped whole, answer included | context_retention, memory, gaia_memory |
| 2 | #5059 | TUI `/query` replays the whole client transcript with no budget; long chats fail every turn | context_retention, memory, gaia_memory |
| 3 | #5060 | A failed reload before retry is swallowed; UI still says "Model reloaded" | — |
| 4 | #5061 | Cloud/Claude overflow errors are not recognised | error_recovery, gaia_resilience |
| 5 | #5062 | "Model loaded too small" skips trim, nothing reloads, the turn is lost | error_recovery, gaia_resilience |
| 6 | #5063 | Undersized-load probe checks the wrong model, ignores the trained ceiling | — |
| 7 | #5064 | Step-limit closing answer has no overflow handling, shows the raw error | error_recovery, gaia_resilience |
| 8 | #5065 | Dropped history is invisible to model and user | context_retention, memory, gaia_memory |
| 9 | #5066 | Token estimator silently falls back to chars/4.28 without its tokenizer | context_retention, memory |
| 10 | #5067 | AgentSDK sizes RAG against a hard-coded 32K, drops documents on error | rag_quality, gaia_rag |
| 11 | #5068 | History budget reserves space from stale inputs | — |
| 12 | #5069 | Overflow helpers hide their own failures at DEBUG | — |

#5061 and #5062 are fixed as typed classification plus a loud error (reload in the loop
for #5062), never a new route into `_shrink_messages_for_overflow`, which phase 5 deletes.

## Design

Five parts, in the order they act. Every constant below (caps, margins, stub depth) is
set in Phase 0 by replaying real long-run traces at 16K, 32K and 64K.

### 1. Stay small

- **Fixed load ≤10K** (from ~20.1K). Core tool set ~25–30, picked by baseline usage *and*
  capability coverage (a 59% baseline does not prove a tool unneeded). Fewer tools also
  help accuracy: in LangChain's [single-agent benchmark](https://www.langchain.com/blog/react-agent-benchmarking),
  Llama-3.3-70B passed 21% of one domain's tasks with its own tools and under 10% once
  other domains' tools were added.
- **Append-only, prefix-stable.** `tools=` never changes mid-session (measured: adding a
  tool took TTFT from 0.4 s to 17 s). Non-core tools go through one pre-declared
  `call_tool(name, args)`; their schemas arrive in the `load_skill` / `load_tools`
  result; bad args error loudly. Skill bodies, RAG rules and other conditional text are
  appended once, never rebuilt into the system prompt.
- **One per-call `ContextBudget`** against the real window: usable input `U = window −
  output reserve − margin`; variable room `D = U − fixed load` (32K: ~2.5K today, ~12.5K
  at 10K fixed). Every injector (history, result cap, RAG chunks, memory) takes from it;
  `fits()` runs before every call. Counting: last server-reported prompt tokens plus
  `max(estimate, utf8_bytes/3)` for appended text. If D < the result-cap floor F (§2)
  plus the active-context block at run start, the run stops loudly naming the fixed load.

**Runtime effect (to be measured in Phase 0).** Cutting ~10K fixed tokens saves ~25–100 s
per cold prefill or cache break (shallow to deep rate, Background); cached turns stay at
0.3–1.4 s TTFT. Uncached cloud providers save ~10K input tokens per call; Claude already
caches tools and system, so its larger saving comes from the history breakpoint (Cloud).

### 2. One reducer

- **Trigger: headroom, not a percentage.** It fires only when the next call plus one
  capped tool result would not fit in U. Percentage thresholds fail on small windows: on
  32K, even at the 10K target (D ≈ 12.5K), 60% and 75% of D are only ~1.9K apart, and one
  capped result (F ≈ 5K) crosses both at once. At today's D (~2.5K), F is larger than D.
- **When: at a step boundary**, one rewrite per event, so it can fire mid-turn. It acts on
  whole steps, after their results are appended, so no tool call is left without its
  result. This is the same point where today's evictor runs.
- **What: old tool results become stubs.** A stub names a handle that is a hash of the
  content, so the same result always yields the same bytes. The stub form is written into
  the turn log, so later turns replay the reduced history and the prefix stays stable
  after the one edit (fixing today's ContextEvictor gap). The same rewrite drops the
  reasoning of the steps it stubs. Today GAIA resends every step's reasoning within a
  turn; earlier turns' is dropped unless `resend_reasoning_across_requests` is on.
- **Archive:** originals go to a session-scoped disk archive (`~/.gaia/runs/<run_id>/`),
  fetched by `read_tool_output(handle)`; a missing original raises, naming the handle.
- **Result cap** = min(room left in U after the current prompt, ~⅓ of D), never below a
  floor F (~5K, today's NPU budget of 20,000 chars; set in Phase 0). Anything bigger goes
  to extraction (§4).
- **Cost:** one full re-read per event (worst case from token 0), priced in Phase 0 at
  both ends of the prefill range; then turns are cached again.

If a reduction still cannot make the next call fit, the run goes to the loud stop (§5).
Avoided: per-turn sliding windows (a re-read every turn), per-turn tool swapping, and
llama.cpp context shift (silently discards the oldest tokens, including the task).

### 3. Active-context block

A reduction can stub out the `load_skill` result that delivered a schema. After every
reduction GAIA appends a short block rebuilt from the loaded skills and `call_tool`
schemas, budgeted as its own capped term, so the model never calls a tool it lost.

### 4. Large single items

One item too big for the cap is a different problem from a long history.

- **Query-focused extraction** for large unstructured outputs (logs, test/build and shell
  output, web pages, document text, transcripts) **and** oversized user pastes or
  attachments. A side call reads the item against the sub-goal (the latest user message
  plus the triggering call's arguments) and returns verbatim lines with line refs; the
  full item is stored behind a handle. Every quoted line must exist at its cited line, or
  the result falls back to today's condensed form (cut to the cap: whole list items
  dropped from the end, or head and tail of text around a `[truncated]` marker) with a
  visible "extraction failed: <reason>" note, logged at WARNING. **Never** applied to
  source code the agent may edit, diffs, configs, structured records, or a
  `read_tool_output` fetch.
- **Fresh-context sub-tasks** for bulky read-heavy steps (multi-source research,
  large-document search). The model starts one through the `delegate` tool in
  [subagent-context-isolation.md](subagent-context-isolation.md), not the reducer, and
  the parent sees only the ≤1–2K result. If that plan's cold-start-prompt eval fails,
  extraction alone covers large items.
- **Side slot, always.** Every side call runs on `SIDE_SLOT`. On local endpoints it runs
  only after checking that both contexts fit the shared KV pool; otherwise the main
  context gets the condensed output plus the handle. `extract_document_items` moves to
  the side slot in the same change and is declared up front (core or via `call_tool`)
  instead of being registered mid-turn.

### 5. Loud stop with handoff

When a reduction can't make the next call fit, GAIA writes a state note **in code, not by
the model**: goal, files touched, commands run, completed effects (from the per-turn
execution record `verification.py` already reads for changed paths), and any in-flight
call marked "outcome unknown". The note is written atomically to
`~/.gaia/runs/<run_id>/task_state.json`.

- **Rollover (automatic, same run, at most 2):** the run continues in a fresh context
  seeded from the note. The note is bounded summarization, and the user is told so.
- **Exhaustion:** if the next call still doesn't fit after the second rollover, the run
  ends with `context_exhausted`, carrying the latest note.
- **Resume (the user starts it, new run):** seeded from `task_state.json`. Unknown-outcome
  calls go through the existing tool confirmation prompt before re-running. Resume does
  **not** promise to never repeat a side effect; it promises to ask.

### What the user sees

| Surface | Reduction | Extraction | Rollover / exhausted |
|---|---|---|---|
| Agent UI | `status` `context_reduced`: "Moved 3 older tool results out of the prompt; still readable" | Step shows "Extracted 14 relevant lines from 2,300" | Error card: what did not fit, partial answer, state note, Resume button |
| TUI (daemon `/query`) | Status line | Status line | Error naming the oversized part and the resume id |
| `gaia_task` (Claude Code plugin) | `turn_metrics` | `turn_metrics` | `failed` / `context_exhausted`, state path |
| CLI | Warning line | Info line | Error exit, state file path |

`/query` keeps one `final` or `error` per turn. Exhaustion is an `error` with additive
`{code: "context_exhausted", oversized_part, state_path, resume_id}`; reduction is a new
`status` value; both behind a version bump of the `/query` event-stream (SSE) schema.

### Cloud

- **Claude:** a rolling cache breakpoint on the newest stable message caches history too.
- **Fireworks:** the run id as a session-affinity hint lands turns on a warm replica.

## Interfaces and components

| Component | Where | Contract |
|---|---|---|
| `ContextBudget` | new `agents/base/context_budget.py` | `resolve_window(agent)`, `U`, `D`, `take(kind, tokens)`, `remaining()`, `fits(messages, tools) -> bool`. Undeclared cloud window raises naming the model |
| `call_tool` | `agents/base/tools.py` | `call_tool(name, args)`; validates against the schema delivered earlier; unknown name or bad args error |
| `Reducer` | replaces policy in `context_eviction.py` | `reduce(history, budget) -> history`; step-boundary only; whole steps; content-hash handles; writes stubbed form to the turn log |
| Run archive | `~/.gaia/runs/<run_id>/` | Originals + `task_state.json`; replaces the in-memory 1 h store; serves MCP handles; missing handle raises |
| `ActiveContext` | new `agents/base/active_context.py` | `build(agent, budget) -> message`; capped term |
| `ResultExtractor` | new `agents/base/result_extractor.py` | `extract(raw, sub_goal, kind) -> Extract(lines, refs, handle)`; side slot + pool check; raises `ExtractionFailed`, which the tool-result path catches and falls back as in §4 |
| `ContextExhausted` | `agents/base/errors.py` | Typed error carrying `oversized_part`, `state_path`, `resume_id` |
| `turn_metrics` | `turn_metrics.py` (exists) | Always records fixed load, budget line items, `cache_n`/`prompt_n`, reductions, extractions, rollovers |

## Relationship to mcp-context-efficiency.md

That plan owns MCP through WS1 (tool disclosure), WS2 (result handles) and WS3
(privacy). This plan supersedes two parts, edited in the phase 2 PR:

- **WS1's per-turn tool selection** gives way to a frozen `tools=` list with non-core
  tools (MCP or in-core) via `call_tool`. Discovery stays WS1's `load_tools`. This
  retires the in-core per-turn loader (`GAIA_DYNAMIC_TOOLS` / the UI `dynamic_tools`
  setting); `load_tools` returns schemas for `call_tool` and no longer re-filters `tools=`.
- **WS2's session-scoped `gaia://artifact/...` store** gives way to the run archive, one
  handle format. WS3 privacy rules apply to the on-disk copy.

## Test-driven plan

**Red first.** Phase 0 lands a failing test per bug #5058–#5069, each
`xfail(strict=True, reason="#50xx")` so the fix must remove the marker. It rewrites the
two tests that pin #5058's current behaviour in `tests/unit/agents/test_model_history.py`
to assert question and answer survive with tool content stubbed.

**Prefix stability (byte-level).** After a reduction, the next request must equal the
previous request plus the new message, byte for byte, even after a process restart.
Phase 3 is gated on server-reported cached tokens (`cache_n`), not the mock alone.

**Context-overflow stress suite** (`tests/stress/`), two tiers:

- **CI tier, deterministic.** A mock LLM counts with the real tokenizer and chat
  template and applies a ±20% skew, so "never rejected" is not just the budget checking
  itself. It rejects over a configurable window (8K, 16K) with real llama.cpp and cloud
  400 bodies, and drives 40+ tool calls, oversized results and pastes, long history, a
  large replayed TUI transcript through `/query`, mid-run skill and MCP loads. Asserts:
  no silent loss (every moved item has a stub, handle or note); budget never exceeded;
  system prompt and `tools=` byte-equal; extraction never touches code, diffs or
  configs and its quotes match; skills/schemas present after every reduction; an
  exhausted run returns partial work and a valid state file.
- **Real-hardware tier** (`require_lemonade`). Same scenarios at a 16K window, plus:
  two interleaved sessions; an oversized first message; killing the run mid-tool and
  showing resume asks before re-running the unknown-outcome call. Records server
  tokens, `cache_n`, reloads and re-prefill seconds per event.
- **Before baseline.** Both tiers run on current `main` first; reports committed
  unedited under `tests/fixtures/context_stress_baseline/` with the commit SHA.

**Unit tests** (mocked LLM; assert the outgoing request's shape): fixed load ≤10K with
the core set listed; skill load/unload, MCP connect, memory forget/update, first index
and `load_tools` leave system prompt and `tools=` byte-equal; budget math 16K–256K,
local and cloud; content-hash handles stable across processes; missing original raises.

**Eval and the three-arm decision.** New scenario **`long_run_context`**: 40 steps under
a 32K cap, read ~12 files of ~4K tokens, run a check twice, fix two planted bugs, answer
a question only file 2 answers. A 16K variant must end `context_exhausted` with a valid
state file; a resume variant must finish, asking before any unknown-outcome re-run.

| Arm | What runs |
|---|---|
| (a) | Fixed-load cut only |
| (b) | Cut + reducer + loud stop (this design) |
| (c) | Eval-only ladder: evict old results at 60% of D, trim oldest history at 75%, model-written task-state summary |
| control | Uncapped window |

~5 seeds × 2 runs per arm on Gemma-4-E4B. Arm (c) ships only if it beats (b) by more
than run-to-run noise. Gates on E4B for (b): planted-fact retrieval through stubs;
rollover handoff A/B (model-written findings vs code-only note); skill/schema presence
after every reduction. Before phase 2 the flagship baseline is re-captured with 2 runs:
today's is one run at 59%, so differences under ~9 points are noise. Every phase runs
`eval_flagship.yml` on the Strix Halo pool; report PASS→FAIL and FAIL→PASS separately.

## Phases

Effort: traditional engineer-weeks, and agentic-coding days (an engineer with a coding
agent, ~10× faster). Calendar items that don't shrink are listed separately.

| Phase | Scope | Exit criteria | Traditional | Agentic |
|---|---|---|---|---|
| 0 Measure + tests first | Prefill time vs context length and cost of an edit at depth per model (default flags, `--swa-full`, a seeded system+tools boundary checkpoint; FLM NPU separately); capture `cache_n`/`prompt_n`; replay traces to derive constants; red tests; stress suite; `long_run_context`; before baseline | Measured curves and edit costs committed; every cost in this plan restated from them; 12 strict-xfail tests; scenario fails on `main` | 4 wk | 2 d |
| 1 Bugs | #5058–#5069, one PR each, RC candidates first | Each xfail removed and green; listed eval categories no worse | 3 wk | 1.5 d |
| 2 Stay small | Re-measure on `main`; core set + `call_tool`; appended skills/RAG rules; prefix breakers fixed (incl. mid-turn extraction-tool registration and the per-turn tool loader); mcp plan WS1/WS2 edits | Fixed load ≤10K by test; prefix byte-stable; every category no worse than the 2-run baseline | 4 wk | 2 d |
| 3 Budget + reducer | `ContextBudget`, `fits()`, reducer, hash handles, stubbed turn log, run archive, active-context block, `/query` budget, in-loop reload | Byte-level prefix test passes with server `cache_n`; budget never exceeded; one re-prefill per event in HW tier | 3 wk | 1.5 d |
| 4 Large items | `ResultExtractor` for results and pastes, side-slot + pool check, `extract_document_items` to side slot, sub-tasks | No extraction of code/diffs/configs; quotes verified; no parent eviction in HW tier | 3.5 wk | 2 d |
| 5 Loud stop | `ContextExhausted`, code-written state note, atomic write, resume as new run, ≤2 rollovers, delete `_shrink_messages_for_overflow`, `/query` SSE schema bump, plugin, docs | 16K variant ends loud with state; kill-mid-tool resume asks before re-run | 2.5 wk | 1.5 d |
| 6 Decide + cloud | Three-arm harness, arm (c) prototype, E4B gates; Claude rolling breakpoint; Fireworks affinity | Arm results reported; (b) passes E4B gates; cloud cached-token share recorded | 2.5 wk | 1.5 d |
| **Total** | | | **~22.5 wk** | **~12 d** |

Conditional: productionising arm (c) if it wins, ~2 wk / ~1 d, not in the total. Each
phase updates the docs it touches: gaia npm README/SPEC/SKILL/CHANGELOG +
`openapi.gaia.json` (`/query`), `docs/sdk/sdks/agent-ui.mdx` (SSE), `docs/reference/cli.mdx`.

**Calendar time (not reduced by agentic coding):**

- Phase 0 hardware measurement (3 flag settings on E4B, 2 on Qwen3.6 since `--swa-full`
  does not apply to it, plus FLM on NPU, at several depths) and trace replays: ~3–4 days.
  Flagship baseline re-capture: ~2–3 days.
- Three-arm experiment (4 arms × ~5 seeds × 2 runs, serial on one backend): ~3–5 days.
- Strix Halo eval gates for phases 1–5, ~1–2 days each with a re-run day: ~5–10 days.
  Real-hardware stress tier: ~2 days across phases 3–5.
- Review: ~2–3 days per PR, ~6 sequential PRs on the critical path (the 12 bug PRs
  review in parallel): ~12–18 days.
- **Total calendar: ~27–42 working days (~5.5–8.5 weeks)** on top of the coding.

## Risks

- **Prefill cost at depth is unmeasured.** At the deep rate (Background), one reduction
  on a full 64K window could cost minutes; if Phase 0 confirms it, add a seeded boundary
  checkpoint (both models) or `--swa-full` (E4B only).
- **Tool cuts may drop a capability** the 59% baseline never exercised, and `call_tool`
  may cost a 4B model accuracy (fallback: declare all tools and accept the fixed cost).
- **Shared KV pool.** When side calls don't fit, condensed output is weaker evidence.
- **Tokenizer drift:** cl100k can be far off on non-English scripts; the budget anchors
  on server counts, and the CI mock's ±20% skew tests that margin.

## Decision requested

1. **Fixed-load budget ≤10K**: frozen core set of ~25–30 tools plus `call_tool`.
2. **One headroom-triggered reducer** at step boundaries (between model calls, including
   mid-turn), with content-hash handles, a stubbed turn log and a run archive, accepting
   one visible re-prefill per event.
3. **Change CLAUDE.md** ("Key architectural decisions" list) from

   > - No context compaction — memory + RAG handles long conversations

   to

   > - No rolling context compaction — memory + RAG remain the retrieval path for long
   >   conversations. Agent runs stay append-only, move old tool results behind
   >   retrievable handles, and at each rollover (at most 2 per run) and at exhaustion
   >   write one bounded, code-written state note. LLM summarization only if an experiment
   >   proves it beats this.

   Rejecting it drops rollover and goes straight from reduction to `context_exhausted`.
4. **Replace the reactive shrink** with `context_exhausted` plus resume as a new run,
   and wire the history budget into `/query`.
5. **Measure before building tiers:** Phase 0 measurements and the three-arm result
   decide whether anything beyond arm (b) ships.

## Open decisions

1. Reducer stub depth: keep the newest K steps whole, with K derived per window from
   the trace replays, or one K for all?
2. Handoff note: code-only, or code plus ≤500 model-written findings (E4B A/B decides)?
3. State note across Agent UI chat turns, or (proposed) within one run only?
4. Recall of dropped turns: a new `recall_turn` tool, or through memory?
5. Run-archive retention (the resume window) and its disk cap.
6. Also cap the TUI's `/query` `context` transcript (bug #5059), or is the server trim enough?
7. Extraction for cloud endpoints: on by default (no shared pool), or only past the cap?
