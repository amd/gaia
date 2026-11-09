# MCP Context Efficiency — Progressive Disclosure & Result Handling

**Status:** Draft / not started
**Related issues:** #688 (tool loader parent), #1449–#1451 (loader parts), #976/#1005 (MCP connectors + per-agent activation)
**Source motivation:** [Anthropic — Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)

## TL;DR

The Anthropic article bundles two ideas: (1) **progressive disclosure** of tool
definitions, and (2) **code execution** as the calling convention (model writes
sandboxed code that composes tool APIs). GAIA already ships (1) for in-core tools
via the dynamic tool loader (`tool_loader.py`), and correctly avoids (2) — running
model-authored code on a user's machine is a large attack surface and a poor fit
for 4B–35B local models that author code unreliably.

This plan closes the remaining gaps that live **on the GAIA side of that line**:

- **WS1 — MCP tools bypass the loader.** `_register_mcp_tools` eagerly registers
  every tool from every connected server into the prompt on every turn. The
  magnitude depends on the render path (see note below) and is worst for
  native tool-calling models, but the *slope* is the real problem: every
  connected server taxes every turn forever. Measure before quoting a figure —
  the existing `tool-loader.mdx` explicitly warns against fixed token estimates.

> **Render-path note.** GAIA has two tool-rendering paths and they cost very
> differently. Native tool-calling models get **full JSON schemas** per tool
> (`_build_openai_tool_schemas`) — close to the article's "~150 tokens per
> definition." Non-native models get **one compact line** per tool
> (`- name(params): desc`, `agent.py:770`) — far cheaper. WS1's token win is
> therefore largest for native tool-calling models; justify on TTFT + slope, not
> a single headline number.
- **WS2 — Tool results enter context verbatim.** Large MCP payloads (a Drive
  transcript, a big query result) are dumped whole into the model's context by the
  registration wrapper, then re-sent when passed to the next tool.
- **WS3 — No privacy pass-through (optional/Phase 3).** Data flowing connector→
  connector always transits the model context, even when the model never needs to
  read it.

**Explicitly out of scope:** model-authored code execution, a code sandbox, or
presenting MCP servers as a code-API filesystem. We adopt the *outcomes*
(fewer tokens, data-out-of-context) using GAIA's existing embedding-based
selection and tool-wrapper layers — not the mechanism.

---

## Current state (what exists today)

### The loader (WS1 foundation)
- `src/gaia/agents/base/tool_loader.py` — `ToolLoader.select(query, registry, *, skill_tools=)`
  returns a sorted tool-name subset, or `None` (fail-safe → full registry).
  Scores **every** registry entry by cosine similarity of the query against
  `"{name}: {description}"` (`tool_loader.py:271-278`), so MCP tools are *already
  scoreable* once the loader runs.
- Wiring lives only in ChatAgent (chat wheel,
  `hub/agents/chat/python/gaia_agent_chat/agent.py`): `_maybe_build_tool_loader`
  (`gaia_agent_chat/agent.py:455`) gates it to the **`doc` profile** with the
  toggle on, and `_dynamic_tools_active` (`gaia_agent_chat/agent.py:520`)
  additionally requires an active memory store.
- Rendering is gated in the base agent: `_format_tools_for_prompt(filter_to=)`
  (`agent.py:737`) and `_openai_tools`/`_build_openai_tool_schemas` read
  `_active_tool_filter`. Execution always uses the full registry.
- **KV-cache invariant** (`agent.py:621-647`): loaded set is monotonic + sorted,
  tool block rendered last, so non-expansion turns serialize byte-identically.
- **Coverage enforcement** — narrower than it first appears (corrected after
  code review):
  - `validate_registry` (`tool_loader.py:200-222`) only checks the
    **config→registry** direction: it raises if a CORE/bundle *name* is absent
    from the registry. An MCP tool that is *in* the registry but *not* in any
    bundle does **not** trip it. So `validate_registry` needs **no** MCP change.
  - The "every registry tool must be covered" rule is enforced by **one CI test**,
    `tests/unit/test_chat_tool_bundles.py::test_core_and_bundles_cover_doc_registry_exactly`
    (`uncovered` assertion, lines 46-54). It builds `build_doc_agent_skeleton`,
    which registers **no** MCP tools, so it doesn't see them today.
  - MCP tools are discovered at **runtime** with unknown names, so they can never
    appear in static `DOC_BUNDLES`/`DOC_CORE_TOOLS`. The only change needed is to
    the CI test *if/when* MCP tools ever enter the registry it inspects: subtract
    `{n for n in registry if registry[n].get("_mcp_server")}` before the
    exact-match comparison. Static-tool drift keeps the strict invariant.

### MCP registration (WS1/WS2 touch point)
- `MCPClientMixin._register_mcp_tools` (`src/gaia/mcp/mixin.py:328`) registers each
  tool into the **global** `_TOOL_REGISTRY` with name `mcp_<prefix>_<tool>`,
  description `[MCP:<prefix>] <desc>`, a params dict, and an `enhanced_wrapper`.
- `MCPTool.to_gaia_format` (`src/gaia/mcp/client/mcp_client.py:154`) builds that
  entry; descriptions and per-param descriptions are preserved from the server
  schema — usable embedding text for WS1.
- The `enhanced_wrapper` (`mixin.py:353-380`) wraps the **full** result dict as
  `data` with an instruction to summarize — the WS2 problem: no size guard, the
  entire payload lands in context.
- `get_mcp_client_system_prompt` (`mixin.py:76`) only teaches tool-name discipline;
  fires with ≥2 MCP tools.

### Reusable infrastructure
- Scratchpad tables backend (`src/gaia/scratchpad/`) — session-scoped SQL storage,
  a natural home for WS2 artifacts.
- Governance layer (`src/gaia/governance/`) — hook point for WS3 PII detection.
- Loader escape hatch `load_tools` (Part 2) — model-driven recovery when a
  semantic match misses; WS1 reuses this instead of inventing a new discovery tool.

---

## Workstream 1 — Route MCP tools through the loader (progressive disclosure)

**Goal:** prompt cost scales with MCP tools *used this turn*, not MCP tools
*connected*. Target: on a session with ≥3 connected servers, first-turn MCP tool
tokens drop ≥60% vs. eager registration, with no recall regression.

### Design

1. **Tag MCP tools as dynamically-covered.** MCP registry entries already carry
   `_mcp_server`. Treat any entry with that key as an **implicit bundle member**
   exempt from static coverage: it participates in semantic scoring but is not
   required to appear in `DOC_BUNDLES`, and is never in CORE (so it only surfaces
   on a semantic/skill match or via `load_tools`). This is exactly the desired
   progressive-disclosure behavior.

2. **Coverage enforcement — no `validate_registry` change needed.** Per the
   corrected analysis above, MCP tools uncovered by bundles do not trip
   `validate_registry`. Only touch the CI coverage test, and only if MCP tools
   ever enter the registry it inspects (subtract `_mcp_server`-tagged names there).
   Static drift keeps the strict invariant.

3. **Generalize loader activation beyond the doc profile — with an MCP-only
   gating mode (critical correction).** Today the loader is `doc`-only, where
   `DOC_CORE_TOOLS ∪ DOC_BUNDLES` cover the *entire* registry. If we simply turn
   the loader on for a non-doc agent that has MCP tools, the loader would also
   start gating that agent's **static** tools — but those agents define no
   CORE/bundles, so every static tool collapses to semantic-match-only and native
   tool recall craters. That is a regression, not a win.
   - **Fix:** add an **MCP-only gating mode**. When the loader is activated *by
     MCP presence* (rather than the doc-profile toggle), the effective CORE is
     "all non-MCP registry tools" (static tools always pass through), and only
     `_mcp_server`-tagged tools are subject to selection/eviction. The doc-profile
     path is unchanged.
   - Build the loader when `_mcp_manager` has ≥1 connected server *and* an embedder
     is available, independent of `prompt_profile`. Additional trigger, not a
     replacement for the doc toggle.
   - `_dynamic_tools_active` currently also requires `_memory_store`. Memory gates
     only the SKILL signal; the loader runs fine on CORE+SEMANTIC without it.
     Decouple **only** under the MCP-present condition, to avoid perturbing the
     doc-profile assumptions.

4. **Compact "unloaded MCP tools" menu — placed in the volatile tail, NOT the
   mixin head (critical correction).** Mirror the article's name + truncated
   (~60 char) menu so unloaded tool *names* stay discoverable at ~1 line each.
   **Placement matters:** mixin fragments compose at the **front** of the prompt
   (`_compose_system_prompt`, `agent.py:611-614`) while the tool block is placed
   **last** to keep the KV prefix warm (`agent.py:621-647`). A per-turn-changing
   menu in `get_mcp_client_system_prompt` would sit at the front and invalidate
   the cache on every expansion — defeating the loader. Render the menu **inside
   the tools block region** (adjacent to the loaded-tool list, in the volatile
   tail) so it moves with the filter, not against the cache.
   - The instruction points only at **`load_tools`** (the Part 2 escape hatch).
     `search_tools` does not exist in GAIA — do not reference it.
   - **This menu is the only discovery channel for native tool-calling models:**
     an unloaded MCP tool is absent from `_openai_tools`, so the model literally
     cannot emit it and must `load_tools` first. Menu correctness + placement are
     therefore load-bearing, not polish.
   - Cap the menu (e.g. 40 tools) and `log()` truncation — no silent cap.

5. **Wire embeddings.** MCP tool embedding text = `"{name}: {description}"` using
   the existing `embed_batch_fn`. The content-keyed embed cache
   (`tool_loader.py:177`) already handles new/removed tools across `reload()`.

### Integration points
- `src/gaia/mcp/mixin.py` — menu fragment in `get_mcp_client_system_prompt`;
  no change to registration (tools stay in the global registry, loader gates
  rendering only).
- `src/gaia/agents/base/tool_loader.py` — MCP-aware `validate_registry`.
- `hub/agents/chat/python/gaia_agent_chat/agent.py` — additional activation
  trigger in `_maybe_build_tool_loader` / `_dynamic_tools_active`.
- Base `Agent` — no interface change; `_select_tools_for_turn` already the hook.

### Edge cases
- MCP tools that share a bundle-like cohesion (a server's read+write pair):
  optional follow-up — synthesize a per-server implicit bundle so a match on one
  server tool pulls its siblings. Start without it; measure recall first.
- Server disconnect mid-session (`reload()`): loaded set may reference a gone tool.
  `select` already tolerates registry-absent names; confirm the menu regenerates.
- Native tool-calling models vs. embedded-JSON models: both render paths read the
  filter, so both are covered — but test both (see lemonade-client-patterns skill).

### Tests / evals
- Unit: extend `tests/unit/test_tool_loader_selection.py` with MCP-tagged entries
  (uncovered by bundles) — assert they score, surface on match, and are gated
  while static tools pass through in **MCP-only gating mode**.
- Unit: MCP-only gating mode — static (non-`_mcp_server`) tools always render;
  MCP tools only on match/`load_tools`. This is the regression guard for the
  Finding-4 correction.
- Unit: menu shape (truncation, cap, ≥2-tool gate) **and** placement — assert the
  menu renders in the volatile tool block, so a stable turn stays byte-identical
  (KV-cache guard).
- Eval: extend `src/gaia/eval/tool_cost.py` / `tool_recall.py` with a fixture of
  N connected mock MCP servers; assert token reduction and recall parity.
- **Agent eval required** (CLAUDE.md rule — this touches prompt assembly + tool
  schema): run `gaia eval agent` on the relevant category, compare to baseline
  before merge.
- No `validate_registry` test change (it doesn't gate MCP tools — see corrected
  analysis).

### Risk: **Low–Medium.** Reuses existing selection + rendering with no new
execution surface, but the MCP-only gating mode and menu placement are genuine
changes to loader activation and prompt composition — the KV-cache invariant and
static-tool recall are the two things a review must protect.

---

## Workstream 2 — Context-efficient tool results

**Goal:** large tool outputs don't transit the model context verbatim, and aren't
re-sent when piped into the next tool.

### Design

1. **Size-gated result handling in the wrapper.** In `_register_mcp_tools`'s
   `enhanced_wrapper` (`mixin.py:353`), measure serialized result size. Below a
   threshold (e.g. ~2KB / ~500 tokens) behave exactly as today (byte-identical —
   no regression for small results). Above it:
   - Store the full payload in a **session-scoped artifact store** (back it with
     the scratchpad backend, `src/gaia/scratchpad/`).
   - Return `{status, message, artifact: "<handle>", preview: <first N + schema
     summary>, instruction: "..."}` instead of the full `data`. The model sees a
     preview + a handle, not the payload.

2. **Handle format & resolver.** Handle e.g. `gaia://artifact/<session>/<id>`.
   Add an argument-resolution step at tool-invocation time (in the base agent's
   tool dispatch, or the MCP wrapper): before calling a tool, scan its arguments
   for artifact handles and re-hydrate them from the store. This is what lets a
   Drive transcript flow into a Salesforce/other tool **without** re-entering
   context.
   - **Fail loudly:** unknown/expired handle → actionable error naming the handle
     and that the artifact expired or belongs to another session. No silent empty.

3. **Preview generation.** For structured data: keys + row count + first row. For
   text: first N chars + length. Enough for the model to reason about *whether* to
   use the artifact and *how*, without the bulk.

### Integration points
- `src/gaia/mcp/mixin.py` — size gate + artifact write in `enhanced_wrapper`.
- New: `src/gaia/agents/base/artifacts.py` — session-scoped store with handle
  mint/resolve, TTL, size caps. **Prefer a small dedicated blob KV over the
  scratchpad backend**: `src/gaia/scratchpad/` is oriented to SQL *tables* for
  data analysis, not opaque blobs, so "reuse" is a stretch (kept as an open
  question below, but leaning dedicated).
- **Argument handle-resolution pass in `Agent._execute_tool`** (`agent.py:1909`)
  — confirmed single dispatch site: it resolves the tool name, then calls
  `self._tools_registry[tool_name]["function"]` at `agent.py:1995`. Scan/rehydrate
  handles in `tool_args` immediately before that call so it applies to **all**
  tools (a small tool can consume a large tool's artifact), MCP or not.
  - **Verify before building:** confirm the native tool-calling path also routes
    execution through `_execute_tool` (it should — `_on_tool_invoked` is recorded
    there for both paths). If a second executor exists, the resolver must cover it.

### Edge cases
- Handle resolution must be scoped to the current session — never cross-session
  (privacy + correctness). Store keyed by session id.
- Artifact store growth: TTL + max-size eviction; loud log on eviction.
- A model that pastes preview text as if it were the full payload: preview must be
  clearly labeled `[PREVIEW — full data in artifact <handle>]`.

### Tests
- Unit: small result → unchanged path (assert byte-identical wrapper output).
- Unit: large result → handle + preview; resolver re-hydrates on next call;
  unknown handle raises actionable error.
- Integration: two-tool pipe (produce large → consume by handle) never puts the
  payload in the message list (assert on captured messages).

### Risk: **Medium.** New store + dispatch hook; keep the small-result path
byte-identical to avoid regressing every existing MCP call.

---

## Workstream 3 — Privacy pass-through (optional, Phase 3)

**Goal:** sensitive fields flow connector→connector without ever entering model
context. Builds directly on WS2's handle mechanism.

### Design (sketch — validate before committing)
- On result ingestion, run the governance layer's PII detection over the payload.
- Tokenize detected fields (emails, phone numbers) into placeholders
  (`<pii:email:1>`) in the preview/context representation; keep the real values
  only in the artifact store.
- On outbound tool call, the resolver substitutes real values back from the store.
- The model orchestrates the flow seeing only placeholders.

### Why Phase 3
- Depends on WS2 landing first (shared store + resolver).
- Depends on governance PII detection maturity.
- Highest complexity, most speculative benefit for current local use cases.
- Decision gate: only build if a concrete connector→connector workflow (e.g.
  Sheets→CRM) is a real GAIA use case at that point.

### Risk: **Higher.** Correctness of tokenize/resolve round-trip is critical — a
missed substitution leaks PII into context (the exact thing it prevents). Requires
adversarial tests.

---

## Phasing & sequencing

| Phase | Workstream | Gate to proceed |
|-------|-----------|-----------------|
| 1 | WS1 (progressive disclosure for MCP) | Ship behind existing dynamic-tools toggle; eval shows token drop + recall parity |
| 2 | WS2 (context-efficient results) | Small-result path proven byte-identical; pipe test passes |
| 3 | WS3 (privacy pass-through) | A real connector→connector workflow exists; PII round-trip adversarially tested |

WS1 and WS2 are independent and can land in either order; WS3 requires WS2.

## Non-goals (the rejected half of the article)
- No model-authored code execution.
- No code sandbox / interpreter for agent-generated code.
- No "MCP servers as a code-API filesystem" presentation.

Rationale: GAIA's default models (Gemma-4-E4B, Qwen-4B) author orchestration code
unreliably; running that code locally is a large security surface; and the token
win is already captured by embedding-based selection (WS1) without either cost.
This matches CLAUDE.md's "no silent fallbacks / fail loudly" posture — a weak model
emitting plausible-but-wrong code is precisely the quiet-wrong-answer failure mode
to avoid.

## Open questions
1. Should the loader's MCP activation be a new config field, or purely
   auto-on-when-MCP-present? (Leaning auto-on, env-overridable, to match
   `GAIA_DYNAMIC_TOOLS`.)
2. WS2 artifact store: reuse scratchpad tables, or a dedicated lightweight KV?
   (Leaning scratchpad to avoid a new subsystem.)
3. WS1 per-server implicit bundles — needed for recall, or does flat semantic
   scoring suffice? (Measure before building.)
