# GAIA Flagship Agent — Internals Gap-Analysis Source Material

Repo: `C:\Users\14255\Work\gaia`, branch `feat/gaia-flagship-agent-2804`. All citations verified by direct file read at the cited line by the researching sub-agent. Read-only research artifact — dense, factual, no recommendations.

## 1. Agent loop / state machine

**File:** `src/gaia/agents/base/agent.py` (6243 lines)

### 1.1 States (agent.py:472-476)
```
STATE_PLANNING        = "PLANNING"
STATE_EXECUTING_PLAN  = "EXECUTING_PLAN"
STATE_DIRECT_EXECUTION = "DIRECT_EXECUTION"
STATE_ERROR_RECOVERY  = "ERROR_RECOVERY"
STATE_COMPLETION      = "COMPLETION"
```
Progress-label map at `agent.py:481-487`.

**`STATE_DIRECT_EXECUTION` is dead state.** Repo-wide grep finds exactly three hits: its definition (474), its progress-label entry (484), and one read-only display check at `agent.py:4344` (`if self.execution_state == self.STATE_DIRECT_EXECUTION:`). No `self.execution_state = self.STATE_DIRECT_EXECUTION` assignment exists anywhere in this file or any subclass under `hub/agents/`.

`self.execution_state` initializes to `STATE_PLANNING` at `agent.py:706` (`__init__`) and resets to `STATE_PLANNING` at the top of every call at `agent.py:4016`.

### 1.2 Main loop control flow

Public entry `process_query` (`agent.py:3930-3955`) wraps `_process_query_impl` (`agent.py:3957`+).

- **Turn start**: `_process_query_impl` (`agent.py:3957-4052`) resets per-turn state (`execution_state=STATE_PLANNING`, `current_plan=None`, `current_step=0`, `plan_iterations=0`, tool-call caches), seeds `messages` with the user query plus persisted `conversation_history`, computes `steps_limit` (`agent.py:4031`).
- **Loop**: `while steps_taken < steps_limit and final_answer is None:` at `agent.py:4053`. Per iteration:
  - Checks cooperative `_cancel_event` (Agent-UI stop) at `agent.py:4059-4071`.
  - If `execution_state == STATE_EXECUTING_PLAN` and steps remain: dequeues the next plan step directly (`agent.py:4084-4341`), **no LLM call this iteration** — resolves `$PREV`/`$STEP_N` placeholders (`_resolve_plan_parameters`, called `agent.py:4112`), executes via `_execute_tool` (`agent.py:4138`), advances `current_step`, or flips to `STATE_ERROR_RECOVERY` (`agent.py:4231`) or `STATE_COMPLETION` when the plan is exhausted (`agent.py:4245`).
  - Otherwise queries the LLM (`agent.py:4342`+): builds a state-specific prompt (error-recovery prompt `agent.py:4362-4382`), then streaming (`self.chat.send_messages_stream`, `agent.py:4447`, retry-and-shrink loop via `_shrink_messages_for_overflow`, `agent.py:3438-3495`, on context overflow) or non-streaming (`self.chat.send_messages`, `agent.py:4613`, same pattern).
  - **Parsing**: `<think>...</think>` stripped (`agent.py:4746-4748`); `_parse_llm_response` (`agent.py:2220`+) recognizes a native `{"__tool_calls__": ...}` envelope, an embedded-JSON `{"thought","tool","tool_args"}` object, or plain text. Malformed JSON: logged, `error_count` incremented; after 3 consecutive parse failures the loop gives up (hardcoded literal `3` at `agent.py:4795`, not a named constant) with a fixed final answer (`agent.py:4795-4807`); otherwise a corrective message is injected and the loop continues.
  - **Dispatch** — three mutually exclusive branches: (1) `"plan" in parsed` (`agent.py:4997`) installs `self.current_plan`, sets `STATE_EXECUTING_PLAN` (`agent.py:5075`); (2) native parallel `tool_calls` (`agent.py:5149`+, issue #944) — sequential fan-out over each call, `_execute_tool` per call, appended via `_create_tool_message` keyed to `tool_call_id` (`agent.py:5225-5231`), errors set `STATE_ERROR_RECOVERY` (`agent.py:5276`); (3) legacy single-tool branch (`agent.py:5290`+), same execute→truncate→append→error pattern (`agent.py:5317-5502`).
  - **Results fed back**: every path routes the raw result through `_handle_large_tool_result` then `_create_tool_message` before it's visible to the next LLM call (see 1.5, the two truncation gates).
  - **Stop — success**: `"answer" in parsed` (`agent.py:5520`), gated by several reject-and-loop-again heuristics (planning-text guard `agent.py:5669-5697`; tool-syntax-artifact guard `agent.py:5702-5724`; raw-JSON-hallucination guard `agent.py:5729-5756`; SD capability-claim guard `agent.py:5764-5858`; post-index-without-query guard `agent.py:5560-5660`). If none fire: `final_answer = self.finalize_answer(...)`, `execution_state = STATE_COMPLETION`, `break` (`agent.py:5895-5911`).
  - **Stop — step limit**: `agent.py:5914` — prints a max-steps message; only on an interactive TTY with `silent_mode=False` prompts the user y/n for 50 more steps (`agent.py:5930-5945`); non-interactive/silent just breaks (`agent.py:5946-5948`).
  - **Stop — error**: tool errors don't stop the loop; they set `STATE_ERROR_RECOVERY` and continue, reprompting with an error-recovery message (`agent.py:4362-4392`, `5496-5502`). Recovery is bounded only by the overall step cap (aside from the 3-strikes parse cap and the repeat-call cap below) — there is **no separate error-count cap**.
- **Post-loop**: builds result dict (`status`: success/failed/incomplete, `agent.py:5989-6012`), optional JSON trace write (`agent.py:6015-6017`), stores `self.last_result`, optional `_after_process_query` mixin hook (`agent.py:6025-6029`).

### 1.3 Caps

| Cap | Default | Definition | Env override |
|---|---|---|---|
| `max_steps` (per-turn) | `DEFAULT_MAX_STEPS = 50` | `agent.py:82`, resolved via `default_max_steps()` `agent.py:85-108` | `GAIA_AGENT_MAX_STEPS` (raises `ValueError` on invalid/non-positive) |
| `max_plan_iterations` | `3` (`0` = unlimited) | ctor param `agent.py:608`, checked `agent.py:4257-4260` | — |
| `max_consecutive_repeats` | `4` | ctor param `agent.py:609`, checked `agent.py:5134`, breaks loop via `_build_loop_break_summary` `agent.py:6049-6065` | — |
| Malformed-parse retry | `3` (hardcoded literal) | `agent.py:4795` | — |
| Per-tool exec timeout | `DEFAULT_TOOL_TIMEOUT = 180.0`s | `agent.py:117`, resolved `agent.py:120-144` | `GAIA_AGENT_TOOL_TIMEOUT` |

### 1.4 Self-critique/reflection state — confirmed **absent**

No state or code path performs an LLM call to critique prior work. The five states (1.1) are `PLANNING`/`EXECUTING_PLAN`/`DIRECT_EXECUTION` (dead)/`ERROR_RECOVERY`/`COMPLETION` — none is a reflection/review state. Grep for `reflect|critique|self-review|self_review` returns only PR-comment-round citations in inline comments (`agent.py:3527,3642,3725,3761,3814,4275,4355` — "reflection C2/C3"), unrelated to runtime states. `ERROR_RECOVERY` is reactive (fires only after a tool error, asks for a corrected plan) — it never re-evaluates a *successful* answer. `STATE_COMPLETION`'s prompt (`agent.py:4295-4306`) asks the model to "check if more work is needed" — checks plan-step completeness only, never answer correctness. This is the closest analog to self-review and it is a templated instruction inside one prompt, not a distinct critique pass.

### 1.5 `truncation_budget()` and the two-gate issue

**Definition:** `src/gaia/llm/lemonade_client.py:195-209`:
```python
def truncation_budget(device):
    ctx = NPU_CTX_SIZE if not normalized or normalized == "npu" else GPU_CTX_SIZE
    threshold = round(ctx * _TRUNCATE_THRESHOLD_RATIO)
    target = round(threshold * _TRUNCATE_TARGET_FRACTION)
    return threshold, target
```
Constants: `GPU_CTX_SIZE = 65536` (`:173`), `NPU_CTX_SIZE = 32768` (`:174`), `_TRUNCATE_THRESHOLD_RATIO = 30000/32768` (`:191`), `_TRUNCATE_TARGET_FRACTION = 2/3` (`:192`). Net: NPU/unset → threshold 30000 chars / target 20000; other device → threshold ~61875 / target ~41250. **The budget derives from local hardware profile, not the active model's real context window** — relevant even when the backend is Claude (flagged as limitation #1, `GAIA_AGENT_V2_SCOPE.md:106-110`).

Imported at `agent.py:47`. Two call sites:
- **Gate 1 — `_handle_large_tool_result`** (`agent.py:3268-3335`, call `agent.py:3296`): truncates the JSON-serialized raw tool result to `target` chars via `_truncate_large_content(..., as_json=True)`, re-parses as JSON. Feeds `previous_outputs`/`step_results` and gate 2.
- **Gate 2 — `_create_tool_message`** (`agent.py:3497-3546`, call `agent.py:3524`): for any non-string tool output, calls `truncation_budget()` again and truncates (prose mode) to the same `target` before splicing into the `role=tool` message text the LLM actually sees.

**Verification against `GAIA_AGENT_V2_SCOPE.md`:** the doc's row 1 (`:16`) describes a now-fixed bug where gate 2 used to re-truncate to a hardcoded **2,000 chars** regardless of what gate 1 had already fitted — making gate 1 dead for anything over 2 KB. Marked "Fixed — `f6245a5b`" in the doc; §5.2 (`:112-114`) still calls this "Two independent truncation gates with no shared owner." **Current code**: two gates still exist as two separate functions/call sites, but post-fix gate 2 calls the *same* `truncation_budget()` (`agent.py:3524`) instead of a hardcoded 2000 — an inline comment (`agent.py:3521-3523`) states the invariant explicitly: *"Every call site hands this a result `_handle_large_tool_result` already fitted to the device budget, so this is a backstop, not the real gate -- it must not be tighter than the gate it backs."* So today gate 2 is sized as a no-op backstop (shares `target` with gate 1), not an independently tighter cap — but nothing enforces gate 2 ≥ gate 1 other than the shared constant and the comment; there is no assertion or single owning object. The specific double-truncation regression (2000 < 20000/40000) is fixed as of `f6245a5b`; the structural "no shared owner" architecture criticized in the scope doc is unchanged.

---

## 2. Memory system

**Files:** `src/gaia/agents/base/memory.py` (2903 lines, read in full), `src/gaia/agents/base/procedural_memory.py`, `src/gaia/agents/base/memory_store.py`, `src/gaia/skills/skill_synthesis.py`, plus `docs/spec/agent-memory-architecture.md`, `docs/plans/skill-synthesis.mdx`, `docs/plans/adaptive-skills.mdx`.

### 2.1 Memory categories

`VALID_CATEGORIES` frozenset, `memory_store.py:92-108`:
```
{"fact", "preference", "error", "skill", "note", "reminder", "system", "profile", "permission"}
```
- `_PRIVILEGED_CATEGORIES = {"system", "profile", "permission"}` (`memory_store.py:115`) — writable only by explicit tool/system code, never by the LLM conversation extractor.
- `EXTRACTABLE_CATEGORIES = VALID_CATEGORIES - _PRIVILEGED_CATEGORIES` (`memory_store.py:119`).
- The `remember` tool's docstring restates a narrower list to the LLM: "fact, preference, error, skill, note, reminder" (`memory.py:2457`), deliberately omitting the three privileged categories.
- `category='skill'` here is a **user-told fact** ("I know vim") — a distinct concept from the synthesized "procedure" mechanism below (§2.2); called out explicitly in `docs/plans/skill-synthesis.mdx`'s "Why procedures is a separate table" section (citing `memory_store.py:97`).
- Goals/tasks are stored separately in a `GoalStore`, not as a knowledge category (`memory_store.py:89-91` comment).

`docs/spec/agent-memory-architecture.md:39-44` maps categories onto CoALA's four tiers (working/episodic/semantic/procedural) but **internally contradicts itself**: line 44 says "procedural" = `knowledge(category='error')` + `knowledge(category='skill')`, while the same doc's Table 4 (`:208-231`) documents the real separate `procedures` table + FAISS index described below — the doc was never updated when the real mechanism landed.

### 2.2 Procedural/skill memory — implemented and live in the request path

**This is the single most consequential finding of this investigation.** The mechanism exists, is wired into the main loop, and runs automatically — contradicting the status line of one of its own planning docs.

**Storage schema**: `procedures` table (`memory_store.py:283`): `id, name, when_to_use, markdown_body, tools_required, tool_sequence, success_count, attempt_count, provenance, version, enabled, embedding, superseded_by, created_at, last_used_at`. Methods: `put_skill` (`:2532`), `search_skills` (`:2674`), `supersede_skill` (`:2735`), `touch_skills` (`:2756`), `iter_sessions` (`:2785`).

**Separate FAISS index over the `when_to_use` trigger vector**: `ProceduralMemoryMixin._rebuild_proc_faiss_index` (`procedural_memory.py:50-116`) builds a distinct `IndexFlatIP` (cosine via L2-normalized vectors) over `procedures.embedding`, kept separate from the knowledge FAISS index (`memory.py:687`) specifically so goal→procedure recall doesn't pollute fact recall. `_proc_faiss_add` (`:118-144`), `_proc_faiss_search` (`:145-176`).

**Recall — goal-based semantic matching**: `recall_skill(goal, top_k=2, similarity_tau=None)` (`procedural_memory.py:181-291`) embeds `goal` with the same 768-dim `nomic-embed-text-v2-moe-GGUF` embedder used for knowledge (`memory.py:148,151`), searches the procedures index, drops matches below `SIMILARITY_TAU = 0.82` (`skill_synthesis.py:59`), `top_k=2` default. It is **not** exposed as an agent-callable `@tool` — an internal method the planner calls programmatically (`procedural_memory.py:190-191`), preserving the five-tool memory registry (`remember` `memory.py:2435`, `recall` `:2518`, `update_memory` `:2701`, `forget` `:2786`, `search_past_conversations` `:2795`). Disabled/superseded procedures are excluded even from a stale index (`procedural_memory.py:203-204,259-268`). Matches render into the system prompt via `_build_recalled_skills_prompt` (`:335-368`), capped at `MAX_RECALL_BODY_CHARS = 1500` chars/body (`skill_synthesis.py:66`). `_refresh_recalled_skills(goal)` (`procedural_memory.py:400-427`) recalls once per turn, caches, only rebuilds the prompt if the set changed. **Confirmed live call site**: `MemoryMixin.process_query` calls `self._refresh_recalled_skills(user_input)` at `memory.py:2162`, inside the wrapper around every agent turn (`memory.py:2143-2168`).

**Storage (synthesis) — fully automatic, no manual trigger required**: driver `ProceduralMemoryMixin._synthesize_skills(since=None)` (`procedural_memory.py:433-530`), five-step pipeline: DETECT (`extract_sequences`, cheap SQL, no LLM) → CLUSTER (`cluster_by_goal`, embeds each cluster's goal, agglomerates at cosine ≥ `SIMILARITY_TAU`) → DISTILL (`distill_cluster`, one LLM call via `self.chat.send_messages`) → RECONCILE/STORE (`reconcile_and_store`, ADD/UPDATE/NOOP, Zep-style supersede on higher success rate) → add to procedures FAISS index (`:527`). Gating thresholds (`skill_synthesis.py:50-56`): `MIN_STEPS=3` (tool-call span), `MIN_OCCURRENCES=3` (similar successful sequences), `MIN_SUCCESS_RATE=0.80` (cluster success rate); `MAX_CLUSTERS_PER_PASS=10` bounds LLM calls (`skill_synthesis.py:62`, applied `procedural_memory.py:500`). **Call chain proving automaticity**: `process_query` (`memory.py:2143`) → on the first real query, `_run_memory_post_init()` fires (`memory.py:2152-2154`) → its Step 8 calls `self._synthesize_skills()` (`memory.py:1569-1579`, call at `:1575`), after Step 6 `reconcile_memory(max_pairs=20)` (`:1553-1555`) and Step 7 `consolidate_old_sessions(max_sessions=5)` (`:1561-1563`) — runs once per process, off the hot path, wrapped in try/except so a synthesis failure never breaks the user's turn. Fail-loud posture confirmed: embedder failure re-raises (`procedural_memory.py:488-495`); Lemonade-down distillation aborts the pass with a warning, no smaller-model fallback (`:500-511`); malformed/`"SKIP"` distill output skips only that cluster (`:513-515`).

**Verdict**: skill synthesis is fully implemented and wired into the live agent loop.

### 2.3 Plan vs. implementation gap

**`docs/plans/skill-synthesis.mdx` (#887) — status line is stale/false.** Its header states *"Status: All PROPOSED... Nothing in this spec is implemented"* / *"No pipeline, `procedures` table, or `recall_skill` exists in code"* (`:16-20`), and its own "current state of the code" table (`:749`) lists every symbol above (`skill_synthesis.py`, `procedures` table, `extract_sequences`, `cluster_by_goal`, `distill_cluster`, `DistilledProcedure.parse`, `reconcile_and_store`, `recall_skill`, `_synthesize_skills`, `put_skill`, `search_skills`, `supersede_skill`, `iter_sessions`) as "NOT FOUND (verified absent on main)" — **all now present and verified to exist** (§2.2). This doc predates the implementation landing and was never updated.

**`docs/plans/adaptive-skills.mdx` (#2674) — current, and self-aware of the above.** A second-generation plan doc that correctly cites the already-shipped mechanism by file:line in its "Grounds on (exists today, verified on main)" block (`:8`), matching §2.2 independently. Its purpose: propose a further per-user "learned overlay" layer (`skill_deltas` table) on top of *authored* SKILL.md skills, distinct from the *synthesized* procedures corpus. **Not implemented, explicitly proposed only**: `skill_deltas` table (v3→v4 migration, `:684-708`), delta grammar (`tool-hint`/`parameter`/`exception`/`preference`/`example` kinds, `:593-606`), section anchoring (`anchor.section`/`anchor.digest`, `:609-630`), staged-write consent gate, `EffectiveSkill = base ⊕ deltas` resolution (`:742-767`).

This doc's "code review" section also documents **real, present-tense defects in the already-shipped §2.2 mechanism** (not future risk):
- **KV-cache claim already false in the shipped recall path.** `_refresh_recalled_skills` runs every turn and rebuilds the system prompt whenever the recalled set changes (`procedural_memory.py:418-425`), yet `memory.py`'s own docstring near the call site claims the prompt "is left frozen so the LLM inference engine can reuse its KV cache across turns." Mixin prompt fragments (including the recalled-procedure block) compose **first** in `_compose_system_prompt` — the position most disruptive to prefix/cache reuse — while the genuinely volatile tools block was deliberately moved last. Filed as issue **#2686** against the shipped code (`adaptive-skills.mdx:174-177`).
- **Success-rate gate is write-once and measures the wrong thing.** `reconcile_and_store` always inserts new rows with `skill_id=None` (`skill_synthesis.py:693-704`), so `put_skill`'s `UPDATE...success_count` branch (`memory_store.py:2613`) is dead code — unreachable from the synthesis path. No `increment`/`record_procedure_outcome` call exists anywhere; a recalled procedure that fails 20 times in production keeps its birth-time confidence numbers. `iter_sessions` counts `success_count` as "no exception raised" (`memory_store.py:2855-2858`), not "user got the desired outcome," so `MIN_SUCCESS_RATE=0.80` measures API cleanliness, not task correctness. The plan doc calls this "the most consequential gap found in either review pass" (`:221-223`).
- **`procedures` table structurally can't hold proposed delta rows** without breaking invariants: `when_to_use`/`markdown_body` are `NOT NULL` (`memory_store.py:286-287`); the reconcile scan would treat an unrelated delta row as a supersede target; the FAISS rebuild indexes every enabled row so a delta fragment would surface as if it were a complete proven procedure (`adaptive-skills.mdx:120`, "the exact failure #2676 describes").
- **"Tier 1 is free" claim is inaccurate** — admitting a recalled procedure's `tools_required` into the tool loader is bounded, not zero-cost: below `DEFAULT_MAX_TOOLS=14` (`tool_loader.py:72`) it adds ~40-80 tokens of schema; at the cap it LRU-evicts another tool (net-zero, not free) (`tool_loader.py:326-337`).

**Net gap**: the shipped DETECT→CLUSTER→DISTILL→STORE→RECALL loop is fully live. Not implemented: (a) the adaptive-skills v2 overlay (deltas/anchoring/consent gate, greenfield), (b) outcome-based reinforcement of the already-shipped procedures' success/attempt counters (a present-day defect in shipped code, not a future feature).

### 2.4 Decay/expiry

- **Confidence decay (soft)**: `MemoryStore.apply_confidence_decay(days_threshold=30, decay_factor=0.9)` (`memory_store.py:2872-2909`) — multiplies `confidence` by 0.9 for any `knowledge` row with `last_used` older than 30 days AND `updated_at` before that cutoff, skipping superseded rows. Called at `memory.py:586` during `init_memory`, again at session rotation `memory.py:2897`.
- **Hard prune (90 days)**: `MemoryStore.prune(days=90)` (`memory_store.py:2911-2951+`) deletes `tool_history` and `conversations` rows older than the cutoff, and `knowledge` rows where `confidence < LOW_CONFIDENCE_PRUNE_THRESHOLD` (`0.1`, `memory_store.py:139`) AND `last_used < cutoff`. Called `memory.py:593` with default `days=90`. Matches `docs/spec/agent-memory-architecture.md:46`: "Episodic memory... pruned at 90 days but consolidated to semantic memory before deletion."
- `CONFIDENCE_BUMP_PER_RECALL = 0.02` (`memory_store.py:136`) — counter-force to decay, applied on each recall (`agent-memory-architecture.md:28`: "+0.02 on recall, ×0.9 decay").
- Special exemption for `error` category: `_forget_errors_for_tool` (`memory.py:2291-2313`) deletes stored error entries for a tool the moment it succeeds again — docstring (`:2294-2297`) explicitly notes without this, "nothing expires it, nothing lowers its confidence, and the model is told to avoid the tool forever."

### 2.5 "No context compaction" rationale — not found beyond the one-line claim

The claim appears verbatim/near-verbatim in exactly three places: `CLAUDE.md:712`, `docs/roadmap.mdx:231`, `docs/plans/agent-ui.mdx:311`. None explain *why* or name a tradeoff. A broad search of `src/gaia/`, `docs/`, `hub/` for "compaction"/"compact" (46 files) found no additional rationale — matches were all unrelated (tool-output truncation, email-thread condensing, etc., not conversation-history compaction as an alternative design). `docs/spec/agent-memory-architecture.md` (2325 lines, read in full) and `docs/reference/agent-core-loop-architecture.md` contain no comment/docstring/prose justifying the decision or naming acknowledged tradeoffs. Closest adjacent material: `agent-memory-architecture.md:31`'s "Key design decision" paragraph about the frozen-prefix/KV-cache approach, and `adaptive-skills.mdx:42-51`'s "Why this exists" argument that structured, cheap-to-store tiers beat writing prose into the context window — but that's a justification for the newer adaptive-skills overlay, not the original no-compaction decision. **Conclusion: no rationale/tradeoff discussion exists in the codebase or docs beyond the bare claim, repeated three times verbatim.**

---

## 3. Skills system

**Files:** `src/gaia/skills/` (`manager.py`, `format.py`, `sets.py`, `tiers.py`, `permissions.py`, `loader.py`, `cli.py`), `hub/skills/`, `docs/plans/skill-format.mdx`.

### 3.1 Discovery roots

`SkillManager.roots` (`manager.py:140-156`) — three precedence-ordered roots, highest first:
1. `ROOT_AGENT_BUNDLED` — agent-bundled `skills/<name>/` dirs (`manager.py:143-145`)
2. `ROOT_USER` — `~/.gaia/skills` via `user_skills_dir()` (`manager.py:74-81`), overridable by `GAIA_CONFIG_DIR`
3. `ROOT_CLAUDE_IMPORT` — `./.claude/skills` and `~/.claude/skills`, read-only (`manager.py:84-87`)

A later root never overrides a same-named skill found earlier (`manager.py:193-202`, tracked via `shadowed()`). `discover()` (`manager.py:162-215`) requires a subdirectory containing `SKILL.md`, parses frontmatter only (level-1 progressive disclosure), caches results. For a live agent, `Agent.skill_manager` (`agent.py:1121-1134`) builds with `agent_skill_dirs=[*self.SKILL_DIRS, *self._bundled_skill_dirs()]`; `_bundled_skill_dirs()` (`agent.py:1139-1175`) auto-detects a `skills/` folder beside the agent's module and beside its `gaia-agent.yaml` manifest.

### 3.2 SKILL.md structure

Documented in `docs/plans/skill-format.mdx`, implemented in `src/gaia/skills/format.py`. The Agent Skills standard (agentskills.io / Claude Code) plus a GAIA extension:
- Required: `name` (≤64 chars, `^[a-z0-9]+(-[a-z0-9]+)*$`, must equal directory name — `format.py:50,623-631`), `description` (≤1024 chars, the trigger signal).
- Optional: `license`, `version` (SemVer, GAIA-only key).
- `metadata.gaia` namespace (`format.py:234-327`): `security_tier` (verified/community/experimental, default experimental), `permissions` (`<domain>:<level>[:scope]` strings), `requirements` (model/context/python/dependencies/node_dependencies/env_vars/hardware — advisory), `tools` (the skill's own `@tool` functions it provides), `tools_required` (registry tool names it consumes).
- Standard's `compatibility`/`allowed-tools`/`disallowed-tools` keys are parsed but deliberately ignored (`format.py:61`, `IGNORED_STANDARD_KEYS`) — permissions come only from `metadata.gaia`.

Verified against real examples: `hub/agents/gaia/python/gaia_agent/skills/gaia-voice/SKILL.md` (instruction-only, `security_tier: verified`, no tools/permissions) and `hub/skills/github-triage/SKILL.md` (`security_tier: community`, `permissions: [shell:execute:gh]`, `tools_required: [run_shell_command]`) — both match the documented grammar.

### 3.3 Always-on vs. on-demand

Explicit distinction in `src/gaia/skills/sets.py`: `gaia-agent.yaml` declares `skills:` (always-on, loaded every launch) and `skill_sets:` (named, mutually-exclusive bundles, exactly one active per launch, chosen by `SkillSets.resolve()`, `sets.py:127-181`, precedence explicit request → agent selector hook → `default_skill_set`). The flagship's manifest (`hub/agents/gaia/python/gaia-agent.yaml:69-84`) declares `skills: [gaia-voice]` as always-on (`Agent.AUTOLOAD_DECLARED_SKILLS: ClassVar[bool] = True`, `agent.py:522`, triggers `load_declared_skills()` at end of `__init__`, `agent.py:761-762`), while its `skill_sets:`/`default_skill_set:` block is commented out — no task-bundle loads by default. A third activation path exists: mid-session on-demand via agent-callable `load_skill`/`unload_skill` tools (§3.6).

### 3.4 Security tiers / permission gating

Two chained gates:
- **Install-time tier ceiling** (`tiers.py`): `verified` (AMD-signed/audited, no ceiling restriction), `community` (publisher-signed, dangerous grants prompt, ceiling = `{network:read, network:write, mcp:connect, shell:execute}`), `experimental` (unsigned, ceiling = `{network:read}` only). `effective_tier()` (`tiers.py:112-121`) = min(claimed tier, cryptographically attested tier) — an unsigned skill can never claim `verified`. `enforce_tier_ceiling()` (`tiers.py:133-172`) refuses install if any declared permission exceeds the ceiling. `DANGEROUS_GRANTS` (`tiers.py:69-78`) = `shell:execute`, `desktop:control`, `database:write`, `network:write`.
- **Permission bridge/refusal** (`permissions.py`): `refuse_unbridged_permissions()` (`:192-233`) is "the single chokepoint" — install, publish, migrate, `register_skill_tools`, and `Agent.load_skill` all funnel through it. Only `network`/`mcp` (connector-bridged) and `shell:execute:<binary>` (policy-gated binary bridge, `binaries.py`) can be enforced at runtime; `filesystem`/`database`/`desktop`/`env`/bare `shell:execute` have no runtime sandbox in Phase 1 and are refused outright rather than loaded unenforced (`permissions.py:71-73`, `LOCAL_CAPABILITY_DOMAINS`).
- `register_skill_tools()` (`loader.py:59-62`) re-runs the refusal check at the actual import/execution point — "not only in `Agent.load_skill`."
- Flagship's `skill_tools.py` mixin adds a further layer: `install_skill` always passes a `confirm=` callback returning `False` (`skill_tools.py:358-365`, "never grant a dangerous permission on the model's say-so"), refuses installing an `experimental` skill (must use CLI `--allow-experimental`), and `load_skill` refuses code-bearing skills from the `.claude/skills` read-only root because that root was never signature/tier/audit-checked (`skill_tools.py:126-157`, `_refuse_ungated_code`).

### 3.5 User-editable vs. agent-editable

Humans author/edit via `gaia skill {create|import|export|migrate|audit|publish}` (`src/gaia/skills/cli.py`) — pure filesystem/CLI, no agent involvement. The agent never writes a `SKILL.md`/`tools.py` anywhere in the codebase (confirmed by grep, §3.6). The agent *can* call `install_skill`/`remove_skill` (writing/deleting an already-published skill's files under `~/.gaia/skills` — `skill_tools.py:319-436`), gated by `CONFIRMATION_REQUIRED_TOOLS = frozenset({"install_skill", "remove_skill"})` (`hub/agents/gaia/python/gaia_agent/agent.py:166-168`) and user approval — but this installs a pre-existing hub-published skill, not original authored content.

### 3.6 Can the agent autonomously write a NEW skill? — **No**

Grep of `src/` and `hub/` for `create_skill|write_skill|register_skill|save_skill|author_skill|generate_skill` finds no agent-callable tool. `SkillLibraryToolsMixin.SKILL_LIBRARY_TOOL_NAMES` (`skill_tools.py:64-72`) is the flagship's complete, exhaustive skill-tool set: `list_skills`, `search_skill_hub`, `install_skill`, `remove_skill`, `load_skill`, `unload_skill`, `skill_status` — discovery, hub-install, session-activation only; no authoring verb. Skill *creation* exists only as `gaia skill create <name>` (`cli.py:114-133`, `_handle_create`, `cli.py:532-577`) — a human-run CLI command, not an `@tool` reachable from an agent's loop. The skill-format doc itself lists autonomous skill-writing as future work: `docs/plans/skill-format.mdx:44-47` names issue **#887** ("skill auto-synthesis") and **#553** ("self-improving agent") as not-yet-built consumers that would emit SKILL.md files — the codebase's own roadmap confirms this doesn't exist today. (Note: #887 is the same issue number as `skill-synthesis.mdx`, whose *procedural-memory* mechanism, per §2.2/2.3, IS implemented — but that pipeline produces database rows in the `procedures` table, not `SKILL.md` files on disk; it does not feed the skills-directory discovery mechanism in this section at all. These are two structurally separate "skill" concepts sharing a name.)

---

## 4. Tool mixin registry

**File:** `src/gaia/agents/registry.py`

### 4.1 `KNOWN_TOOLS` (registry.py:40-52)
```python
KNOWN_TOOLS: Dict[str, tuple] = {
    "rag":         ("gaia.agents.tools.rag_tools", "RAGToolsMixin"),
    "code_index":  ("gaia.agents.tools.code_index_tools", "CodeIndexToolsMixin"),
    "file_search": ("gaia.agents.tools.file_tools", "FileSearchToolsMixin"),
    "file_io":     ("gaia.agents.tools.file_io_tools", "FileIOToolsMixin"),
    "shell":       ("gaia.agents.tools.shell_tools", "ShellToolsMixin"),
    "screenshot":  ("gaia.agents.tools.screenshot_tools", "ScreenshotToolsMixin"),
    "filesystem":  ("gaia.agents.tools.filesystem_tools", "FileSystemToolsMixin"),
    "scratchpad":  ("gaia.agents.tools.scratchpad_tools", "ScratchpadToolsMixin"),
    "browser":     ("gaia.agents.tools.browser_tools", "BrowserToolsMixin"),
    "sd":          ("gaia.sd.mixin", "SDToolsMixin"),
    "vlm":         ("gaia.vlm.mixin", "VLMToolsMixin"),
}
```
Comment (`registry.py:37-39`): tool name → `(module_path, class_name)` for lazy import, consumed by `BuilderAgent`'s template (`src/gaia/agents/builder/template.py`) to scaffold mixin imports for new agents.

### 4.2 Purpose per mixin (from each mixin's own module docstring)

| Tool | File | Purpose (verbatim/paraphrased from source docstring) |
|---|---|---|
| `rag` | `agents/tools/rag_tools.py:4,6` | Document retrieval, querying, and evaluation tools |
| `code_index` | `agents/tools/code_index_tools.py:4-6` | Exposes CodeIndexSDK ops as tools: `index_codebase`, `search_code_index`, `get_index_status`, `clear_code_index` |
| `file_search` | `agents/tools/file_tools.py:4,6` | Common file search/read ops shared across agents |
| `file_io` | `agents/tools/file_io_tools.py:5,7-8` | File I/O ops (read/write/edit) for code agents |
| `shell` | `agents/tools/shell_tools.py:4,6` | Shell command execution for file ops and system queries |
| `screenshot` | `agents/tools/screenshot_tools.py:3` | Cross-platform screenshot capture |
| `filesystem` | `agents/tools/filesystem_tools.py:6,8-9` | Browsing, search, tree visualization, file info, bookmarks, enhanced reading |
| `scratchpad` | `agents/tools/scratchpad_tools.py:6,8-11` | SQLite working-memory tools for structured multi-document analysis (financial, tax, research) |
| `browser` | `agents/tools/browser_tools.py:6,8-10` | requests+BeautifulSoup web fetch/search/download (no Playwright/browser binaries) |
| `sd` | `sd/mixin.py:2,4-6` | Stable Diffusion image gen via Lemonade SD endpoint; 4 models (SD-Turbo default, SDXL-Turbo, SD-1.5, SDXL-Base-1.0) on Ryzen AI |
| `vlm` | `vlm/mixin.py:5,7-9` | Generic VLM: image description/analysis, Q&A about images |

### 4.3 Cross-check vs. CLAUDE.md table — **no discrepancies**

All 11 entries match CLAUDE.md's table exactly on tool name, module path, class name. Purposes are consistent (CLAUDE.md's phrasing is a compressed paraphrase of each mixin's own docstring, not a contradiction).

---

## 5. Flagship agent

**Files:** `hub/agents/gaia/python/gaia_agent/agent.py`, `skill_tools.py`; `hub/agents/chat/python/gaia_agent_chat/agent.py`, `profiles.py`; `hub/agents/gaia/python/gaia-agent.yaml`.

### 5.1 What the flagship adds over ChatAgent

`GaiaAgent(SkillLibraryToolsMixin, ChatAgent)` (`agent.py:158`) — composition, not a fork. Adds: (a) the skill-library tools mixin (7 tools, §3.6); (b) a bundled `skills/` dir as highest-precedence discovery root (`agent.py:53-81`); (c) `GaiaAgentConfig` forcing `prompt_profile: str = "full"` (not ChatAgent's default `"doc"`) plus `enable_filesystem/enable_scratchpad/enable_browser = True` (`agent.py:108-155`); (d) `allowed_paths` defaulting to the user's home directory rather than cwd (`agent.py:144-155`).

**System prompt composition**: `Agent._compose_system_prompt()` (`src/gaia/agents/base/agent.py:849-908`) concatenates, in order: (1) mixin fragments auto-discovered by scanning `dir(self)` for any `get_*_system_prompt()` method (`agent.py:802-847`, `_get_mixin_prompts` — this is how `get_skills_system_prompt()`, `agent.py:1620-1638`, injects loaded-skill bodies and, per §2.3, the recalled-procedures block), (2) `_get_system_prompt()` (agent-specific — ChatAgent's profile-driven prompt blocks per `PROFILE_SPECS["full"].prompt_blocks`), (3) the `==== AVAILABLE TOOLS ====` block, (4) the JSON-envelope response-format template (non-tool-calling models only).

### 5.2 Tool loading — all registered up front, confirming GAIA_AGENT_V2_SCOPE.md §4.2

`Agent.__init__` calls `self._register_tools()` synchronously and unconditionally at construction (`agent.py:753`), before any user query. `GaiaAgent._register_tools()` (`hub/agents/gaia/python/gaia_agent/agent.py:173-181`) calls `register_skill_library_tools()` then `super()._register_tools()`. ChatAgent's `_register_tools()` (`hub/agents/chat/python/gaia_agent_chat/agent.py:1274-1296`) always registers `register_shell_tools()` + `register_memory_tools()`, then loops unconditionally over every registrar named by the profile's `tool_groups`:
```python
for _group_name in spec.tool_groups:
    for _registrar_name in TOOL_GROUP_REGISTRARS[_group_name]:
        getattr(self, _registrar_name)()
```
(`agent.py:1292-1294`). For `prompt_profile="full"` (`profiles.py:141-163`), `tool_groups = ("doc_rag", "file_fs", "data_scratch", "web_browse", "full_screenshot")` → via `TOOL_GROUP_REGISTRARS` (`profiles.py:39-62`) calls `register_rag_tools`, `register_file_tools`, `register_file_search_tools`, `register_filesystem_tools`, `register_file_io_tools`, `register_scratchpad_tools`, `register_browser_tools`, `register_screenshot_tools` — every one at `__init__` time. `Agent._snapshot_tools()` (`agent.py:988-995`) freezes the registry into the instance; `manifest.yaml`'s `tools_count: 62` (`gaia-agent.yaml:15-20`) is drift-guarded by a test asserting this exact count.

**Nuance beyond the binary confirmation**: an on-demand semantic tool selector (`ToolLoader`, issue #1449) does exist in `ChatAgent` (`agent.py:574-676`, `_maybe_build_tool_loader`/`_select_tools_for_turn`/`tool_loader.select(...)`), but `_maybe_build_tool_loader()` (`:574-592`) returns `None` unless the `dynamic_tools` toggle is on **and** `prompt_profile == "doc"` (`:583-584`: `if getattr(self.config, "prompt_profile", "full") != "doc": return None`). `GaiaAgentConfig` hardcodes `prompt_profile: str = "full"` (`agent.py:124`, comment explains this deliberately, since `"doc"` alone lacks scratchpad/browser groups) — so `self.tool_loader` is always `None` for `GaiaAgent`, `_dynamic_tools_active()` (`:639-650`) is always `False`, and `_select_tools_for_turn` always returns `None` (full registry, no filtering) — confirmed by `_compose_system_prompt`'s `tool_filter = self._active_tool_filter` branch (`src/gaia/agents/base/agent.py:884-906`), which with `filter_to=None` renders every registered tool unconditionally (`:1008-1009`). **Conclusion**: for the flagship specifically, all tools are registered up front and the one on-demand mechanism in the codebase is architecturally excluded from the profile the flagship uses (a separate `load_tools(bundle)` escape-hatch, `agent.py:1303-1343`, is gated identically and also never registers for `GaiaAgent`).

---

## 6. Autonomy-adjacent infrastructure

### 6.1 `gaia schedule`

**CLI wiring**: `src/gaia/cli.py:2096-2158` — subparser with subcommands `add` (`:2106-2126`), `list` (`:2127-2130`), `show` (`:2132-2133`), `remove` (`:2135-2136`), `pause` (`:2138-2141`), `resume` (`:2143-2146`), `run` (`:2148-2151`), `daemon` (`:2153-2156`). Dispatch: `_handle_schedule()` (`cli.py:3351-3448`), called from `cli.py:3634-3635`.

**Implementation dir**: `src/gaia/schedule/` — `store.py` (persistence), `daemon.py` (scheduler loop), `runner.py` (single-job execution), `sinks.py` (output delivery).

**What it can trigger**: only a raw prompt to a fresh `AgentSDK` session — **not** a skill, **not** a full stateful agent invocation. `runner.fire()` (`runner.py:32-47`): `sdk = AgentSDK(AgentConfig()); response = sdk.send(prompt, no_history=True)`. `resolve_input()` (`runner.py:16-29`) explicitly raises `NotImplementedError` if `schedule.skill` is set: "the scheduler is not wired to [the skills runtime] yet." Enforced even at add-time: `_handle_schedule` (`cli.py:3360-3375`) rejects `gaia schedule add --skill ...` outright, pointing to issue #1019, directing the user to `--prompt`. The `Schedule` dataclass (`store.py:26-49`) still carries a `skill` field and validates "exactly one of skill/prompt" (`:42-47`), but it's dead on the fire path.

**Cron-only, no events**: `daemon.py:45-64` (`build_scheduler`) arms one `CronTrigger.from_crontab(schedule.cron)` (APScheduler) per enabled schedule. No file-watcher, webhook, or other event source exists anywhere in `src/gaia/schedule/` (grep confirms no `watchdog`/webhook code).

**Scheduling mechanism**: APScheduler `BackgroundScheduler` + `CronTrigger`, not a custom poll loop or OS cron (`daemon.py:15-16,45-64`). `run_daemon()` (`:67-82`) starts the scheduler, installs SIGINT/SIGTERM handlers, blocks on a `threading.Event`. Each fire wraps `runner.fire(schedule)` in try/except (logs and continues on failure — one broken schedule doesn't kill the daemon), then persists `last_run`/`next_run` to `~/.gaia/schedules.toml` (`store.py:23,115-191`).

`gaia daemon` (parser `cli.py:2927-2996`, dispatch `cli.py:4844`) is a **wholly distinct** subsystem (`gaia.daemon` — sidecar-agent custody) with no code-level integration to `gaia.schedule` in either direction — confirmed as a live, acknowledged gap by the autonomy-engine.mdx reconciliation note (§6.3).

Sinks (`sinks.py`): `stdout`, `file:<path>`, `notification` (macOS `osascript`/Linux `notify-send`; explicit `NotImplementedError` on Windows, `sinks.py:76-80`), `telegram` (HTTP POST to Bot API). All fail loudly per the no-silent-fallback convention.

### 6.2 `src/gaia/governance/`

All 9 substantive files read in full: `action_mapper.py`, `adapter.py`, `checkpoint_bridge.py`, `config.py`, `decorators.py`, `exceptions.py`, `mixin.py`, `policy_binding.py`, `protocols.py`, `receipt_service.py`, `schemas.py`, `stubs.py`, `README.md`.

**What it gates today**: per-tool-call action governance, opt-in via `GovernedAgentMixin`. `GovernedAgentMixin._execute_tool()` (`mixin.py:117-174`) intercepts every tool call: resolves canonical tool name (`:178-201`) → builds an `ActionRequest` from `@govern` decorator + dict-based risk tags (`:203-221`) → `adapter.govern_action()` → `RuleBasedPolicyEngine.evaluate_action()` (`stubs.py:26-47`, the only shipped policy engine: `"blocked"` tag → BLOCK, `"review"` tag → REVIEW, else ALLOW) → `adapter.handle_transition()` (`adapter.py:218-249`) → `CONTINUE`/`TERMINATED`/`CHECKPOINT_OPEN`. ALLOW runs the tool; BLOCK short-circuits + signed JSONL receipt; REVIEW opens a checkpoint (`InMemoryCheckpointBridge`, `checkpoint_bridge.py:29-111`) and asks a `governance_reviewer` callback or `console.confirm_tool_execution` — only if the active console sets `blocking_confirmation = True` (Agent UI's SSE handler does; GAIA's CLI console does not, `mixin.py:330-336`, `README.md:96-103`). With neither wired, REVIEW **fails closed** (denied) by explicit design (`mixin.py:40-43,338-346`).

Receipts (`receipt_service.py`): `JsonlReceiptService` appends canonical-JSON-hashed records (`adapter.py:147-163`) to disk; `PolicyVersionRef`/`StaticPolicyBindingService` (`policy_binding.py`) stamp a policy version + constitution hash but the binding is hardcoded static (`version="v0"`, `constitution_hash="constitution-dev"`, `policy_binding.py:15-25`). **No budget/rate-limit/cost-cap enforcement anywhere in the module** — only ALLOW/REVIEW/BLOCK on string risk tags.

**Integration**: none in-core, by default. `Grep` for `governance`/`GovernedAgentMixin` in `src/gaia/agents/base/agent.py` returns zero matches — the base `Agent` has no knowledge of governance; it's bolted on via mixin composition only, opt-in per the module's own README (`README.md:3`, "Off by default"). Repo-wide integration points found: `src/gaia/ui/sse_translation.py:49,57,81,392` and `src/gaia/ui/sse_handler.py:1062` (UI-side display plumbing for a `policy_alert` SSE event, consumed only if the mixin is active and emits it — not itself a call into governance); `src/gaia/skills/audit_gate.py:24-25` (reuses the ALLOW/REVIEW/BLOCK vocabulary for an unrelated skills-audit verdict, not the governance engine); `examples/governed_weather_agent.py` (the only actual composition example in the repo); test files. **No hub agent or in-core agent (chat, code, docqa, email, Analyst, Browser, FileIO, etc.) mixes in `GovernedAgentMixin` in its own class definition** — governance is a library capability a developer can adopt, not something gating any shipped GAIA agent's tool calls today.

**Explicit stubs / not-yet-wired** (module's own README, `:156-163`): policy control plane is static (`StaticPolicyBindingService`); attestation/trust routing not implemented at all; precedent memory / validator marketplace not implemented; **"the mixin only intercepts tool calls today; broader workflow events will arrive in a follow-up PR"** — `WorkflowTransition`/`TransitionOutcome` schemas exist (`schemas.py:21,56-63,85-89`) but are only ever synthesized *per tool call* (`mixin.py:239-251`, one transition per invocation, `from_state="READY"` → `to_state=f"TOOL:{name}"`) — no genuine multi-step workflow state machine drives them. `RuleBasedPolicyEngine` is explicitly labeled a stub for real ACGS-lite engines (`stubs.py:3-7`). `InMemoryCheckpointBridge` is explicitly a placeholder pending "a persistent bridge backed by constitutional-swarm" (`checkpoint_bridge.py:3-8`) — checkpoints do not survive process restart.

### 6.3 `docs/plans/autonomy-engine.mdx` — full read

**Status** (`:17-28`): "Planning (0% implemented)." Milestone v0.23.0. Dated 2026-04-01. Hard prerequisite: **v0.20.0 Memory & Bootstrap must ship first** (`MemoryStore`, `MemoryMixin`). Related: Agent UI Phase C, Messaging Integrations, Security Model.

**Live architecture-conflict banner** (`:9-15`, added after the fact): states this doc's proposed "always-on background service" **is** the Agent UI v2 "headless custody daemon" and explicitly warns "don't build a second one" — the daemon is said to own "the single scheduler clock" (§0.22), with jobs firing into an owning agent sidecar via `/query` rather than an in-process loop. **The body of the document below still describes the older, separate-process design.** This is the exact gap §6.1 observed empirically: `gaia daemon` and `gaia schedule daemon` are today two unrelated code paths — precisely what the reconciliation note says must not remain the end state.

**1. Triggers proposed**:
- **Heartbeat Scheduler**: cron-based, config at `~/.gaia/heartbeat.yaml`, parsed with `croniter` — note this is a *different* library from the shipped `gaia schedule`'s APScheduler. Example tasks: `"*/30 * * * *"` project watcher, `"0 9 * * 1-5"` system health.
- **Event Hooks** (§6): (a) filesystem triggers via `watchdog` as `event_hooks` entries in `heartbeat.yaml` (e.g. watch `~/Downloads` for new PDFs → auto-RAG-index; watch `~/.ssh` for changes → alert); (b) localhost-only webhook receiver, HMAC/token-validated; (c) system event triggers (login, network-connected, USB attach, screen unlock) via native platform APIs (Task Scheduler/WMI on Windows, systemd/D-Bus on Linux, launchd/IOKit on macOS) — doc explicitly scopes Phase 1 to login + network-connected only, others named "stretch goals."
- **Self-scheduling**: agent creates one-shot follow-ups via a `schedule_followup` tool inside a THINK→ACT→SCHEDULE→COMPLETE cycle (§5).

None of file-watcher/webhook/system-event triggers exist in the shipped `gaia schedule` (confirmed cron-only, §6.1) — wholly unbuilt.

**2. Proposed in-flight-task state**: No formal named state-machine enum for tasks is given. What is specified:
- **Three-tier cost-aware escalation per run** (§4): **Tier 0 — Deterministic** (zero-cost: git status, file diff, disk space, watched-dir stat deltas; stops if nothing found); **Tier 1 — Triage** (`Qwen3-0.6B-GGUF`, 50-200 tokens, binary "worth notifying? YES/NO" — stops if NO); **Tier 2 — Action** (`Qwen3.5-35B-A3B-GGUF`, 500-5000 tokens, full new chat session with tools; notifies + writes activity feed).
- One-shot self-scheduled follow-ups persisted as **JSON files** in `~/.gaia/autonomy/tasks/pending/` (§5, `:210-211`) — the closest thing to a task-record schema, but field names aren't specified (no code sketch).
- **Activity log**: SQLite DB at `~/.gaia/autonomy/`, surfaced as an Electron sidebar timeline (§10, "Activity feed"); file layout note (§3, `:106-107`): PID file, rotating log, SQLite activity DB, pending/results task directories.
- **Self-scheduling constraints** (§5, `:203-208`): max 24-hour follow-up delay (longer belongs in recurring `heartbeat.yaml` config); max 10 pending follow-ups at once; each follow-up fires at most once (one-shot, not recurring); follow-ups inherit the creating session's dangerous-mode setting.

**3. Resumption after interruption**: The only concrete mechanism is the pending-tasks JSON directory — one-shot tasks persist as JSON "so they survive engine restarts" (§5, `:210-211`), plus a bare "results" directory noted in the file layout. **No checkpointing of already-started/in-progress work; no described recovery of a task that crashed mid-Tier-2-execution; silent on network-loss recovery.** The doc only guarantees not-yet-fired scheduled work survives restart — meaningfully thinner than "checkpoint an in-flight agentic loop and resume it."

**4. Explicit scope boundaries / deferrals**:
- The reconciliation banner itself is a deferral (don't build a second scheduler clock — defer ownership to the Agent UI v2 daemon).
- §14 "Open Questions" — five explicitly unresolved points: (1) whether the 0.6B triage model stays warm vs. loads on demand; (2) whether different heartbeat tasks can invoke *different* agents (CodeAgent for repos vs. GaiaAgent for email) — multi-agent dispatch unresolved; (3) depth of Memory-system integration (findings→memory write / memory→findings read / both — undecided); (4) whether notifications route through messaging adapters (Telegram/Discord) in addition to desktop — undecided; (5) rate-limiting strategy for MCP calls hitting external APIs (calendar/email) to avoid quota exhaustion — undecided, no algorithm proposed.
- Phase-1 cuts named explicitly: system-event triggers limited to login + network-connected, others "stretch goals" (§6).
- Security detail explicitly punted: "For comprehensive security analysis, see security-model.mdx" (§9, `:289`) — this doc only sketches Safe Mode (all writes need Accept/Reject/Review, agent cannot self-schedule around confirmation) vs. Dangerous Mode (opt-in, off by default, per-task, session-scoped, logged) at a policy-statement level, no enforcement mechanism spec.

**5. Concrete data models / API sketches** (reproduced structurally):
- `~/.gaia/heartbeat.yaml` full example (§4, `:118-154`): `enabled` (bool, default false), `triage_model`, `action_model`, `notifications` (bool), `max_concurrent_tasks` (int), `quiet_hours: {enabled, start, end}`, `tasks:` (list of `{name, schedule (cron), enabled, checks: [...], watch_paths: [...]}`), `resource_limits: {max_cpu_percent, defer_if_user_active}`.
- Dangerous-mode task variant (§9, `:279-287`) adds `dangerous: true` on a per-task entry.
- Named built-in check functions (§4, `:160-165`): deterministic — `check_git_changes`, `check_new_files`, `check_disk_space`, `check_watched_dirs`; MCP-based zero-cost — `check_calendar`, `check_email`; LLM-backed — `summarize_if_needed`, `daily_summary`, `generate_daily_brief`.
- ASCII architecture diagram (§3, `:72-100`): `GAIA Autonomy Engine (background proc)` → `Heartbeat Scheduler` + `Event Hooks (fs, webhooks)` → `Task Executor` (Tier 0/1/2 labeled) → `Session Manager` ("new sessions, never interrupts active ones") → fanning out to `Notifications (desktop)`, `Activity Log (SQLite)`, `Electron UI (activity feed)`.
- `PlatformService` interface sketch (§11, `:315-317`): `install()`, `uninstall()`, `start()`, `stop()`, `is_running()`, per-OS backends (Windows Registry Run key/Task Scheduler; Linux systemd user service `~/.config/systemd/user/gaia-autonomy.service`; macOS launchd plist `~/Library/LaunchAgents/ai.amd.gaia.autonomy.plist`).
- CLI surface sketch (§12, `:323-338`) — **entirely unimplemented**; none of these exist in `src/gaia/cli.py` today (the shipped surface is `gaia schedule ...`, not `gaia autonomy ...`): `gaia autonomy start/stop/status/restart`, `heartbeat list/run/pause/resume`, `log [--all] [--json]`, `schedule "<text>" --delay 30m`, `schedule "<text>" --at "17:00"`, `tasks`, `cancel <task_id>`.

**6. Dependency graph**: hard prerequisite v0.20.0 Memory & Bootstrap (`MemoryStore`/`MemoryMixin`) must ship first (`:23`); soft dependencies on Agent UI Phase C, Messaging Integrations, Security Model (`:25-28`). Issue cross-reference table (§2, `:56-66`): #634 (core engine), #550 (scheduler — marked **"shipped"**, referring to a *different* Agent UI Schedule Manager with NL intervals and `/api/schedules`, not the CLI `gaia schedule` command), #555/#557 (autonomous loop), #558 (activity feed), #559 (dangerous mode), #560 (one-shot delayed execution), #643 and #415-#424 (system tray app).

**Cross-cutting fact**: the shipped `gaia schedule` and this doc's Heartbeat Scheduler are materially different systems — shipped is APScheduler + TOML store + cron-only + prompt-only dispatch, no tiered escalation, no event hooks, no activity DB, no dangerous-mode distinction, explicitly cannot run skills. The doc's proposal is a superset that is 0% implemented per its own status line, and its own reconciliation banner concedes the design as written is already stale relative to the Agent UI v2 daemon architecture.

---

## 7. Voice

**Files:** `src/gaia/talk/` (`sdk.py` 541 lines, `app.py`), `src/gaia/audio/` (`audio_client.py` 532 lines, `whisper_asr.py` 428 lines, `kokoro_tts.py` 614 lines, `audio_recorder.py`).

### 7.1 ASR/TTS mechanism — **not Lemonade-served, refuting GAIA_AGENT_V2_SCOPE.md's premise**

`GAIA_AGENT_V2_SCOPE.md:83` states "Whisper (ASR) and Kokoro (TTS) are already Lemonade-served." **This is factually incorrect for the current codebase:**
- **ASR**: `whisper_asr.py:24` imports the local `openai-whisper` package directly (`import whisper`); loaded locally at `:76` (`whisper.load_model(model_size)`); transcription local at `:220-232` and `:270` (`self.model.transcribe(...)`). No HTTP call, no Lemonade client, anywhere in the file.
- **TTS**: `kokoro_tts.py:22` imports the local `kokoro` package (`from kokoro import KPipeline`); instantiated locally at `:55`; synthesis local at `:271`/`:290` (`self.pipeline(chunk_text, voice=..., speed=1)`). No HTTP call to Lemonade.
- `grep -i lemonade` over `src/gaia/audio/` → zero matches. Over `src/gaia/talk/` → two matches, neither ASR/TTS-related: `talk/sdk.py:16` imports `DEFAULT_MODEL_NAME` from `lemonade_client.py` (just the default text-model name string for the chat leg of TalkSDK); `talk/README.md:49` — "Local LLM server (Lemonade) running OR OpenAI API key" (Lemonade used only for text generation via `gaia.llm.create_client`, `audio_client.py:9,71-75` — never for audio).
- `lemonade_client.py:253-254` declares `ModelType.ASR = "asr"` / `ModelType.TTS = "tts"` as enum entries alongside `LLM`/`EMBEDDING`/`VLM` — but a repo-wide grep for usage of these two values returns **zero matches**; declared and never referenced. `lemonade_client.py:2602` mentions `'whispercpp'` only as an example backend-recipe name in a docstring for `get_recipe_status()`. **There is no Lemonade HTTP endpoint for speech that GAIA's audio code calls.**
- Internal inconsistency corroborating the gap: `hub/agents/chat/python/gaia_agent_chat/agent.py:1845` — *"Phase 5a (voice input) OMITTED: WhisperASR requires Lemonade server ASR endpoint."* That endpoint doesn't exist in `lemonade_client.py`, and `WhisperAsr` itself doesn't need one (needs `torch`+`openai-whisper`+`sounddevice` locally). Both this comment and the scope doc assert a Lemonade-audio integration that isn't implemented anywhere.

### 7.2 Reachability from flagship/TUI — nuanced, partially refutes GAIA_AGENT_V2_SCOPE.md §4.3

Every non-test external importer of `gaia.talk`/`gaia.audio`, found by repo-wide grep:

| File:line | Imports | Context |
|---|---|---|
| `cli.py:901` | `TalkConfig, TalkSDK` | `gaia talk` CLI subcommand (`cli.py:899-929`) — builds config, runs `talk_sdk.start_voice_session()` (full duplex voice loop) |
| `cli.py:4070` | `KokoroTTS` | `gaia test tts-*` (`:4066-4101`) — manual TTS smoke test |
| `cli.py:4105` | `WhisperAsr` | `gaia test asr-*` (`:4103-4162`) — manual ASR smoke test |
| `cli.py:4165` | `AudioRecorder` | `gaia test asr-list-audio-devices` (`:4164-4175`) |
| `hub/agents/chat/python/gaia_agent_chat/agent.py:1872` | `KokoroTTS` | Inside the `text_to_speech` `@tool` (`:1847-1903`), registered by `ChatAgent._register_tools()` |

**The full voice session (`TalkSDK`/`AudioClient`, mic-in via Whisper + streamed TTS-out via Kokoro) is confirmed unreachable from the flagship agent or the TUI.** Its only non-test importer is `cli.py:901`, a standalone CLI entry point. Neither `hub/agents/gaia/python/gaia_agent/` nor `tui/` import `gaia.talk` or `gaia.audio.audio_client`/`whisper_asr` anywhere. `tui/` (191 Go files) has zero references to audio/voice/tts/speech/whisper/kokoro/talk (confirmed by grep — only false-positive substring hits on "invoice"). `hub/agents/gaia/python/gaia_agent/server.py` (the HTTP/SSE server the TUI talks to) likewise has zero audio references — no voice endpoint or audio streaming/playback support on either side of the TUI↔server boundary.

**However, TTS output alone (one-way, no ASR) IS reachable from the flagship**, via a separate, undocumented path bypassing `TalkSDK`/`AudioClient` entirely: `ChatAgent._register_tools()` registers a `text_to_speech` tool (`gaia_agent_chat/agent.py:1847-1903`, Phase 5b) that directly imports `KokoroTTS` and calls `.generate_speech(text)` (`:1872,1875`), writing a WAV file to `~/.gaia/tts/`. This registration is unconditional for any prompt profile except the early-return `"chat"` profile — the Phase 4/Phase 5b blocks sit as sibling statements at the same indentation as the top-level `if spec.web_tools:` gate, not nested inside a profile conditional; skipped only if `spec.early_return` is true (`:1282-1286`). `GaiaAgent` subclasses `ChatAgent` with `prompt_profile` defaulting to `"full"` (`gaia_agent/agent.py:124`, comment calls this "the load-bearing line of the whole package") and its `_register_tools()` (`:173-181`) calls `super()._register_tools()` unmodified — inheriting `text_to_speech`. **Net effect**: the flagship has a real, always-on `text_to_speech` tool the LLM can invoke mid-session, but since the TUI has no audio playback code (confirmed above), the practical effect is a WAV file written to disk, not an audible response in the TUI. **Voice input (ASR) has no equivalent path** — explicitly omitted per the comment above; no file under `hub/agents/gaia/` or `hub/agents/chat/` imports `WhisperAsr` or `AudioRecorder`.

**Correction to GAIA_AGENT_V2_SCOPE.md §4.3**: "already Lemonade-served" is incorrect (§7.1). "Not reachable from the flagship or the TUI" should be narrowed: the *full voice session* is correctly unreachable (exists only as the standalone `gaia talk`/`gaia test tts-*`/`asr-*` CLI commands), but *TTS output alone* is already reachable from the flagship via the inherited `text_to_speech` tool — a fact the scope doc does not capture, and which has no matching TUI-side playback capability to make it audible to the user.
