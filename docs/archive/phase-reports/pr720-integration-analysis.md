# PR #720 Integration Analysis — feature/pipeline-orchestration-v1

**PR:** amd/gaia#720 — feat: agent registry with per-session agent selection
**Author:** itomek (Tomasz Iniewicz)
**Branch analyzed:** feature/pipeline-orchestration-v1
**Analysis date:** 2026-04-07
**Analyst:** planning-analysis-strategist (automated pipeline)

---

## 1. PR #720 Summary

PR #720 delivers the first end-user-facing agent selection system for GAIA. Its scope is the Agent UI and the runtime layer that backs it. It does not touch the pipeline engine.

### Delivered components

**AgentRegistry** (`src/gaia/agents/registry.py` — 635 lines, new file on main)
Discovers agents at server startup from three sources: built-in Python agents (chat, gaia, builder), custom Python modules placed in `~/.gaia/agents/*/agent.py`, and YAML manifests at `~/.gaia/agents/*/agent.yaml`. The registry stores `AgentRegistration` dataclasses (id, name, description, source, factory callable, models list, conversation starters). Uses a threading lock for safety. Manifests are validated by a Pydantic v2 `AgentManifest` model. Uses `gaia.logger` (not our `gaia.utils.logging`).

**GaiaAgent** (`src/gaia/agents/gaia/agent.py` — new)
Lightweight built-in agent with a ~797-token system prompt versus ChatAgent's ~5,760-token prompt. Delivers warmup reduction from 249s to 34s and TTFT reduction from 21s to 2.9s on AMD iGPU hardware. Mixes in RAGToolsMixin, FileSearchToolsMixin, and MCPClientMixin.

**BuilderAgent** (`src/gaia/agents/builder/agent.py` — new)
Hidden built-in agent (excluded from the UI selector list) that scaffolds YAML manifests for custom agents via the "+" button in the Agent UI.

**YAML manifest agent support**
Manifests support: id, name, description, instructions (system prompt), tools (subset of seven known tools: rag, file_search, file_io, shell, screenshot, sd, vlm), models (ordered preference list), conversation_starters, and mcp_servers. Manifests are validated; invalid tool names raise at load time.

**Per-session agent routing**
`src/gaia/ui/database.py` is migrated to add an `agent_type` column to the sessions table (default: `'chat'`). `_chat_helpers.py` cache keying is extended to include `agent_type`. The session create endpoint accepts `agent_type`. The chat dispatch path instantiates the correct registered agent on cache miss.

**Agent selector UI**
Moved from the sidebar to the chat input area, matching the Perplexity / Claude Code interaction pattern. `src/gaia/apps/webui/src/` has changes in ChatView, WelcomeScreen, chatStore, types/index.ts, and services/api.ts.

**`src/gaia/ui/routers/agents.py`** — new REST endpoint: `GET /api/agents` and `GET /api/agents/{id}`.

**`src/gaia/ui/dispatch.py`** — new DispatchQueue (185 lines) for async task management.

**Bug fixes**
- `_response_format_template` init ordering in `base/agent.py` — the template is now set before `_register_tools()` is called, preventing the UnboundLocalError when `load_mcp_servers_from_config` triggers `rebuild_system_prompt` mid-init.
- JSON envelope stripping on LLM responses.
- Registry kwargs cleanup preventing unknown kwargs from propagating to agent constructors.

**Issues closed:** #612 (custom agent support), #713 (agent selector UX).

**Files changed:** 38 files, 3,808 additions, 129 deletions.

---

## 2. Overlap Surface — Files Changed in Both

The following files are modified by PR #720 on upstream main AND exist in modified form on our branch.

| File | PR #720 change | Our branch change | Relationship | Merge risk |
|---|---|---|---|---|
| `src/gaia/agents/registry.py` | **New file** (635 lines). UI-focused: three-source discovery (builtin Python agents, custom Python, YAML manifests), `AgentRegistration` dataclass with factory callable, threading lock, Pydantic manifest validation, `gaia.logger`. No async, no hot-reload, no capability index, no keyword scoring. | **New file** (our version, 600+ lines). Pipeline-focused: YAML-only discovery from `config/agents/`, async lock, hot-reload via watchdog, `AgentDefinition`/`AgentCapabilities`/`AgentTriggers` dataclasses imported from `base/context.py`, capability index, keyword-trigger scoring, phase-filter, complexity-filter, `select_agent()` routing method. Imports from `gaia.pipeline.defect_types` and `gaia.exceptions`. | **CONFLICT — mutual new file.** Both branches created `registry.py` from scratch with incompatible designs. PR #720's version is simpler and UI-serving. Ours is pipeline-serving and architecturally richer. These cannot be auto-merged. A deliberate reconciliation is required. | **HIGH** |
| `src/gaia/agents/base/agent.py` | Reorders `_response_format_template` assignment to occur before `_register_tools()` call. This is a targeted 8-line fix for the UnboundLocalError. No new public API. | We extended `agent.py` to add `agent_id` property, context integration hooks for NexusService, pipeline-aware state propagation, and LLM output forwarding to the state machine. Our changes are additive, not structural. | **COMPLEMENT with absorption.** PR #720's `_response_format_template` fix is a genuine bug fix we should absorb. Our additions are orthogonal. The fix does not conflict with our additions but must be reconciled manually because both branches modified the same file at overlapping line ranges (around `__init__`). | **MEDIUM** |
| `src/gaia/ui/database.py` | Adds `agent_type` column migration to sessions table. Adds `agent_type` parameter to `create_session()`. | Our branch extended `database.py` with modular router architecture, SSE improvements, document upload TOCTOU fix, and LRU eviction. We did not add `agent_type`. | **COMPLEMENT.** No logical conflict. The `agent_type` migration adds a new column and does not touch our changes. Standard three-way merge should handle this with minor manual review at `create_session()` signature. | **LOW** |
| `src/gaia/ui/_chat_helpers.py` | Adds `_agent_registry` module-level variable, `set_agent_registry()`, `get_agent_registry()`. Extends cache keying to include `agent_type`. Renames `_model_load_lock` to `model_load_lock` (makes it public). Extends `_get_cached_agent()` signature with `agent_type`. | Our branch modified `_chat_helpers.py` for SSE streaming improvements, session management changes, and performance improvements. We did not touch the cache key structure or add `agent_type`. | **COMPLEMENT with one naming hazard.** The rename of `_model_load_lock` to `model_load_lock` (removing underscore) is a public API change. If any of our code references `_model_load_lock` by the private name, it will break at runtime after merge. Must verify. | **MEDIUM** |
| `src/gaia/ui/routers/sessions.py` | Adds `agent_type=request.agent_type` to the `create_session()` call. | Our branch added modular router architecture, including a restructured `sessions.py`. | **COMPLEMENT.** Both changes to `sessions.py` are additive. The `agent_type` field threaded through from the request model is a one-line addition to the create-session path that our modular refactor should accommodate. | **LOW** |
| `src/gaia/apps/webui/src/` (5 files) | Moves agent selector from sidebar to chat input area. Adds `agentType` to chatStore, `AgentInfo` to types, and `fetchAgents()` to api.ts. Updates ChatView and WelcomeScreen. | Our branch added extensive frontend changes: terminal animations, device-unsupported guard, privacy-first UI patterns, and accessibility improvements. | **COMPLEMENT.** Different UI areas. The agent selector lives in the input footer; our changes concentrated on message display, device guards, and terminal animations. However, ChatView changes in both require manual diff review — both PRs touch ChatView.tsx. | **MEDIUM** |
| `src/gaia/ui/server.py` | Adds registry discovery to server lifespan, mounts `agents` router, stores `agent_registry` on `app.state`. | Our branch restructured `server.py` to use modular router mounts and added the Agent UI backend improvements. | **COMPLEMENT with wiring conflict.** Both add router mounts to `server.py`. The exact insertion point and import list will conflict at merge. Requires manual resolution but logic is compatible. | **MEDIUM** |
| `src/gaia/ui/models.py` | Adds `AgentInfo`, `AgentListResponse`, `InitTaskInfo`, `TaskListResponse`, `TaskResponse` Pydantic models. Extends session creation request model with `agent_type`. | Our branch extended `models.py` with additional response models for our modular router endpoints. | **COMPLEMENT.** Additive changes on both sides. The `agent_type` field on the session create request model is a new field with a default, so our existing callers are not broken. | **LOW** |

### Files changed in PR #720 not present on our branch

| File | PR #720 introduces | Impact on our branch |
|---|---|---|
| `src/gaia/agents/gaia/agent.py` | GaiaAgent (new built-in) | None during merge. We will want to wire it as a pipeline-capable fast-warmup option post-merge. |
| `src/gaia/agents/builder/agent.py` | BuilderAgent scaffold tool | None during merge. Orthogonal to pipeline work. |
| `src/gaia/ui/dispatch.py` | DispatchQueue for async task mgmt | Potentially useful for pipeline job dispatch in the UI. |
| `docs/guides/custom-agent.mdx` | User guide for custom agents | Additive, no conflict. |
| `setup.py` | Minor update | Verify no dependency additions conflict with our `pyproject.toml`. |

---

## 3. Open Items Assessment

### 3.1 Open Item 1 — AgentOrchestrator / RoutingAgent Hardcoding

**Current status:** `RoutingAgent` (`src/gaia/agents/routing/agent.py`) was modified on our branch to accept capability-based routing requests, but it still defaults to CodeAgent when no explicit agent is matched. The `AgentOrchestrator` that was intended to provide dynamic agent selection was never implemented.

**How PR #720 affects it:** PR #720 delivers a working `AgentRegistry.discover()` + `AgentRegistry.get()` pattern that already solves the discovery half of this problem for the UI. However, PR #720's registry design is UI-serving (returns registered agent factories for on-demand instantiation) while our registry is pipeline-serving (implements `select_agent()` with phase, complexity, and keyword filtering). These are complementary functions, not substitutes.

The more important finding: PR #720's `RoutingAgent` is a different code path from ours. PR #720 does not touch `src/gaia/agents/routing/agent.py`. Our open item therefore remains unaddressed by PR #720.

**Recommended action:** This item is not resolved by PR #720. After merge, use PR #720's AgentRegistry as the discovery layer and implement `AgentOrchestrator` as a thin adapter that calls `our_registry.select_agent()` to pick an agent, then calls `pr720_registry.get(agent_id).factory(...)` to instantiate it. This bridges the two registry designs without duplicating logic. Priority: HIGH before declaring the pipeline routing feature complete.

---

### 3.2 Open Item 2 — BAIBEL Phase 4 (Adaptive Learning)

**Current status:** Deferred. Phases 0–3 are complete. Phase 4 (Adaptive Learning) has not been started.

**How PR #720 affects it:** No impact. PR #720 does not touch `src/gaia/state/nexus.py`, the BAIBEL integration code, or any file in `docs/spec/baibel-gaia-integration-master.md`. The BAIBEL workstream is entirely orthogonal to the agent registry / UI selection work in PR #720.

**Recommended action:** Maintain deferral status. Track as a post-merge issue on a dedicated branch. No coordination with itomek required.

---

### 3.3 Open Item 3 — Resilience Primitives Not Wired into Pipeline Engine

**Current status:** `CircuitBreaker`, `Bulkhead`, and `Retry` exist as standalone modules in `src/gaia/resilience/` with full test coverage. They are not imported or invoked in `engine.py`, `loop_manager.py`, or `routing_engine.py`.

**How PR #720 affects it:** No direct impact. PR #720 does not touch `src/gaia/resilience/`, `src/gaia/pipeline/engine.py`, or any pipeline component. However, PR #720 introduces `dispatch.py` (a DispatchQueue for async task management in the UI backend) which demonstrates the team's approach to bounded async execution. This pattern is worth consulting when designing the engine-side wrapping.

**Indirect risk from PR #720:** After PR #720 merges, the `AgentRegistry.discover()` is called at server startup and agent factories are invoked on demand. If we wire resilience primitives around agent calls in the pipeline engine *after* PR #720 merges, we must ensure that the resilience wrappers are compatible with both the synchronous factory pattern from PR #720 and the async pipeline execution model. There is no conflict today, but the design decision should be made before wiring begins.

**Recommended action:** Wire resilience primitives into `engine.py` and `loop_manager.py` as a dedicated task. This is independent of PR #720 and can proceed in parallel. Target the three agent invocation call sites in `routing_engine.py`'s `route()` method. Priority: MEDIUM before merge to main.

---

### 3.4 Open Item 4 — Capability Vocabulary Not Standardized

**Current status:** The 18 YAML files in `config/agents/` use a `capabilities` list field that works with our `AgentRegistry` and `AgentCapabilities` system. However, the vocabulary (e.g., `requirements-analysis`, `full-stack-development`, `code-review`) is not cross-referenced against a canonical vocabulary definition in `src/gaia/core/capabilities.py`. Some files use freeform strings, which means the registry's capability index may fail to match routing requests that use different string spellings.

**How PR #720 affects it:** PR #720 introduces a second, parallel capability vocabulary for the UI-side registry. Its `AgentManifest` model uses a `tools` field (with a closed enum: `rag`, `file_search`, `file_io`, `shell`, `screenshot`, `sd`, `vlm`) rather than a capabilities field. These are functional tool bindings, not semantic capability descriptors. They are a different concept from our `AgentCapabilities`.

The risk is vocabulary proliferation: after PR #720 merges, there will be three capability-like fields in play:
1. Our `config/agents/*.yaml` `capabilities` list (pipeline routing)
2. PR #720's `AgentManifest.tools` field (UI tool binding)
3. `src/gaia/core/capabilities.py` formal vocabulary (partially implemented)

This makes standardization harder, not easier, because there is now a third concept that users and developers must distinguish.

**Recommended action:** Before merging our branch to main, standardize the 18 YAML files in `config/agents/` against the vocabulary defined in `src/gaia/core/capabilities.py`. This is a purely mechanical task. Additionally, open a coordination issue with itomek to ensure that future YAML agent manifests (PR #720 style) can optionally declare pipeline-style capabilities so that the pipeline registry can also discover user-defined agents placed in `~/.gaia/agents/`. Priority: HIGH — this blocks full pipeline routing functionality.

---

### 3.5 Open Item 5 — Spec Document Coherence Check

**Current status:** An interrupted quality review of specification documents was underway. The review was checking that code examples match implementation and that cross-document references are internally consistent. The check was not completed.

**How PR #720 affects it:** PR #720 adds `docs/guides/custom-agent.mdx` — a new user-facing guide with code examples for both YAML manifest authoring and Python agent subclassing. This guide is external to our spec documents, but the Python agent example in the guide (`from gaia.agents.base.agent import Agent`, `_TOOL_REGISTRY`, `@tool`) references `src/gaia/agents/base/agent.py` — exactly the file that was modified in both our branch and PR #720. After PR #720 merges, the coherence check must verify that our spec documents' code examples remain accurate against the merged `base/agent.py`.

**Recommended action:** Resume and complete the coherence check as a post-merge task, scoped to documents that reference `base/agent.py`, the registry API, or agent instantiation patterns. The check should be completed before the branch-change-matrix is marked merge-ready. Priority: MEDIUM.

---

### 3.6 Open Item 6 — Handoff Document (No PR #720 Interaction)

Open Item 6 in the branch-change-matrix is a meta-item: it points reviewers to `future-where-to-resume-left-off.md` as the authoritative program handoff document (version 19.0, dated 2026-04-06). It does not represent a discrete work item and has no interaction with PR #720. It is therefore not analyzed in this document. Reviewers should consult that file directly for program-level context.

---

### 3.7 Open Item 7 — YAML Frontmatter Missing from Six Spec Files

**Current status:** Six files in `docs/spec/` were committed without Mintlify-required YAML frontmatter: `agent-ui-eval-kpi-reference.md`, `agent-ui-eval-kpis.md`, `gaia-loom-architecture.md`, `nexus-gaia-native-integration-spec.md`, `pipeline-metrics-competitive-analysis.md`, and `pipeline-metrics-kpi-reference.md`. All six begin with an `#` H1 heading and no `---` frontmatter block.

**How PR #720 affects it:** No impact. PR #720 adds `docs/guides/custom-agent.mdx` (correctly formatted with frontmatter: `title`, `description`, `icon`). It does not touch any of the six affected `.md` files.

**Recommended action:** This is a standalone fix requiring no coordination. Add three-line frontmatter blocks (`---`, `title:`, `---`) to all six files before the branch is submitted for merge review. This can be done in a single commit. Priority: LOW (documentation build risk, not runtime risk).

---

## 4. Conflict Analysis

### 4.1 registry.py — Fundamental Design Divergence

This is the highest-risk conflict in the analysis.

**PR #720's design philosophy:** The registry is a UI runtime service. It is discovered at server boot and asked for agent factories. The selection decision is made by the user (via the agent selector dropdown). The registry answers the question: "given an agent ID the user chose, give me a factory that creates that agent." It is lookup-centric.

**Our design philosophy:** The registry is a pipeline runtime service. It is loaded at pipeline boot and asked to select the best agent for a task given phase, complexity, and keyword signals. The selection decision is made autonomously by the pipeline engine. The registry answers the question: "given a task description and pipeline state, which agent should handle this?" It is selection-centric.

**Structural incompatibilities:**

| Dimension | PR #720 registry | Our registry |
|---|---|---|
| Agent input format | Three sources: builtin Python, custom Python, YAML manifest | One source: YAML configs in `config/agents/` |
| Agent descriptor | `AgentRegistration` (id, name, factory, source, models, starters) | `AgentDefinition` (id, name, capabilities, triggers, constraints, enabled) |
| Core import | `gaia.logger` | `gaia.utils.logging`, `gaia.agents.base.*`, `gaia.pipeline.defect_types` |
| Threading model | `threading.Lock` (sync) | `asyncio.Lock` (async) |
| Selection method | None — caller provides agent ID | `select_agent(task, phase, state)` |
| Hot reload | No | Yes, via watchdog observer |
| Capability index | No | Yes: `_capability_index`, `_trigger_index`, `_category_index` |
| Pydantic validation | Yes (manifest validation) | No |

**Resolution approach:** Do not attempt to merge these into a single class. The right architecture is a two-registry system:

- PR #720's `AgentRegistry` (rename to `AgentDiscovery` or keep as `AgentRegistry` in `agents/registry.py`) handles UI-facing concerns: what agents exist, how to instantiate them on demand, what conversation starters to show.
- Our `AgentRegistry` (rename to `PipelineAgentRegistry` or move to `pipeline/agent_registry.py`) handles pipeline-facing concerns: which agent to select for a given task, defect routing, phase-gating.

The two registries share agent IDs as their common key. The pipeline registry's `select_agent()` returns an agent ID. That ID can then be resolved via PR #720's discovery registry to get the factory and instantiate the agent. This is the bridge described in section 3.1.

The file-level conflict at merge time: both branches have `src/gaia/agents/registry.py` as a new file. Git will not see this as a conflict (both are `new file` mode additions from the respective branch perspective), but when our branch rebases onto main after PR #720 merges, git will detect that our `registry.py` differs from the upstream `registry.py` in nearly every line. The rebase will require a full manual resolution of this file. Plan for one engineer-hour of careful merge work.

### 4.2 base/agent.py — Ordering Fix vs. Our Extensions

PR #720 moves `self._response_format_template = ...` to before `self._register_tools()`. Our branch added `agent_id` property, NexusService hooks, and pipeline state propagation to the same `__init__` method.

The PR #720 fix is correct and resolves a real ordering bug (the UnboundLocalError when MCP loading triggers `rebuild_system_prompt` during tool registration). We must incorporate this fix. The merge conflict will appear as overlapping edits in the `__init__` block — roughly lines 176–220 in the upstream file.

Recommended merge resolution: Accept PR #720's ordering fix as the base. Reapply our additions (agent_id, NexusService hooks, state propagation) after the `_register_tools()` call. Verify that our additions do not themselves call `rebuild_system_prompt` or access `_response_format_template` before the template is set. If they do, move those accesses to after the template assignment.

### 4.3 _chat_helpers.py — Lock Rename

PR #720 renames `_model_load_lock` to `model_load_lock` (removing the underscore prefix, making it a module-level public symbol). This is a deliberate cross-module access change: `server.py` accesses the lock during boot-time preload.

If our branch has any reference to `_model_load_lock` by name, those references will become `AttributeError` at runtime after merge.

Confirmed status: `src/gaia/ui/_chat_helpers.py` on our branch defines `_model_load_lock = threading.Lock()` at line 67 and uses it at line 67 (`with _model_load_lock:`). Both the declaration and the usage site must be updated before or immediately after rebase.

Recommended action: After rebase, run `grep -r "_model_load_lock" src/gaia/ui/` and update all occurrences to `model_load_lock`. This is a guaranteed runtime break if skipped.

### 4.4 server.py — Router Mount Insertion Point

Both branches add router mounts to the FastAPI server lifespan / app creation. The exact insertion point and import block will conflict. The logic is compatible — both add new routers that serve different endpoints. The merge conflict is mechanical (import ordering, router.include_router call ordering) and can be resolved without design discussion.

### 4.5 ChatView.tsx — UI Component Overlap

Both branches modify `src/gaia/apps/webui/src/components/ChatView.tsx`. PR #720 adds the agent selector dropdown to the chat input footer. Our branch added terminal animations and device-unsupported guards. These are different UI regions (input footer vs. message display area), but a line-level conflict is probable because both changes touch the component's JSX return tree.

Recommended action: Perform a three-way merge of ChatView.tsx using the common ancestor as base. Keep both: PR #720's agent selector in the footer and our terminal animations and device guard in the message area. Test visually after merge.

---

## 5. Build-Upon Strategy

PR #720's architecture enables the following work items that our branch should layer on top of after PR #720 merges:

### 5.1 Unified Agent Discovery for Pipeline Routing

PR #720's `AgentRegistry.discover()` scans `~/.gaia/agents/` and loads both Python and YAML agents. Our pipeline's `RoutingEngine` currently only knows about agents defined in `config/agents/`. After PR #720 merges, we can extend `routing_engine.py` to call `AgentRegistry.discover()` at pipeline boot and merge the discovered agents into our pipeline registry. This would allow user-defined custom agents (placed in `~/.gaia/agents/`) to participate in pipeline routing without any code changes.

This is the highest-value build-upon opportunity: it makes the pipeline extensible by end users using the same drop-folder mechanism that PR #720 introduces for the UI.

### 5.2 GaiaAgent as Pipeline Fast-Warmup Default

PR #720's GaiaAgent (249s → 34s warmup, 21s → 2.9s TTFT) is currently used only for the UI's default chat experience. After merge, the pipeline engine's first-phase agent selection could prefer GaiaAgent for simple, low-complexity tasks where the full ChatAgent token budget is not needed. This requires adding GaiaAgent to our capability index with appropriate phase and complexity triggers.

### 5.3 BuilderAgent as Pipeline Design-Time Tool

PR #720's BuilderAgent scaffolds YAML manifests for custom agents. An extension worth considering: wire the BuilderAgent into the pipeline's PLANNING phase so that when the pipeline encounters a task that no registered agent is qualified to handle, it can offer to scaffold a new agent definition on the fly. This is a longer-horizon idea but PR #720's scaffolding code makes it tractable.

### 5.4 Per-Session Agent Type as Pipeline Session Context

PR #720 stores `agent_type` on the session row in the database and threads it through the chat dispatch path. After merge, we can extend this mechanism so that when a user's session is operating in "pipeline mode," the `agent_type` field carries enough information for the backend to route that session through the full pipeline engine rather than a single-agent chat path. This would make the pipeline engine accessible from the Agent UI without a separate CLI.

### 5.5 YAML Manifest Extensions for Pipeline Capability Declaration

PR #720's `AgentManifest` schema (Pydantic model) currently has no `capabilities` or `triggers` fields — it is tool-focused (`tools`, `mcp_servers`, `models`). We should propose an extension to the manifest schema that allows capability and trigger declaration:

```yaml
# proposed extension (coordinate with itomek)
pipeline:
  capabilities:
    - requirements-analysis
    - strategic-planning
  triggers:
    phases: [PLANNING]
    complexity_range: [0.3, 1.0]
```

With this extension, user-defined manifest agents could participate in pipeline routing without requiring a Python subclass. This is the cleanest path to a fully declarative agent ecosystem.

### 5.6 Dispatch Queue for Pipeline Job Management

PR #720 introduces `src/gaia/ui/dispatch.py` — a DispatchQueue for managing async tasks in the UI backend. After reviewing its design, this queue could serve as the delivery mechanism for pipeline runs initiated from the Agent UI, allowing the UI backend to track pipeline job state and stream progress events back to the frontend via SSE.

---

## 6. Recommended Action Plan

Priority tiers: **P0** = must happen before PR #720 merges; **P1** = must happen immediately after PR #720 merges, before our branch is submitted for review; **P2** = must happen within one sprint of PR #720 merge, before our branch merges to main.

Ownership: **us** = our branch team acts unilaterally; **itomek** = requires action or approval from itomek; **joint** = requires synchronous coordination between both teams.

**P0 — Before PR #720 merges (deadline: before itomek's PR lands on main):**

1. [joint] Open a discussion on PR #720 proposing the `pipeline:` extension to `AgentManifest` (see section 5.5). Get itomek's feedback on whether this belongs in PR #720 or a follow-up PR. This decision affects our post-merge architecture and must be resolved before the PR merges to avoid schema lock-in.

2. [joint] Confirm with itomek whether the PR #720 `AgentRegistry` class name should be preserved as-is, or whether a rename to `AgentDiscovery` is acceptable to reduce confusion with our pipeline-scoped `AgentRegistry`. A coordinated rename avoids a permanent naming collision in the codebase. Must be agreed before merge so both teams can update imports simultaneously.

**P1 — Immediately after PR #720 merges (deadline: before submitting our branch for review):**

3. [us] Rebase `feature/pipeline-orchestration-v1` onto updated main. Expect conflicts in: `src/gaia/agents/registry.py` (full manual resolution), `src/gaia/agents/base/agent.py` (moderate, ordering fix), `src/gaia/ui/_chat_helpers.py` (moderate, lock rename + cache key), `src/gaia/ui/server.py` (low, router mount ordering), `src/gaia/apps/webui/src/components/ChatView.tsx` (low-moderate, UI regions). Allocate 3–4 engineer-hours for rebase and conflict resolution.

4. [us] Search for `_model_load_lock` across `src/gaia/ui/` and update all references to `model_load_lock`. Confirmed present on our branch at `_chat_helpers.py` line 67 — this is a guaranteed runtime break if skipped.

5. [us] Rename our pipeline-specific `AgentRegistry` (or its file path) to eliminate the naming collision. Recommended: move to `src/gaia/pipeline/agent_registry.py` and update all imports in `pipeline/routing_engine.py`, `pipeline/engine.py`, and `pipeline/defect_router.py`.

**P2 — Within one sprint of PR #720 merge (deadline: before our branch merges to main):**

6. [us] Implement `AgentOrchestrator` as the bridge between the two registry systems (see section 3.1 and 5.1). This resolves Open Item 1 and unlocks dynamic pipeline routing.

7. [us] Wire resilience primitives (CircuitBreaker, Bulkhead, Retry) into `routing_engine.py`'s agent invocation call sites. This resolves Open Item 3.

8. [us] Standardize the 18 YAML files in `config/agents/` against the formal capability vocabulary in `src/gaia/core/capabilities.py`. This resolves Open Item 4 and unblocks full pipeline routing.

9. [us] Add YAML frontmatter to the six spec files missing it. This resolves Open Item 7.

10. [us] Complete the spec document coherence check (Open Item 5), scoped to documents that reference `base/agent.py`, the registry API, or agent instantiation patterns. Must be completed before the branch-change-matrix is marked merge-ready.

---

## 7. Questions for itomek / kovtcharov-amd

The following questions require upstream team input before we can finalize our integration approach.

**Q1 — Registry naming collision.**
PR #720's `AgentRegistry` and our `AgentRegistry` are both in `src/gaia/agents/registry.py` but have entirely different designs and purposes. What is the preferred resolution: (a) rename our class and relocate the file to `src/gaia/pipeline/agent_registry.py`, (b) rename PR #720's class (e.g., to `AgentDiscovery`), or (c) merge the two into a single class that serves both UI and pipeline concerns? Option (c) is technically possible but carries significant design complexity.

**Q2 — AgentManifest pipeline extension.**
Would itomek be open to adding optional `pipeline:` fields (capabilities, triggers, phases) to the `AgentManifest` schema in PR #720 or a follow-up PR? This would allow drop-folder YAML agents to participate in pipeline routing. If yes, we can draft the schema extension and submit it as a PR against main after PR #720 merges.

**Q3 — GaiaAgent warmup model choice.**
GaiaAgent achieves 34s warmup vs. 249s for ChatAgent. What model does GaiaAgent warm up with by default — is it using `Qwen3.5-35B-A3B-GGUF` or a smaller model? The pipeline engine's first-phase selection logic needs to know whether GaiaAgent's fast warmup persists across model switches or is specific to the default model configuration.

**Q4 — dispatch.py ownership.**
PR #720 introduces `src/gaia/ui/dispatch.py` (DispatchQueue). Is this module intended to remain scoped to the UI backend, or is it designed to be general-purpose? We are evaluating whether it could serve as the async job delivery mechanism for pipeline runs initiated from the Agent UI.

**Q5 — Session agent_type and pipeline mode.**
PR #720 adds `agent_type` to sessions, defaulting to `'chat'`. Is the `agent_type` field intended to be extensible to values beyond the registered agent IDs (e.g., `'pipeline'` as a special mode)? Or is the expectation that pipeline execution will always be associated with a specific named agent? This affects how we design the UI-to-pipeline bridge in section 5.4.

**Q6 — Dependency on `gaia.logger` vs `gaia.utils.logging`.**
PR #720's registry uses `from gaia.logger import get_logger` while our branch uses `from gaia.utils.logging import get_logger`. These are different import paths. Is `gaia.logger` the new standard logger module, and should we migrate our usage? Or is this an inconsistency in PR #720 that should be corrected before merge?

---

*Document produced by the planning-analysis-strategist (Dr. Sarah Kim) as part of the recursive iterative analysis pipeline. File: `docs/reference/pr720-integration-analysis.md`. All file references are relative to the repository root `C:\Users\amikinka\gaia`.*
