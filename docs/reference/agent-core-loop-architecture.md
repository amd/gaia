# Agent Core Loop — Architecture Review & Improvement Roadmap

**Date:** 2026-03-22
**Scope:** `src/gaia/agents/base/agent.py` + `src/gaia/agents/chat/agent.py`
**Context:** Analysis driven by failures surfaced in the Agent UI eval benchmark (34-scenario suite).

---

## 1. Current Architecture

### 1.1 The Agentic Loop

The core loop lives in `Agent.process()` (`base/agent.py:1593`):

```
while steps_taken < steps_limit and final_answer is None:
    1. Determine execution state (PLANNING / EXECUTING_PLAN / DIRECT_EXECUTION / ERROR_RECOVERY)
    2. Build prompt for LLM
    3. Call LLM → get raw response string
    4. Parse response: extract JSON with "tool" key OR "answer" key
    5. If tool → execute tool, append result to conversation, loop
    6. If answer → run guards, set final_answer, break
```

### 1.2 Execution States

Five states are defined (`agent.py:73-77`):

| State | Purpose |
|---|---|
| `PLANNING` | Initial state; LLM generates a multi-step plan |
| `EXECUTING_PLAN` | Iterate through pre-generated plan steps without LLM |
| `DIRECT_EXECUTION` | Single-step tasks; skip planning |
| `ERROR_RECOVERY` | Tool failed; ask LLM to re-plan |
| `COMPLETION` | Final answer ready |

**Problem:** These states are tracked in `self.execution_state` but are not enforced by transition rules. The loop can move between any states based on string comparisons and ad-hoc `if/elif` branches. There is no guard preventing `COMPLETION` from being entered before a required workflow step (e.g., querying after indexing).

### 1.3 Output Parsing

The LLM returns a free-form string. Parsing proceeds through multiple fallback layers (`agent.py:481–909`):

1. Direct `json.loads()`
2. Code-block extraction (```` ```json ... ``` ````)
3. Regex brace-matching
4. Heuristic extraction from malformed JSON

This four-layer cascade exists because the LLM frequently produces malformed or non-JSON output. Each layer adds fragility and maintenance burden.

### 1.4 Tool Call History

`tool_call_history` is a list of `(tool_name, str(tool_args))` tuples capped at **5 entries** (`agent.py:2319`). It is used for:
- Loop detection (3 identical consecutive calls → abort)
- Determining `last_was_index` for the post-index guard

**Problems:**
- Cap of 5 means history is lost on longer chains
- Used only reactively (detecting bad patterns), never proactively (enforcing good patterns)

### 1.5 Guards (Reactive Patches)

Five guards are bolted onto the answer-acceptance path:

| Guard | Catches | Added |
|---|---|---|
| Planning-text guard | `"Let me now search..."` as final answer | Earlier session |
| Post-index query guard | Final answer with no query after `index_document` | This session |
| Tool-syntax artifact guard | `[tool:query_specific_file]` as answer text | This session |
| Raw-JSON hallucination guard | `{"status": "success", ...}` as answer text | Earlier session |
| Result-based query dedup | Same query issued 2+ times in a row | Earlier session |

Each guard was added in response to a specific eval failure. They work, but represent **symptom treatment** rather than structural prevention.

---

## 2. Identified Issues

### Issue 1 — No Workflow Enforcement (High Severity)

**Symptom:** Agent indexes a document, then returns a confident final answer from LLM memory instead of querying the document. Score: 0 correctness (hallucination).

**Root cause:** The loop has no concept of "you must complete step X before you can produce a final answer." Any step can produce `{"answer": "..."}` and exit the loop. The post-index guard catches one specific case (last tool was `index_document`), but a chain like `index_document → list_indexed_documents → [answer without query]` bypasses it because `list_indexed_documents` is the last tool, not an index tool.

**Code location:** `agent.py:2454–2581` — the entire guard block is a series of pattern-checks on `answer_candidate`.

---

### Issue 2 — Unstructured LLM Output (High Severity)

**Symptom:** LLM produces `[tool:query_specific_file]` as answer text, raw JSON blobs, planning sentences, or plain prose tool invocations. Each failure mode requires a new guard.

**Root cause:** The LLM is prompted to produce JSON but there is no enforcement at the generation level. The model is Qwen3-GGUF running via Lemonade Server. Grammar-constrained sampling (GBNF grammars in llama.cpp) can restrict token generation to valid JSON matching a schema, eliminating this entire class of failures.

**Code location:** `agent.py:481–909` (4-layer parse cascade) + `agent.py:2531–2580` (artifact/JSON guards).

---

### Issue 3 — Fresh Agent Per Message, Thin Cross-Turn State (Medium Severity)

**Symptom:** On multi-turn sessions, the agent re-searches for documents it indexed in a previous turn (context blindness). Score: `context_retention = 2`, `efficiency = 3`.

**Root cause:** `_chat_helpers.py:290` — `ChatAgent(config)` is instantiated fresh for every HTTP request. `rag.indexed_files` (the set of what's indexed) starts empty each turn. Documents are restored from session DB (`agent.py:232–257`) by re-indexing from disk, but:

1. The LLM does not see *what was retrieved* in prior turns — only what files are indexed
2. Restoration can silently fail (file moved, DB miss)
3. The LLM may not connect "api_reference.py is indexed" to "user is asking about a Python file"

**Code location:** `ui/_chat_helpers.py:268–290` (agent construction), `agents/chat/agent.py:232–257` (restoration).

---

### Issue 4 — Tool Call History Cap (Low-Medium Severity)

**Symptom:** On sessions with many tool calls, loop detection loses history. An agent that called `query_specific_file` 6 steps ago is treated as having no prior query.

**Root cause:** `tool_call_history` is pruned to 5 entries for memory reasons (`agent.py:2319`). The post-index query guard scans `tool_call_history` to find if a query was issued after the last index. If there are 6+ tool calls between an index and the current step, the index disappears from history and the guard stops firing.

**Code location:** `agent.py:2319–2320`.

---

### Issue 5 — Context Bloat on Long Sessions (Medium Severity)

**Symptom:** On large-document or multi-turn scenarios, full tool results (RAG chunks, directory listings, file contents) are appended to `messages` verbatim. The conversation window grows until the LLM's attention degrades or the context limit is hit.

**Root cause:** No summarization or deduplication of tool results before appending to `messages`. RAG chunks are truncated at 5000 chars (`agent.py:31`), but the chunk *count* is not limited, and repeated queries to the same document add more copies.

**Code location:** `agent.py:31–32` (truncation constants), tool result appending throughout the loop.

---

### Issue 6 — Planning-then-Execution Mismatch (Low Severity)

**Symptom:** `PLANNING` state generates a multi-step plan. `EXECUTING_PLAN` iterates through it deterministically. But if step N's output is needed to determine step N+1's args (dynamic parameters), the plan may use stale values resolved at plan-creation time.

**Root cause:** `_resolve_plan_parameters()` replaces `$prev_result` tokens, but complex data flows (e.g., "use the file path returned by search_file in the next index_document call") rely on the LLM having correctly predicted the output at plan time.

**Code location:** `agent.py:1633` (`_resolve_plan_parameters` call), `agent.py:1620–1659` (plan execution).

---

## 3. Proposed Improvements

### 3.1 Explicit Workflow State Machine (Addresses Issues 1, 4)

**Design:** Define a `WorkflowPhase` enum separate from the existing execution states:

```python
class WorkflowPhase(Enum):
    DISCOVER   = "discover"   # search_file, browse_files, list_indexed_documents
    INDEX      = "index"      # index_document, index_directory
    QUERY      = "query"      # query_specific_file, query_documents
    SYNTHESIZE = "synthesize" # multiple queries completed, building answer
    ANSWER     = "answer"     # final answer allowed
```

**Transition rules (enforced in code, not prompt):**

```
DISCOVER → INDEX    (after finding a file)
INDEX    → QUERY    (mandatory after every index_document)
QUERY    → QUERY    (additional queries allowed)
QUERY    → SYNTHESIZE (after ≥1 query per indexed doc)
SYNTHESIZE → ANSWER
DISCOVER → ANSWER  (only if no relevant docs found and user acknowledged)
```

**Implementation:** Replace the 5 ad-hoc guards with a single `_check_workflow_transition(from_phase, to_phase)` method. The answer-acceptance path checks `current_phase == WorkflowPhase.ANSWER` before setting `final_answer`.

**Benefit:** Eliminates post-index hallucination, planning-text answers, and tool-artifact answers structurally. New failure modes don't require new guards — they fail to reach `ANSWER` phase.

---

### 3.2 Structured Output via JSON Schema (Addresses Issue 2)

**Design:** Send a JSON schema with the LLM request that constrains output to exactly:

```json
{
  "oneOf": [
    {
      "type": "object",
      "required": ["tool", "tool_args"],
      "properties": {
        "thought": {"type": "string"},
        "tool": {"type": "string", "enum": ["<list of registered tools>"]},
        "tool_args": {"type": "object"}
      }
    },
    {
      "type": "object",
      "required": ["answer"],
      "properties": {
        "answer": {"type": "string", "minLength": 10}
      }
    }
  ]
}
```

**For Lemonade/llama.cpp:** Use GBNF grammar generation from the schema. `llama-cpp-python` supports `grammar` parameter; Lemonade Server can expose it.

**For Claude/OpenAI backends:** Use native `response_format: {"type": "json_schema", ...}`.

**Benefit:** Eliminates the 4-layer parse cascade, all 4 artifact/hallucination guards, and the `[tool:X]` pattern entirely. The LLM physically cannot produce malformed output.

**Risk:** Small models may produce worse reasoning under strict schema constraints. Needs benchmarking.

---

### 3.3 Per-Turn Context Injection (Addresses Issue 3)

**Design:** Before each turn's system prompt is sent, inject a compact session summary built from the session's message history:

```python
def _build_session_context(self) -> str:
    """Build a compact summary of what was done in prior turns."""
    lines = []
    for turn_num, turn in enumerate(self.session_turns, 1):
        indexed = turn.get("indexed_docs", [])
        key_facts = turn.get("retrieved_facts", [])  # top 3 per query
        if indexed or key_facts:
            lines.append(f"Turn {turn_num}:")
            for doc in indexed:
                lines.append(f"  - Indexed: {doc}")
            for fact in key_facts:
                lines.append(f"  - Retrieved: {fact[:100]}")
    return "\n".join(lines) if lines else ""
```

This summary is injected at the top of the system prompt:

```
[SESSION MEMORY]
Turn 1: Indexed api_reference.py
  - Retrieved: Bearer token auth via Authorization header; get_auth_token(api_key, api_secret)
Turn 2: Indexed employee_handbook.md
  - Retrieved: PTO = 15 days first year; contractors not eligible for benefits
```

**Storage:** Add `retrieved_facts` to `SessionModel` in the UI database alongside `indexed_documents`.

**Benefit:** The LLM sees exactly what was retrieved in prior turns. Eliminates context blindness. Agent never re-searches for a file it found two turns ago.

**Cost:** ~200–400 extra tokens per turn. Negligible vs. typical 4000-token turns.

---

### 3.4 Tool Result Compression (Addresses Issue 5)

**Design:** After each tool call, apply a compression pass before appending to `messages`:

```python
def _compress_tool_result(self, tool_name: str, result: str, query: str = None) -> str:
    """Compress tool result to avoid context bloat."""
    # RAG results: keep top-3 most relevant chunks, discard rest
    if tool_name in ("query_documents", "query_specific_file"):
        return self._keep_top_chunks(result, n=3, query=query)
    # Directory listings: summarize file counts by extension
    if tool_name in ("browse_files", "search_file"):
        return self._summarize_file_list(result)
    # Already-seen content: deduplicate against recent messages
    if self._is_duplicate_content(result):
        return f"[Same result as step {self._find_prior_result(result)}]"
    return result
```

**Deduplication:** Before appending a tool result, hash it and compare against the last 10 results. If matched, replace with a back-reference instead of full content.

**Benefit:** 30–50% context reduction on multi-turn/large-doc scenarios. Fewer tokens = faster inference and better LLM attention allocation.

---

### 3.5 Unbounded Tool Call History (Addresses Issue 4)

**Design:** Replace the 5-entry sliding window with a full per-turn call log:

```python
# Current (fragile)
tool_call_history = []          # max 5 entries
if len(tool_call_history) > 5:
    tool_call_history.pop(0)

# Proposed (complete)
tool_call_log = []              # all calls this turn, never pruned
```

Loop detection still uses the last-5 window (to avoid false positives on similar but non-identical calls). The post-index query guard uses `tool_call_log` for correctness.

**Migration:** Replace `tool_call_history` references in the post-index guard with `tool_call_log`. Keep the 5-entry window only for the loop-detection path.

**Cost:** Negligible memory — typical turns have 5–15 tool calls.

---

### 3.6 Adaptive Step Budget (Addresses Issue 6 partially)

**Design:** Classify query complexity at turn start and set `steps_limit` accordingly:

```python
def _estimate_step_budget(self, user_input: str, has_indexed_docs: bool) -> int:
    """Estimate steps needed based on query complexity signals."""
    lower = user_input.lower()
    # Simple factual lookup from indexed doc
    if has_indexed_docs and len(user_input) < 100:
        return 5
    # Discovery required (no docs indexed)
    if not has_indexed_docs:
        return 15
    # Synthesis across multiple docs / long analysis
    if any(w in lower for w in ("compare", "summarize all", "across", "list all")):
        return 25
    # Default
    return 12
```

**Benefit:** Simple queries finish in 3–5 steps instead of burning toward a 20-step limit. Complex queries get more budget without requiring `--max-steps` CLI overrides.

---

## 4. Implementation Priority

| Priority | Change | Effort | Impact | Dependencies |
|---|---|---|---|---|
| 1 | **Per-turn context injection** (3.3) | 1–2 days | High | Session DB schema update |
| 2 | **Unbounded tool call log** (3.5) | 2 hours | Medium | None |
| 3 | **Tool result compression** (3.4) | 2–3 days | Medium | None |
| 4 | **Adaptive step budget** (3.6) | 4 hours | Low-Medium | None |
| 5 | **Structured output via JSON schema** (3.2) | 3–5 days | Very High | Lemonade Server GBNF support |
| 6 | **Workflow state machine** (3.1) | 1–2 weeks | Very High | Refactor of full loop |

**Recommended immediate actions (this sprint):**
- ✅ Already done: post-index guard, tool-artifact guard, planning-text guard (reactive)
- 🔜 Next: per-turn context injection (#1) and unbounded tool log (#2) — small changes, high reliability payoff
- 📋 Backlog: structured output and state machine — architectural, require design review

---

## 5. What Won't Be Fixed by These Changes

- **LLM quality floor:** The underlying Qwen3-0.6B / Qwen3-Coder-30B models have inherent limitations. Structured output and workflow enforcement raise the floor but don't eliminate all reasoning errors.
- **Fresh agent per message:** The UI server's request-per-agent design is an architectural choice driven by stateless HTTP. Full persistence across turns would require either long-running agent threads (memory/resource concern) or a robust state serialization layer. Per-turn context injection (#3.3) mitigates this without changing the server model.
- **RAG retrieval quality:** Semantic mismatch between query and chunk is a RAG system issue (embedding model, chunking strategy), not an agentic loop issue. Addressed separately in `src/gaia/rag/sdk.py`.

---

## 6. Test Coverage Gaps

The eval benchmark currently covers these loop behaviors:

| Behavior | Scenario | Status |
|---|---|---|
| Post-index query enforcement | `search_empty_fallback` | ✅ Covered |
| Cross-turn context retention | `cross_turn_file_recall`, `search_empty_fallback` T2 | ✅ Covered |
| Planning text blocking | `multi_step_plan` | ✅ Covered |
| Tool artifact blocking | `multi_step_plan` | ✅ Covered |
| Loop detection (repeated calls) | — | ❌ Not covered |
| Max steps graceful degradation | — | ❌ Not covered |
| Error recovery (tool failure) | `file_not_found` (partial) | ⚠️ Partial |
| Dynamic plan parameter resolution | — | ❌ Not covered |

Adding scenarios for loop detection and max-steps behavior would prevent regressions when the loop is refactored.
