# Sub-agents as a context-isolation primitive

> **Superseded by [`skill-bound-task-execution.md`](skill-bound-task-execution.md).** That
> document reframes the problem around a work plane (async jobs + artifact handles) and
> reaches the same "don't build sub-agent delegation" conclusion for stronger reasons.
>
> **One claim below is corrected there:** Lemonade *does* support concurrent multi-model,
> multi-device residency (`max_loaded_models` defaults to 1 — a config default, not a
> hardware limit), so "one resident model, no parallelism" described GAIA's own single-slot
> broker, not a real ceiling.
>
> Still accurate and not repeated in the newer doc: the context arithmetic below, the
> empirical tool-registry isolation test, and the licensing finding.

**Status:** scoping / recommendation. Nothing built.
**Relationship to #674:** narrower and different — see [What this is not](#what-this-is-not).

## Recommendation

**Don't build sub-agents yet. Build the cheap fix first, then run one eval that decides whether sub-agents are worth it at all.**

The problem is real and measured: a retrieval-heavy turn runs out of context after roughly **five large tool results**, and buying a bigger GPU does not raise that number. But sub-agents are the most expensive of three fixes for it, and their value depends entirely on a question nobody has tested — whether a 4B model can write a good cold-start prompt for a child that sees none of the conversation. If it can't, delegation is strictly worse than not delegating.

Order of work:

1. **Ship #1973 (artifact store) regardless.** It caps a single oversized payload. Orthogonal to everything below, already scoped, already has a design PR.
2. **Make the existing overflow shrink proactive** — a few days, reuses shipped code, captures most of the benefit. Details in [Alternative C](#alternative-c--proactive-result-eviction).
3. **Time-box a `delegate` prototype and run the cold-start-prompt eval.** If Gemma-4-E4B writes usable child prompts, the design in [If we build it](#if-we-build-it) is sound and the plumbing already exists. If it doesn't, close this and keep the shrink.

Sub-agents should never become an architecture here — only an opt-in tool the flagship may call. That distinction is the whole reason this isn't #674.

## The problem, measured

GAIA pins context per device and has **no compaction by policy** (`CLAUDE.md`). So the window is a hard budget, and the only lever is what goes into it.

The flagship's composed system prompt measures **28,957 chars / 6,757 tokens** (`src/gaia/agents/base/turn_metrics.py:52-53` — measured against the real prompt, not estimated). Large tool results are truncated to a target that *scales with the window* (`src/gaia/llm/lemonade_client.py:191-192`): 20,000 chars on NPU, 40,000 on GPU.

That scaling is the trap:

| | NPU (32,768) | GPU (65,536) |
|---|---|---|
| Fixed prompt | 6,757 tok (21%) | 6,757 tok (10%) |
| Left for history | 26,011 tok | 58,779 tok |
| One max-size tool result | 4,673 tok | 9,346 tok |
| **Results before overflow** | **~5** | **~6** |

Because the truncation budget scales with `ctx_size` by design, **doubling the context window buys about one extra tool result.** Hardware does not fix this.

Two honest caveats: these are *max-size* results, and typical results are smaller — this is a ceiling for retrieval-heavy work, not a typical chat. And assistant reasoning also accumulates, so the real number is a little worse. The workloads that hit it are exactly the ones skills like `research-report`, `data-explore`, and `document-brief` describe.

**What happens at the ceiling is the actual bug.** The recovery path (`_shrink_messages_for_overflow`, `agent.py:3934`) stubs out *every* older tool result, keeping only the latest, and retries. So a research turn that read five sources silently continues with one. The user sees a confident answer built on 20% of the evidence and blames the model. That is a quality failure disguised as a successful turn — worse than a loud error, and it violates the spirit of the no-silent-fallbacks rule.

## Why most of the Claude Code value doesn't transfer

Three GAIA constraints remove the majority of the upside. These are not objections to the design; they are limits on how much it can ever pay back.

- **One resident `(model, ctx)` pair.** The child runs the same Gemma-4-E4B. No "cheap model for grunt work" — Claude Code's single biggest lever is unavailable, and opencode's per-agent `model` field would trigger an eviction and a ~100s cold reload.
- **Lemonade is single-tenant per model slot.** No parallel fan-out. The canonical Claude Code pattern — twenty explorers at once — is off the table. Delegation is serial, so it buys context but never wall-clock.
- **KV cache.** GAIA deliberately keeps the prompt prefix stable so the backend's KV cache survives — `llama.cpp reuses the KV cache only up to the first differing token` (`agent.py:1005-1009`), which is why volatile fragments are pinned to the tail. A child whose prompt diverges early pays a cold prefill. Every delegation costs one.

What survives is narrow but real: **the parent's transcript never sees the child's intermediate junk.** On a window where five results is the ceiling, that is worth something.

## What this is not

#674 was closed 2026-09-02 with "sub-agent orchestration is not the architecture," and that call was right. This proposal is a different object:

| | #674 (closed) | This |
|---|---|---|
| Shape | Permanent mesh of specialist agents, 0.6B router, all-to-all comms | One tool the flagship may call |
| Lifetime | Architectural, always on | One task, then discarded |
| Problem | Prompt size + tool-selection accuracy | Transcript accumulation |
| Status of that problem | **Solved** by #3008 (12,164 → 4,654 tok/call) | Unsolved; recovery is lossy |
| User-visible | Yes — specialists, narration | No — one tool call |

If this ships, the flagship stays the flagship, one agent, one model, one prompt. Nothing is decomposed. If the design starts growing named specialist agents, a routing layer, or agent-to-agent messaging, it has become #674 and should be closed again.

## If we build it

### Feasibility: the plumbing already exists

The extension points are in place, and one of them was built for exactly this:

```python
# hub/agents/chat/python/gaia_agent_chat/agent.py:1958
# Snapshot: freeze this agent's tool set so mutations by other agents
# in the same process do not leak in.
self._snapshot_tools()
```

I verified the isolation empirically rather than trusting the comment. Two live agent instances in one process, both registering a same-named closure-bound tool: the parent keeps its own binding, because `@tool` *replaces* the whole registry entry rather than mutating it, so the parent's shallow copy still points at the parent's function. Parent and child do not cross-bind.

One residual hazard: `_tools_registry` falls back to the process-global dict when `_instance_tools is None` (`agent.py:1181-1183`). Any agent that never snapshots *would* pick up the child's bindings. Narrow, and a test pins it.

Also available: `_active_tool_filter` / `_apply_tool_filter` for the child's tool subset, `_compose_system_prompt` for its prompt, and `default_max_steps()` for its step cap. The embedder/chat-model eviction risk is retired — Lemonade holds both simultaneously (#1544, `src/gaia/rag/sdk.py:459-468`).

`process_query` mutates instance state (`_current_query`, `_turn_recorder`, `_tool_reported_usage`) and is not re-entrant, so the child **must** be a distinct instance. That is a constraint, not a blocker.

### Shape

One tool. Not a config format, not a `.gaia/agents/` directory — GAIA already has `SKILL.md` for scoped prompts and tools, and shipping a second authoring format for the same job would be a mistake.

```python
@tool
def delegate(task: str, tools: str = "") -> str:
    """Run a self-contained sub-task in a fresh context and return only its answer.

    Use when a sub-task will produce many intermediate results you don't need —
    searching a large codebase, reading several documents, checking many pages.
    The child sees NONE of this conversation, so `task` must be fully
    self-contained: state the goal, the constraints, and exactly what to return.
    """
```

Design rules, each earning its place:

- **Child inherits the parent's model.** Not configurable. Anything else evicts.
- **Prompt built as parent-prefix + child-suffix.** The child's persona and rules stay byte-identical to the parent's; only the trailing tool block differs. `_compose_system_prompt` **already enforces exactly this ordering** — static head, volatile tail, filtered tool block last, explicitly to protect the KV prefix (`agent.py:995-1013`). A child that differs only in its tool filter therefore shares the entire cached static head, turning a full cold prefill into a partial one. This is the single strongest feasibility signal in the codebase: the discipline the design needs is already load-bearing for a shipped feature.
- **Deny-only inheritance**, lifted from opencode (`subagent-permissions.ts`): the child inherits the parent's *denials* and path scope, never its grants. A permissive parent cannot escalate a child; a restrictive one cannot be escaped. One pure function, trivially unit-tested.
- **No nesting.** `delegate` is excluded from the child's tool set, full stop. opencode defaults to depth 1 and Claude Code to 3; on a single-tenant serial backend, depth 1 is the only defensible number.
- **Only the final text crosses back.** Tool calls, file contents, and reasoning stay in the child. This is the entire point.
- **Failure is loud.** A child that errors or hits its step cap returns an actionable error naming the cause — never a partial answer dressed as a complete one, which is the failure mode we're trying to remove.
- **Serial and bounded.** One child at a time; child step cap well below `DEFAULT_MAX_STEPS` (50). A child that needs 50 steps was mis-delegated.

### The eval that decides it

The design above is sound. Whether the *feature* works is one empirical question:

> Can Gemma-4-E4B write a `task` string good enough for a cold-start child to succeed?

opencode's own `task` tool prompt warns the parent to give a highly detailed instruction *because the subagent starts cold* — and that warning is aimed at a frontier model. A 4B model summarizing enough context into one string is a genuinely hard generative task, and it is cheap to test.

Per `CLAUDE.md`, tool-schema and prompt changes are an LLM-affecting surface, so this needs `gaia eval agent` against the committed baseline regardless — run serially, one eval process at a time.

Pass condition, decided before running: on retrieval-heavy scenarios, delegation must **improve** answer quality against the baseline, not merely avoid overflow. If it comes out neutral, the added latency and complexity aren't paid for and this closes.

## Alternatives compared

### Alternative B — #1973 artifact store

Size-gates a single large tool result into a handle plus preview. **Ship regardless.** It solves *one oversized payload*; sub-agents solve *many accumulated payloads*. Different axes, both real, no overlap.

### Alternative C — proactive result eviction

The overflow recovery at `agent.py:3934` already stubs old tool results and retries. Today it fires *reactively*, after the backend has already rejected the request, and bluntly — everything but the latest result.

Make it proactive and selective: evict results the model has already consumed, before the ceiling, keeping a one-line summary and (with B) an artifact handle to re-read on demand.

- **Cost:** days. Extends shipped, tested code.
- **Captures:** most of the benefit for the common case.
- **Tension:** it is compaction, which `CLAUDE.md` forbids. But the reactive path *already does this*, worse and later. The policy is defending against silently rewriting history; this is worth an explicit decision rather than an assumption either way.

### Comparison

| | A: sub-agents | B: artifact store | C: proactive eviction |
|---|---|---|---|
| Solves | Accumulated junk | One huge payload | Accumulated junk |
| Cost | Weeks + eval risk | Scoped (#1973) | Days |
| Latency | +1 prefill per delegation | Neutral | Neutral |
| Risk | 4B can't write child prompts | Low | Needs a compaction-policy call |
| Verdict | **Prototype, gate on eval** | **Ship** | **Do first** |

## Licensing

opencode (`sst/opencode`, now `anomalyco/opencode`) is stock **MIT** — verified in `LICENSE` and the `package.json` license field. Not copyleft, not source-available, no non-compete. Reading it for reference is unrestricted; re-implementing the design in Python carries no obligation. If any non-trivial TypeScript is lifted, the MIT notice must ship with it — but nothing here requires that.

The ideas borrowed above (deny-only inheritance, final-text-only return, depth capping) are architecture, not code.

## Open questions

- Does the compaction ban extend to Alternative C, or was it aimed only at summarizing *conversation* turns? This blocks the cheapest fix and is a one-line decision.
- Should `delegate` be exposed to skills, or reserved for the flagship's own turn planning?
- Is there production telemetry appetite? `GAIA_TURN_LOG` already records per-turn prefill and history size (`turn_metrics.py:162-172`) but is dev-only and off by default — so nobody knows how often overflow actually fires. That number would settle this argument better than any of the reasoning above.
