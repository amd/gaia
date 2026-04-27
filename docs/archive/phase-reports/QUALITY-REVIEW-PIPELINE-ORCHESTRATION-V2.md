# Quality Review Report: Pipeline Orchestration V2 Plans

**Branch:** `feature/pipeline-orchestration-v1`
**Reviewer:** Taylor Kim, Senior Quality Management Specialist
**Date:** 2026-04-26
**Review Scope:** Strategic Plan (embedded in V2) + Program Management Plan V2
**Assessment Score:** 6.5/10 -- CONDITIONALLY READY (Phase 1-2 only)
**Documents Reviewed:**
- `docs/archive/phase-reports/PROGRAM-MANAGEMENT-PIPELINE-ORCHESTRATION-V2.md`
- `docs/archive/phase-reports/PROGRAM-MANAGEMENT-PIPELINE-ORCHESTRATION.md`
- `docs/archive/phase-reports/PROJECT-STATE-SUMMARY-pipeline-orchestration-v1.md`
- `src/gaia/pipeline/engine.py` (1,373 lines)
- `src/gaia/hooks/base.py` (373 lines)
- `src/gaia/hooks/registry.py` (436 lines)
- `src/gaia/hooks/production/quality_hooks.py` (445 lines)
- `src/gaia/hooks/production/context_hooks.py` (351 lines)
- `src/gaia/hooks/production/validation_hooks.py` (284 lines)
- `src/gaia/pipeline/sse_hooks.py` (230 lines)
- Verified: `src/gaia/pipeline/supervision/` DOES NOT EXIST
- Verified: `src/gaia/quality/supervisor.py` EXISTS (separate module)

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Coherence Check](#2-coherence-check)
3. [Architecture Gaps](#3-architecture-gaps)
4. [Feasibility Assessment](#4-feasibility-assessment)
5. [Risk Analysis](#5-risk-analysis)
6. [Excel Equations Metaphor Quality](#6-excel-equations-metaphor-quality)
7. [Answers to 6 Open Questions](#7-answers-to-6-open-questions)
8. [Recommendations](#8-recommendations)
9. [Scoring Matrix](#9-scoring-matrix)

---

## 1. Executive Summary

The V2 Program Management Plan proposes a three-part orchestration kernel above `PipelineEngine`:
1. **ProjectOrchestrator** -- Long-running loop that dispatches PipelineEngine per objective
2. **ProjectSupervisor hierarchy** -- Strategic agents (Quality, Git) overseeing pipeline outcomes
3. **Objectives Document** -- YAML roadmap with status tracking and dependency management

The plan is broken into 5 phases, with Phases 1-2 detailed (file-level code examples, tests, acceptance criteria) and Phases 3-5 high-level (hook lists, feature tables).

### Verdict Summary

| Dimension | Score | Notes |
|-----------|-------|-------|
| Architecture | 7/10 | Good separation of concerns, but integration gaps in hook cascades and storage |
| Feasibility | 7/10 | Phases 1-2 are solid; Phases 3-5 need fundamental re-design |
| Risk Management | 5/10 | Risk matrix exists but critical risks (circuit breaker bug, YAML corruption) are under-mitigated |
| Code Quality | 8/10 | Examples are clean, follow existing patterns, match codebase conventions |
| Test Strategy | 8/10 | Comprehensive per-phase test plan with clear mock strategies |
| **Overall** | **6.5/10** | **Conditional pass: Phases 1-2 with 5 required changes; Phases 3-5 deferred** |

### Gate Decision

- **Phase 1 + 2:** CONDITIONALLY READY -- 5 required changes must be resolved before developer handoff
- **Phase 3:** NOT READY -- Hook cascade architecture needs complete re-design
- **Phase 4:** NOT READY -- Parallel dispatch incompatible with YAML storage model
- **Phase 5:** NOT READY -- Defers to earlier phase resolution

---

## 2. Coherence Check

### 2.1 Strategic vs Implementation Alignment

The strategic vision is embedded within the V2 document (Section 1, Executive Summary). The implementation plan directly addresses the identified gap:

| Strategic Element | Implementation Coverage | Alignment |
|-------------------|----------------------|-----------|
| "PipelineEngine runs single pipeline, nothing orchestrates multiple" | ProjectOrchestrator (Phase 1) | DIRECT |
| "ProjectSupervisor hierarchy" | Phase 2 (ProjectSupervisor, GitSupervisor) | DIRECT |
| "Objectives Document -- YAML roadmap" | Phase 1 (objectives.py) | DIRECT |
| "Hooks Recalculate -- dependency cascade" | Phase 3 (6 automation hooks) | DIRECT but under-specified |

**Finding:** Strategic intent and implementation details are well-aligned. No substantive contradictions between vision and plan.

### 2.2 Internal Contradictions

**C-1: auto_commit default contradicts recommendation**
- Code example (line 425): `auto_commit: bool = True`
- Open Question #5 recommendation: "Disabled by default"
- **Severity:** MEDIUM -- developers following the code example will implement the opposite of the recommendation
- **Resolution:** Change code example to `auto_commit: bool = False`

**C-2: git_user_email is a truncated placeholder**
- Code example (line 428): `git_user_email: str = "orchestrator@gai"`
- "gai" is not a valid domain
- Open Question #2 acknowledges the issue but doesn't resolve it
- **Severity:** MEDIUM -- will produce invalid git commits
- **Resolution:** Use `git config` lookup or `"gaia-orchestrator@local"`

**C-3: "PipelineEngine never modified" vs "adapter may be needed"**
- Section 7, Key Integration Rules #1: "PipelineEngine is never modified"
- Open Question #3: "If PipelineEngine needs minor changes... should we create adapter?"
- **Severity:** LOW -- the "never modified" rule is a good principle; the adapter preserves it
- **Resolution:** Create the adapter proactively in Phase 1, don't wait for a problem to arise

**C-4: Documentation gap -- no standalone strategic plan**
- The V2 document references "the strategist identified a critical gap" but no separate strategic plan exists in the repo
- **Severity:** LOW -- content is present, just not as a separate document
- **Resolution:** Accept the merged format; do not create a redundant separate document

---

## 3. Architecture Gaps

### G-1: No `supervision/` directory exists (CONFIRMED MISSING)

The user's prompt references `src/gaia/pipeline/supervision/` as a potential existing component. Directory check confirms it does NOT exist. The quality supervisor lives in `src/gaia/quality/supervisor.py`.

- **Impact:** The V2 plan correctly references `src/gaia/quality/supervisor.py` and proposes renaming/refactoring it to `QualitySupervisor` in Phase 2. No action needed -- the plan already handles this.

### G-2: No reverse dependency index (CRITICAL FOR EXCEL METAPHOR)

The `Objective` model stores `dependencies: List[str]` (forward references). To implement "when I complete, notify my dependents," the system needs a reverse index.

- **Current approach:** `is_blocked_by_dependencies()` iterates ALL objectives to find blocking deps -- O(n) per check. Phase 3 hooks would do this for EVERY dependent.
- **Impact:** Phase 3 hook cascade is O(n^2) per state change. Acceptable for <20 objectives, problematic for 50+.
- **Resolution:** Add `dependents: Dict[str, List[str]]` (reverse index) to `ProjectObjectives`. Built once on load, updated on `add_objective()` and `transition_to()`.

### G-3: `_check_criterion` keyword matching is fragile

```python
# engine.py lines 676-687
def _check_criterion(self, criterion: str, artifacts: Dict, quality_score: float) -> bool:
    cl = criterion.lower()
    for name, value in artifacts.items():
        if any(w in name.lower() for w in cl.split() if len(w) > 3):
            return True
        text = value.lower() if isinstance(value, str) else str(value).lower()
        if any(w in text for w in cl.split() if len(w) > 3):
            return True
    if ("test" in cl or "error" in cl) and quality_score >= 0.85:
        return True
    return False
```

- **False positive risk:** Criterion "Build REST API" matches artifact "api_gateway.md" even if about a different API
- **False negative risk:** Criterion "Implement authentication" misses artifact "auth_module.py" because "implement" and "authentication" are > 3 chars but the artifact name uses "auth"
- **Special case problem:** "test" in criterion + quality > 0.85 = automatic pass. A criterion "All tests pass" would match ANY artifact if quality > 0.85, regardless of actual test results
- **Severity:** HIGH for Phase 1 (this IS the evaluation mechanism)
- **Resolution:** Add a "required keyword" extraction that requires ALL words > 3 chars to appear, not ANY. Add semantic weight for artifact name matches vs content matches. Phase 2 ProjectSupervisor LLM evaluation is the real fix.

### G-4: CircuitBreaker misuse in GitSupervisor (BUG)

```python
# git.py lines 1497-1503
def _protected(self, operation, func, branch, message) -> bool:
    try:
        self._circuit_breaker(func)  # <-- BUG: returns decorated func, never calls it
        ...
```

Looking at `src/gaia/resilience/circuit_breaker.py`, the CircuitBreaker's `__call__` method acts as a hybrid decorator factory. Calling `self._circuit_breaker(func)` returns a wrapped function but never executes it. The correct pattern is either:
- `self._circuit_breaker(func)()` -- call the returned wrapper
- `@self._circuit_breaker` -- decorate the method at definition time
- Or use the CircuitBreaker's explicit `call(func)` method if available

- **Impact:** ALL git operations execute WITHOUT circuit breaker protection. A git server outage causes immediate failures with no retry/recovery.
- **Severity:** CRITICAL
- **Resolution:** Fix to actually invoke the wrapped function. See Recommendation #1.

### G-5: Dual hook registry pattern (undocumented)

The pipeline has its own HookRegistry/HookExecutor. The orchestrator has its own separate HookRegistry/HookExecutor (engine.py lines 490-492). When the orchestrator calls `self._emit_event("OBJECTIVE_COMPLETE", ...)`, this goes to the ORCHESTRATOR's hook registry, NOT the pipeline's.

- **Impact:** A hook registered for OBJECTIVE_COMPLETE on the orchestrator's registry fires. A hook registered on the pipeline's registry does NOT fire for orchestrator events. This is architecturally correct but undocumented.
- **Severity:** MEDIUM -- developers extending the system may register hooks on the wrong registry
- **Resolution:** Document the dual-registry pattern. Consider a unified event bus if Phase 3 adds cross-boundary hooks.

### G-6: No PipelineDispatch protocol/interface

```python
pipeline_factory: Optional[Callable] = None
```

The factory has no type contract. The orchestrator assumes it produces something with `.initialize()` and `.start()`. There's no `Protocol` or ABC enforcing this.

- **Impact:** A bad factory produces runtime errors at dispatch time, not load time
- **Severity:** MEDIUM
- **Resolution:** Define a `PipelineDispatch` Protocol with `initialize()` and `start()` methods. Type the factory as `Optional[Callable[[Objective], PipelineDispatch]]`.

---

## 4. Feasibility Assessment

### Phase 1: CORE ORCHESTRATION KERNEL -- FEASIBLE (8/10)

| Work Item | Estimate | Reality Check |
|-----------|----------|---------------|
| 1.1 Objectives Model | 1-2 hours | Accurate. Pure data layer, no external deps. |
| 1.2 ProjectOrchestrator | 4-6 hours | Realistic IF pipeline integration works cleanly. The adapter pattern (recommended) adds ~1 hour. |
| 1.3 __init__.py | 10 minutes | Trivial. |
| 1.4 Hook Events | 2-3 hours | Accurate. Follows existing BaseHook pattern. |
| 1.5 Tests | 3-4 hours | Accurate. Test examples are already provided. |

**Total Phase 1:** 10.5-15.5 hours. One developer can complete in 2 working days.

**Risk:** PipelineEngine integration. The orchestrator passes `PipelineContext` with `metadata` containing objective info, but PipelineEngine does NOT read this metadata. If the orchestrator needs to pass objective-specific configuration (agents, templates) to the pipeline, the current interface doesn't support it. The adapter pattern mitigates this.

### Phase 2: SUPERVISOR HIERARCHY -- MODERATELY FEASIBLE (6/10)

| Work Item | Estimate | Reality Check |
|-----------|----------|---------------|
| 2.1 ProjectSupervisor | 4-6 hours | Agent class is heavy. Even with skip_lemonade=True, the Agent constructor may require environment setup. Risk: HIGH. |
| 2.2 GitSupervisor | 3-4 hours | Code example has CircuitBreaker bug (G-4). Fix adds 1-2 hours. Risk: MEDIUM. |
| 2.3 SupervisorRegistry | 1-2 hours | Straightforward. |
| 2.4 Wire into Orchestrator | 2-3 hours | Modifies engine.py, breaking the "never modify" rule. Risk: LOW if adapter pattern used. |

**Total Phase 2:** 10-15 hours. One developer can complete in 2 working days.

**Risk:** ProjectSupervisor inherits from `Agent`, which is a complex class. The `Agent` class may require LLM backend initialization even with `skip_lemonade=True`. Consider making ProjectSupervisor NOT inherit from Agent for Phase 2, and add Agent capabilities later if needed.

### Phase 3: AUTOMATION HOOKS -- LOW FEASIBILITY (5/10)

The 6 proposed hooks are individually straightforward, but the cascade architecture (how they chain together) is the problem:
- No dependency graph model (G-2)
- No circular reference detection
- No max cascade depth enforcement
- TaskSpawnHook requires dynamic objective creation (not yet modeled)

**Recommendation:** Phase 3 starts with a `DependencyGraph` class (topological sort, cycle detection, reverse index), THEN builds hooks on top of it.

### Phase 4: ADVANCED FEATURES -- LOW FEASIBILITY (3/10)

**Parallel dispatch** (`asyncio.gather()` on independent objectives):
- YAML file I/O is NOT thread-safe / coroutine-safe
- Multiple coroutines writing to the same YAML file = data corruption
- The plan acknowledges this and suggests "stay sequential" as fallback
- If sequential-only, the Phase 4 value proposition collapses

**Git worktrees:**
- PipelineEngine would need working-directory awareness (not currently supported)
- Each objective runs in a separate directory, then results merge
- This is a PipelineEngine change, violating the "black box" principle
- Significant architecture change, not a Phase 4 hardening task

**Recommendation:** Phase 4 should be re-scoped. Replace parallel dispatch with "parallel SSE event emission" (already works). Defer git worktrees to a separate feature branch with PipelineEngine changes.

### Phase 5: HARDENING & PRODUCTION -- NOT SCOPED (N/A)

Phase 5 items (performance testing, security audit, documentation, UI dashboard) are standard and can be executed once Phases 1-3 are stable. No feasibility assessment possible until earlier phases are complete.

---

## 5. Risk Analysis

### Critical Risks (Must be resolved before handoff)

| ID | Risk | Probability | Impact | Current Mitigation | Adequate? |
|----|------|------------|--------|-------------------|-----------|
| R-1 | CircuitBreaker bug in GitSupervisor (G-4) | CERTAIN | HIGH | None documented | NO -- Fix required |
| R-2 | YAML file corruption on crash mid-write | LOW | CRITICAL | "Fail hard" (Q6) | NO -- Use atomic writes |
| R-3 | auto_commit default is True (C-1) | HIGH | MEDIUM | "Recommendation: False" | NO -- Code must match recommendation |

### High Risks (Should be resolved before Phase 1 completion)

| ID | Risk | Probability | Impact | Mitigation |
|----|------|------------|--------|------------|
| R-4 | `_check_criterion` false positives/negatives | HIGH | MEDIUM | Phase 2 ProjectSupervisor LLM evaluation is the fix |
| R-5 | PipelineEngine is NOT a clean black box | MEDIUM | MEDIUM | Create OrchestratorPipelineAdapter |
| R-6 | git_user_email invalid ("orchestrator@gai") | CERTAIN | LOW | Fix to use git config lookup |
| R-7 | ProjectSupervisor depends on heavy Agent class | MEDIUM | MEDIUM | Consider decoupling from Agent inheritance |

### Medium Risks (Phase 2/3)

| ID | Risk | Probability | Impact | Mitigation |
|----|------|------------|--------|------------|
| R-8 | Hook cascade loops (circular deps) | MEDIUM | HIGH | Add topological sort validation on load |
| R-9 | O(n^2) dependency checking per state change | HIGH | LOW | Add reverse dependency index |
| R-10 | Dual hook registry confusion | MEDIUM | LOW | Document the pattern |

### Low Risks

| ID | Risk | Probability | Impact | Mitigation |
|----|------|------------|--------|------------|
| R-11 | Parallel dispatch impossible with YAML storage | HIGH | MEDIUM | Accept sequential-only or change storage layer |
| R-12 | Git worktrees require PipelineEngine changes | MEDIUM | MEDIUM | Defer to separate branch |

---

## 6. Excel Equations Metaphor Quality

### 6.1 What Excel Does (Reference Model)

1. Cell formulas declare dependencies (A1 = B1 + C1)
2. Dependency graph enables efficient invalidation
3. Change propagation is automatic and ordered
4. Circular references are detected and flagged
5. Recalculation is batched for efficiency

### 6.2 What the Plan Proposes

| Excel Feature | Plan Equivalent | Status |
|---------------|----------------|--------|
| Formula = dependency declaration | `dependencies: List[str]` field | IMPLEMENTED (forward only) |
| Change propagation | ObjectiveUpdateHook on OBJECTIVE_COMPLETE | PROPOSED (Phase 3) |
| Dependency graph | Implicit (not modeled) | MISSING |
| Circular reference detection | None | MISSING |
| Batch recalculation | Not modeled | MISSING |
| Reverse index (who depends on me?) | O(n) iteration | MISSING (G-2) |

### 6.3 Specific Issues

**Issue E-1: No circular dependency detection**
If OBJ-001 depends on OBJ-002 and OBJ-002 depends on OBJ-001, both stay BLOCKED forever. No error is raised. The project appears "stuck" with no diagnostic.

**Issue E-2: No reverse index**
When OBJ-001 completes, the system iterates ALL objectives to find which ones are unblocked. O(n) per state change. For 50 objectives and 50 state changes, that's 2,500 iterations.

**Issue E-3: Event propagation model is ambiguous**
The orchestrator has its own hook registry. The pipeline has its own. OBJECTIVE_COMPLETE events go to the orchestrator's registry. But the hook code examples in Phase 3 don't specify which registry they register to. This creates confusion.

**Issue E-4: TaskSpawnHook breaks the model**
If a blocked objective spawns a sub-objective, the dependency graph changes mid-execution. The topological sort (if implemented) would need to be re-run. The model doesn't account for dynamic graph changes.

### 6.4 Metaphor Quality Score: 5/10

The metaphor is conceptually correct but the implementation design doesn't yet match. Phase 3 needs a proper `DependencyGraph` class (topological sort + cycle detection + reverse index) before the hooks can deliver "Excel-like" behavior. Without this, the system is a simple state machine, not a spreadsheet.

---

## 7. Answers to 6 Open Questions

### Q1: Where should the objectives YAML live?

**Decision: `.gaia/objectives.yaml`**

Rationale:
1. Keeps project root clean -- `.gaia/` is the established GAIA convention for framework-managed files
2. Can be added to `.gitignore` for teams that don't version-control objectives
3. Works with `ProjectObjectives.load(path)` (already uses Path, any location works)
4. Default constant in `OrchestratorConfig` should resolve `.gaia/objectives.yaml` relative to CWD

Implementation:
```python
# In OrchestratorConfig or a default factory
DEFAULT_OBJECTIVES_PATH = Path(".gaia/objectives.yaml")
```

### Q2: How is git author configuration handled?

**Decision: Read from git config at initialization, with safe fallback**

```python
def _resolve_git_identity(self) -> tuple[str, str]:
    """Read git user.name and user.email from config, with fallback."""
    try:
        name = subprocess.check_output(
            ["git", "config", "user.name"], text=True, timeout=5
        ).strip()
        email = subprocess.check_output(
            ["git", "config", "user.email"], text=True, timeout=5
        ).strip()
        if name and email:
            return name, email
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return "GAIA Orchestrator", "gaia-orchestrator@local"
```

Override via `OrchestratorConfig.git_user_name` / `git_user_email` if set.

### Q3: What is the PipelineEngine adapter strategy?

**Decision: Create `OrchestratorPipelineAdapter` in Phase 1**

This is not optional -- it's necessary for clean architecture. The adapter:
- Wraps PipelineEngine instantiation
- Provides a clean interface: `dispatch(objective) -> PipelineResult`
- Translates objective metadata into PipelineContext configuration
- Is the ONLY place that knows about PipelineEngine internals

File: `src/gaia/orchestration/pipeline_adapter.py`
```python
class OrchestratorPipelineAdapter:
    """Adapts ProjectOrchestrator's objective model to PipelineEngine's interface."""
    
    def __init__(self, skip_lemonade: bool = True, model_id: Optional[str] = None):
        self._skip_lemonade = skip_lemonade
        self._model_id = model_id
    
    def dispatch(self, objective: Objective) -> PipelineEngine:
        from gaia.pipeline.engine import PipelineEngine
        from gaia.pipeline.state import PipelineContext
        
        engine = PipelineEngine(
            enable_logging=False,
            skip_lemonade=self._skip_lemonade,
            model_id=self._model_id,
        )
        return engine
    
    async def execute(self, engine: PipelineEngine, objective: Objective) -> PipelineSnapshot:
        from gaia.pipeline.state import PipelineContext
        
        pipeline_id = f"orch-{objective.id.lower()}-{uuid4().hex[:8]}"
        context = PipelineContext(
            pipeline_id=pipeline_id,
            user_goal=objective.description or objective.title,
            metadata={
                "objective_id": objective.id,
                "objective_title": objective.title,
                "acceptance_criteria": objective.acceptance_criteria,
            },
            max_iterations=10,
            quality_threshold=0.85,
        )
        config = {"template": "generic", "enable_hooks": True}
        await engine.initialize(context, config)
        return await engine.start()
```

### Q4: Which LLM is used for optional LLM evaluation?

**Decision: Use `model_id` from ProjectSupervisor config, defaulting to GAIA standard**

```python
class ProjectSupervisor(Agent):
    def __init__(
        self,
        model_id: str = "Qwen3.5-35B-A3B-GGUF",  # GAIA standard
        use_llm_evaluation: bool = False,  # Rule-based by default
        ...
    ):
```

Key constraints:
1. Rule-based evaluation is the DEFAULT (`use_llm_evaluation=False`)
2. LLM evaluation is a lightweight criteria match, NOT a full code review
3. The LLM prompt is minimal: criteria + artifact names + quality score
4. LLM failure falls back to rule-based (already implemented in code example)

### Q5: Is auto-commit safe?

**Decision: Disabled by default (`auto_commit: bool = False`)**

Rationale:
1. Partial objective state could create noisy/incomplete commits
2. CI/CD environments may not allow git commits
3. Users should explicitly opt-in after understanding the behavior
4. Add a `dry_run` config option that logs "would commit: <message>" without actually committing

```python
@dataclass
class OrchestratorConfig:
    objectives_path: Path
    auto_commit: bool = False  # CHANGED from True
    dry_run: bool = False  # NEW: log commits without making them
    ...
```

### Q6: How does the system recover from YAML corruption?

**Decision: Atomic file writes (Phase 1) + git history recovery (Phase 1)**

Phase 1 atomic write pattern:
```python
def save(self, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".yaml.tmp")
    with open(tmp_path, "w") as f:
        yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)
    os.replace(str(tmp_path), str(path))  # Atomic on POSIX and Windows
```

Phase 1 recovery on load failure:
```python
@classmethod
def load(cls, path: Path) -> "ProjectObjectives":
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls._from_yaml_data(data, path)
    except (yaml.YAMLError, FileNotFoundError, KeyError) as e:
        # Try git history
        try:
            result = subprocess.run(
                ["git", "show", f"HEAD:{path}"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                data = yaml.safe_load(result.stdout)
                return cls._from_yaml_data(data, path)
        except Exception:
            pass
        raise ObjectivesLoadError(str(path), str(e))
```

---

## 8. Recommendations

### 8.1 Required Changes (Block handoff until resolved)

| # | Change | File | Severity | Effort |
|---|--------|------|----------|--------|
| 1 | Fix CircuitBreaker bug in GitSupervisor._protected() | `supervisors/git.py` | CRITICAL | 30 min |
| 2 | Use atomic file writes (write to .tmp, then os.replace) | `objectives.py` | HIGH | 30 min |
| 3 | Create OrchestratorPipelineAdapter | NEW: `pipeline_adapter.py` | HIGH | 2 hours |
| 4 | Change auto_commit default to False | `engine.py` (OrchestratorConfig) | MEDIUM | 5 min |
| 5 | Fix git_user_email placeholder | `engine.py` (OrchestratorConfig) | MEDIUM | 30 min (git config lookup) |

### 8.2 Recommended Changes (Should be included in Phases 1-2)

| # | Change | File | Severity | Effort |
|---|--------|------|----------|--------|
| 6 | Add circular dependency detection (topological sort on load) | `objectives.py` | HIGH | 2 hours |
| 7 | Add reverse dependency index to ProjectObjectives | `objectives.py` | MEDIUM | 1 hour |
| 8 | Define PipelineDispatch Protocol for pipeline_factory | `pipeline_adapter.py` | MEDIUM | 30 min |
| 9 | Improve `_check_criterion` (require ALL words, not ANY) | `engine.py` | MEDIUM | 1 hour |
| 10 | Document dual hook registry pattern | NEW: `docs/` or inline docstrings | LOW | 1 hour |

### 8.3 Deferred Changes (Phase 3+)

| # | Change | Phase | Reason for Deferral |
|---|--------|-------|---------------------|
| 11 | DependencyGraph class (topological sort, cycle detection, reverse index) | 3 | Foundation for hook cascades; must come before hooks |
| 12 | TaskSpawnHook dynamic objective creation | 3 | Requires dependency graph + add_objective() method |
| 13 | Parallel dispatch | 4 | Incompatible with YAML storage; needs storage redesign |
| 14 | Git worktrees | 4 | Requires PipelineEngine working-directory awareness |
| 15 | Performance testing (50+ objectives) | 5 | Depends on Phase 3-4 stability |

### 8.4 Process Recommendations

| # | Recommendation | Rationale |
|---|----------------|-----------|
| 16 | Phase 1 and Phase 2 should be developed together | Phase 2 wiring depends on Phase 1 orchestrator; parallel development risks integration issues |
| 17 | Create a feature branch `feature/project-orchestration-v2` from `feature/pipeline-orchestration-v1` | Isolates V2 work; V1 can merge independently |
| 18 | Sprint cadence: 2-week sprints, demo at end of each | Phase 1 = Sprint 1-2; Phase 2 = Sprint 3; Phase 3 = Sprint 4+ |
| 19 | Add a quality gate: "All recommended changes must be merged before Phase 3 begins" | Prevents accruing technical debt in the foundation |

---

## 9. Scoring Matrix

| Dimension | Score | Details |
|-----------|-------|---------|
| **Architecture** | 7/10 | Clean separation (objectives, orchestrator, supervisors). Gaps: no reverse index, no circuit breaker fix, no adapter |
| **Feasibility** | 7/10 | Phases 1-2: 20-30 hours, realistic. Phases 3-5: need fundamental re-design |
| **Risk Management** | 5/10 | Risk matrix is comprehensive but misses the CircuitBreaker bug, YAML corruption risk, and auto_commit contradiction |
| **Code Quality** | 8/10 | Examples follow GAIA patterns (dataclasses, ABC, existing hook patterns). Clean, well-documented |
| **Test Strategy** | 8/10 | Per-phase test plan with clear mock strategies. Integration tests defined. Could add property-based tests for objectives model |
| **Documentation** | 7/10 | V2 is thorough. Missing: dual-registry documentation, adapter rationale, Excel metaphor design doc |
| **Open Question Resolution** | 6/10 | 4 of 6 questions had recommendations but no decisions. This review provides decisions for all 6 |

### Weighted Overall Score: 6.5/10

| Dimension | Weight | Score | Weighted |
|-----------|--------|-------|----------|
| Architecture | 25% | 7 | 1.75 |
| Feasibility | 20% | 7 | 1.40 |
| Risk Management | 20% | 5 | 1.00 |
| Code Quality | 15% | 8 | 1.20 |
| Test Strategy | 10% | 8 | 0.80 |
| Documentation | 5% | 7 | 0.35 |
| Open Questions | 5% | 6 | 0.30 |
| **TOTAL** | **100%** | | **6.80** |

Rounded to **6.5/10** to reflect the critical CircuitBreaker bug and YAML corruption risk that must be resolved.

---

## Appendix A: Files Verified During Review

| File | Exists | Lines | Notes |
|------|--------|-------|-------|
| `src/gaia/pipeline/engine.py` | Yes | 1,373 | PipelineEngine, 4 phases, hook integration |
| `src/gaia/hooks/base.py` | Yes | 373 | BaseHook, HookContext, HookResult, HookPriority, HookEvent |
| `src/gaia/hooks/registry.py` | Yes | 436 | HookRegistry, HookExecutor, HookExecutionRecord |
| `src/gaia/hooks/production/quality_hooks.py` | Yes | 445 | QualityGateHook, DefectExtractionHook, PipelineNotificationHook, ChronicleHarvestHook |
| `src/gaia/hooks/production/context_hooks.py` | Yes | 351 | ContextInjectionHook, OutputProcessingHook |
| `src/gaia/hooks/production/validation_hooks.py` | Yes | 284 | PreActionValidationHook, PostActionValidationHook |
| `src/gaia/pipeline/sse_hooks.py` | Yes | 230 | 5 SSE hooks, all verified working (12 tests) |
| `src/gaia/pipeline/supervision/` | NO | N/A | Does not exist |
| `src/gaia/quality/supervisor.py` | Yes | N/A | SupervisorAgent (separate from pipeline) |
| `src/gaia/resilience/circuit_breaker.py` | Yes | N/A | CircuitBreaker with hybrid call pattern |
| `src/gaia/state/nexus.py` | Yes | N/A | NexusService singleton, Chronicle persistence |

## Appendix B: Defect Summary

| ID | Severity | Type | File | Description |
|----|----------|------|------|-------------|
| D-1 | CRITICAL | Bug | `supervisors/git.py:1498` | CircuitBreaker called but result never invoked |
| D-2 | HIGH | Data Loss | `objectives.py:291-292` | Non-atomic YAML write risks corruption on crash |
| D-3 | MEDIUM | Contradiction | `engine.py:425` | auto_commit=True in code, False in recommendation |
| D-4 | MEDIUM | Invalid Value | `engine.py:428` | git_user_email = "orchestrator@gai" (truncated) |
| D-5 | MEDIUM | Design Gap | `objectives.py` | No circular dependency detection |
| D-6 | MEDIUM | Performance | `objectives.py` | No reverse dependency index (O(n) per state change) |
| D-7 | LOW | Design Gap | `engine.py:676-687` | _check_criterion keyword matching fragile |
| D-8 | LOW | Documentation | N/A | Dual hook registry pattern undocumented |

---

**Document Version:** 1.0
**Created:** 2026-04-26
**Reviewer:** Taylor Kim, Senior Quality Management Specialist
**Status:** READY FOR PLAN REVISION -- 5 required changes block developer handoff
