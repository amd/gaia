# PR #720 Integration Tracking — Phase 5 Coordination

**Document Type:** Integration Plan
**Issued by:** software-program-manager
**Date:** 2026-04-07
**Branch:** feature/pipeline-orchestration-v1
**Related PR:** amd/gaia#720 — feat: agent registry with per-session agent selection
**PR Author:** itomek (Tomasz Iniewicz)

---

## Executive Summary

This document tracks the coordination activities required to integrate Phase 5 work with PR #720. PR #720 delivers an AgentRegistry for UI-facing agent selection, while Phase 5 delivers a PipelineAgentRegistry for autonomous agent selection. Both registries must coexist without conflict.

**Integration Status:** PENDING PR #720 MERGE
**Critical Path:** Registry naming resolution → Rebase execution → Conflict resolution → Bridge implementation

---

## Pre-Merge Tasks (P0 — Before PR #720 merges)

### Task PR720-COORD-1: Open GitHub Discussion on AgentManifest Extension

**Subject:** Propose `pipeline:` extension to AgentManifest schema

**Description:**
Open a discussion on PR #720 proposing an optional `pipeline:` field in the AgentManifest Pydantic schema. This extension would allow drop-folder YAML agents to participate in pipeline routing.

**Proposed Schema Extension:**
```yaml
# Add to AgentManifest in src/gaia/agents/registry.py
class AgentManifest(BaseModel):
    # ... existing fields ...

    # Optional pipeline integration fields
    pipeline: Optional[dict] = None  # Contains:
    #   capabilities: Optional[List[str]]
    #   triggers: Optional[dict]
    #     phases: Optional[List[str]]
    #     complexity_range: Optional[Tuple[float, float]]
```

**Discussion Points:**
1. Should this be in PR #720 or a follow-up PR?
2. Is the schema backward compatible (optional field)?
3. What validation rules should apply?

**Owner:** software-program-manager
**Status:** TODO
**Blocking:** Yes — affects Phase 5 architecture
**Target Date:** Before PR #720 merge

**Action Items:**
- [ ] Draft schema extension proposal
- [ ] Post comment on PR #720
- [ ] Tag itomek and kovtcharov-amd
- [ ] Document decision in this tracking file

---

### Task PR720-COORD-2: Confirm Registry Naming Convention

**Subject:** Resolve AgentRegistry naming collision

**Description:**
Both PR #720 and Phase 5 have classes named `AgentRegistry` in `src/gaia/agents/registry.py`. This must be resolved before merge to avoid import conflicts.

**Options:**

| Option | Action | Pros | Cons |
|--------|--------|------|------|
| A | Rename PR #720's class to `AgentDiscovery` | Clear semantic distinction | Requires itomek approval, changes public API |
| B | Rename our class to `PipelineAgentRegistry` and move to `pipeline/agent_registry.py` | No upstream changes needed | Requires updating all our imports |
| C | Merge into single class with dual functionality | Single source of truth | High complexity, design disagreements likely |

**Recommended:** Option B — Rename our class to `PipelineAgentRegistry` and relocate to `pipeline/agent_registry.py`. This is the lowest-friction path and maintains clear separation of concerns.

**Owner:** software-program-manager
**Status:** TODO
**Blocking:** Yes — affects rebase execution
**Target Date:** Before PR #720 merge

**Action Items:**
- [ ] Post comment on PR #720 proposing naming resolution
- [ ] Get itomek's preference
- [ ] Document decision
- [ ] Prepare import update list for post-rebase

---

## Post-Merge Tasks (P1 — Immediately after PR #720 merges)

### Task PR720-COORD-3: Execute Rebase onto Updated Main

**Subject:** Rebase feature/pipeline-orchestration-v1 onto main after PR #720 merge

**Description:**
Once PR #720 merges to main, rebase our branch onto the updated main branch. Expect significant conflicts in registry.py and related files.

**Expected Conflicts:**

| File | Conflict Type | Resolution Complexity |
|------|---------------|----------------------|
| `src/gaia/agents/registry.py` | Mutual new file (both branches created from scratch) | HIGH — full manual resolution |
| `src/gaia/agents/base/agent.py` | Overlapping edits in `__init__` | MEDIUM — absorb PR #720's ordering fix |
| `src/gaia/ui/_chat_helpers.py` | Lock rename (`_model_load_lock` → `model_lock`) | MEDIUM — update all references |
| `src/gaia/ui/server.py` | Router mount insertion point | LOW — mechanical resolution |
| `src/gaia/apps/webui/src/components/ChatView.tsx` | Overlapping JSX edits | LOW-MEDIUM — keep both changes |

**Rebase Steps:**
1. Fetch updated main: `git fetch origin main`
2. Start rebase: `git rebase origin/main`
3. Resolve conflicts file by file (see below)
4. Continue rebase: `git rebase --continue`
5. Run tests to verify resolution: `pytest tests/unit/test_registry.py`

**Estimated Effort:** 3-4 engineer-hours

**Owner:** senior-developer
**Status:** TODO (blocked on PR #720 merge)
**Blocking:** Yes — all Phase 5 work blocked until rebase complete

**Action Items:**
- [ ] Monitor PR #720 status
- [ ] Schedule rebase work session (4 hours blocked time)
- [ ] Prepare conflict resolution notes
- [ ] Run full test suite post-rebase

---

### Task PR720-COORD-4: Resolve registry.py Conflict

**Subject:** Manual resolution of registry.py mutual new file conflict

**Description:**
Both PR #720 and Phase 5 created `src/gaia/agents/registry.py` from scratch with incompatible designs. This requires full manual resolution.

**Resolution Strategy:**

1. **Keep PR #720's registry** as `src/gaia/agents/registry.py` (AgentRegistry/AgentDiscovery)
   - This is the UI-facing discovery registry
   - Handles builtin Python agents, custom Python agents, YAML manifests
   - Returns agent factories for on-demand instantiation

2. **Move our registry** to `src/gaia/pipeline/agent_registry.py` (PipelineAgentRegistry)
   - This is the pipeline-facing selection registry
   - Handles YAML configs from `config/agents/`
   - Implements `select_agent()` with phase/complexity/keyword filtering

3. **Update all imports** in our codebase:
   ```python
   # Old import (will break):
   from gaia.agents.registry import AgentRegistry

   # New import:
   from gaia.pipeline.agent_registry import PipelineAgentRegistry
   ```

**File Changes Required:**
- Create `src/gaia/pipeline/agent_registry.py` with our registry code
- Update imports in: `pipeline/routing_engine.py`, `pipeline/engine.py`, `pipeline/defect_router.py`, `pipeline/loop_manager.py`
- Keep PR #720's `src/gaia/agents/registry.py` unchanged

**Owner:** senior-developer
**Status:** TODO (blocked on PR720-COORD-3)
**Blocking:** Yes — pipeline routing blocked

**Action Items:**
- [ ] Copy our registry code to `pipeline/agent_registry.py`
- [ ] Rename class from `AgentRegistry` to `PipelineAgentRegistry`
- [ ] Update all imports
- [ ] Run `grep -r "from gaia.agents.registry import"` to find all occurrences
- [ ] Verify pipeline tests pass

---

### Task PR720-COORD-5: Update _model_load_lock to model_lock

**Subject:** Fix lock rename from PR #720

**Description:**
PR #720 renames `_model_load_lock` to `model_lock` (removes underscore prefix, making it public). Our branch references `_model_load_lock` which will break after rebase.

**Confirmed References on Our Branch:**
- `src/gaia/ui/_chat_helpers.py` line 67: `_model_load_lock = threading.Lock()`
- `src/gaia/ui/_chat_helpers.py` line 67: `with _model_load_lock:`

**Resolution:**
1. Rename declaration: `model_lock = threading.Lock()`
2. Update usage: `with model_lock:`
3. Search for other references: `grep -r "_model_load_lock" src/gaia/ui/`
4. Update all occurrences

**Owner:** senior-developer
**Status:** TODO (blocked on PR720-COORD-3)
**Blocking:** Yes — runtime break if skipped

**Action Items:**
- [ ] Run `grep -r "_model_load_lock" src/gaia/`
- [ ] Update all references to `model_lock`
- [ ] Run UI tests to verify no `AttributeError`

---

### Task PR720-COORD-6: Rename Our Registry to PipelineAgentRegistry

**Subject:** Relocate and rename our registry class

**Description:**
See PR720-COORD-4. This task tracks the actual implementation of the rename and relocation.

**Files to Modify:**
- `src/gaia/pipeline/agent_registry.py` (create new)
- `src/gaia/pipeline/routing_engine.py` (update import)
- `src/gaia/pipeline/engine.py` (update import)
- `src/gaia/pipeline/defect_router.py` (update import)
- `src/gaia/pipeline/loop_manager.py` (update import)
- Any test files that import `AgentRegistry`

**Owner:** senior-developer
**Status:** TODO (blocked on PR720-COORD-3)
**Blocking:** No — can proceed after rebase

**Action Items:**
- [ ] Create `pipeline/agent_registry.py`
- [ ] Rename class to `PipelineAgentRegistry`
- [ ] Update all imports
- [ ] Run pipeline tests

---

## Post-Rebase Tasks (P2 — Within one sprint)

### Task PR720-COORD-7: Implement AgentOrchestrator Bridge

**Subject:** Bridge between AgentDiscovery and PipelineAgentRegistry

**Description:**
Implement `AgentOrchestrator` as a thin adapter that:
1. Calls `PipelineAgentRegistry.select_agent()` to pick an agent ID
2. Calls `AgentDiscovery.get(agent_id).factory()` to instantiate the agent

**Implementation:**
```python
class AgentOrchestrator:
    def __init__(self, discovery_registry: AgentRegistry, pipeline_registry: PipelineAgentRegistry):
        self.discovery = discovery_registry
        self.pipeline = pipeline_registry

    def select_and_instantiate(self, task: str, phase: str, state: dict):
        # Select agent ID via pipeline registry
        agent_id = self.pipeline.select_agent(task, phase, state)

        # Get factory via discovery registry
        agent_registration = self.discovery.get(agent_id)

        # Instantiate and return
        return agent_registration.factory()
```

**Owner:** senior-developer
**Status:** TODO (blocked on PR720-COORD-6)
**Blocking:** No — can proceed after registry rename

**Action Items:**
- [ ] Design AgentOrchestrator interface
- [ ] Implement in `src/gaia/pipeline/orchestrator.py`
- [ ] Write unit tests
- [ ] Integrate with routing engine

---

### Task PR720-COORD-8: Wire Resilience Primitives

**Subject:** Wrap agent invocations with CircuitBreaker, Bulkhead, Retry

**Description:**
Wire the resilience primitives from Phase 4 (`src/gaia/resilience/`) into agent invocation call sites in `routing_engine.py`.

**Target Call Sites:**
1. `routing_engine.py::route()` — agent selection and invocation
2. `engine.py::execute_agent()` — agent execution
3. `loop_manager.py::run_loop()` — loop iteration

**Implementation:**
```python
from gaia.resilience import CircuitBreaker, Retry, Bulkhead

# Configure circuit breaker for LLM calls
llm_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30)

# Configure retry for transient failures
llm_retry = Retry(max_retries=3, base_delay=1.0)

# Wrap agent invocation
@llm_breaker
@llm_retry
def invoke_agent(agent, task):
    return agent.execute(task)
```

**Owner:** senior-developer
**Status:** TODO (blocked on PR720-COORD-7)
**Blocking:** No — can proceed after orchestrator

**Action Items:**
- [ ] Import resilience primitives
- [ ] Wrap agent invocation call sites
- [ ] Write integration tests
- [ ] Verify circuit breaker trips on failures

---

### Task PR720-COORD-9: Standardize YAML Capabilities

**Subject:** Normalize capabilities in 18 YAML files against vocabulary

**Description:**
Extract all capability strings from the 18 existing YAML agent files, normalize them, and document in `src/gaia/core/capabilities.py` as a comment block (before VALID_CAPABILITY_STRINGS is implemented in Phase 2).

**Steps:**
1. Extract capabilities: `grep -h "capabilities:" config/agents/*.yaml`
2. Normalize spelling (e.g., `full-stack-development` vs `fullstack`)
3. Document in `capabilities.py` as comment:
   ```python
   # Canonical capability vocabulary (derived from 18 YAML agents, Phase 5):
   # - requirements-analysis
   # - strategic-planning
   # - full-stack-development
   # - ...
   ```
4. Update YAML files to use normalized vocabulary

**Owner:** senior-developer
**Status:** TODO (blocked on PR720-COORD-6)
**Blocking:** No — can proceed after registry rename

**Action Items:**
- [ ] Extract capabilities from all 18 YAML files
- [ ] Identify inconsistencies
- [ ] Define canonical vocabulary
- [ ] Document in capabilities.py
- [ ] Update YAML files

---

## Risk Tracking

| Risk | Probability | Impact | Mitigation | Status |
|------|-------------|--------|------------|--------|
| PR #720 merge delayed | Medium | High | Parallel work on template library and stage agents | ACTIVE |
| registry.py conflict resolution takes longer than expected | Medium | High | Schedule 4-hour blocked session, have senior-developer pair with software-program-manager | ACTIVE |
| AgentOrchestrator design requires itomek approval | Low | Medium | Open design discussion early, document in GitHub issue | MONITORING |
| Resilience wiring breaks existing agent calls | Medium | Medium | Write integration tests before wiring, run full test suite | MONITORING |

---

## Integration Timeline

```
Week 1-2: Pre-Merge
├── PR720-COORD-1: Open AgentManifest discussion [TODO]
└── PR720-COORD-2: Confirm registry naming [TODO]

Week 3: Post-Merge Rebase
├── PR720-COORD-3: Execute rebase [TODO - blocked on PR #720]
├── PR720-COORD-4: Resolve registry.py conflict [TODO]
└── PR720-COORD-5: Fix _model_load_lock rename [TODO]

Week 4: Post-Rebase Integration
├── PR720-COORD-6: Rename to PipelineAgentRegistry [TODO]
├── PR720-COORD-7: Implement AgentOrchestrator [TODO]
├── PR720-COORD-8: Wire resilience primitives [TODO]
└── PR720-COORD-9: Standardize capabilities [TODO]
```

---

## Questions for itomek / kovtcharov-amd

The following questions require upstream team input:

**Q1 — Registry Naming:**
Should PR #720's `AgentRegistry` be renamed to `AgentDiscovery` for clarity, or should our registry be renamed to `PipelineAgentRegistry`? Recommendation: Rename ours to avoid upstream changes.

**Q2 — AgentManifest Extension:**
Would you be open to adding optional `pipeline:` fields (capabilities, triggers, phases) to the AgentManifest schema? This allows drop-folder YAML agents to participate in pipeline routing.

**Q3 — GaiaAgent Warmup:**
What model does GaiaAgent warm up with by default? The pipeline engine's first-phase selection logic needs to know if GaiaAgent's fast warmup (34s vs 249s) persists across model switches.

**Q4 — DispatchQueue Ownership:**
Is `dispatch.py` (DispatchQueue) intended for general-purpose use or UI-only? We are evaluating it for pipeline job management in the UI backend.

**Q5 — Session AgentType Extensibility:**
Is the `agent_type` field extensible to values beyond registered agent IDs (e.g., `'pipeline'` as a special mode)?

**Q6 — Logger Import Path:**
PR #720 uses `from gaia.logger import get_logger` while our branch uses `from gaia.utils.logging import get_logger`. Which is the standard path?

---

## Contact

**Integration Owner:** software-program-manager
**Technical Lead:** senior-developer
**PR #720 Author:** itomek
**Escalation:** @kovtcharov-amd

---

**Document Status:** READY FOR EXECUTION
**Next Action:** Monitor PR #720 merge status

---

**END OF PR #720 INTEGRATION TRACKING**
