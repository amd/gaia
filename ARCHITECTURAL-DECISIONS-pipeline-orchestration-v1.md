# Architectural Decision Record: Pipeline Orchestration Branch

**Document Type:** Architectural Decision Record (ADR)  
**Branch:** `feature/pipeline-orchestration-v1`  
**Date:** 2026-04-11  
**Prepared By:** Jordan Blake, Principal Software Engineer & Technical Lead  
**Status:** Decisions Ready for Implementation  

---

## Executive Summary

This document provides architectural decisions for 4 critical issues identified in the pipeline orchestration branch. These decisions shape the long-term maintainability and architectural coherence of the GAIA agent system.

**Decisions Made:**
1. **ARCH-1:** Python classes are the permanent architecture for pipeline stages
2. **WIRE-3:** PipelineOrchestrator is sufficient; routing-level AgentOrchestrator is NOT needed
3. **INT-2:** Two-registry pattern with clear separation of concerns
4. **Cross-Cutting Review:** Implementation plans are sound with minor adjustments required

---

## Decision 1: ARCH-1 - Python-Class vs. MD-Config Agent Architecture

### Problem Statement

Phase 5 built Python classes (`DomainAnalyzer(Agent)`, `WorkflowModeler(Agent)`, etc.) rather than MD-config files (`config/agents/domain-analyzer.md`) that `agent-ecosystem-design-spec.md` Section 5 specified. This creates a discrepancy between design spec and implementation.

### Options Considered

**Option A: Python Classes Are Permanent (SELECTED)**
- Update `agent-ecosystem-design-spec.md` Section 5 to reflect Python-class approach
- Document advantages: type safety, IDE support, runtime validation, testability
- Keep MD-config files in `config/agents/` for registry discovery with `pipeline.entrypoint` pointers

**Option B: MD-Config Is Still Target**
- Complete Tasks 1-6 of `senior-dev-work-order.md`
- Migrate Python classes to MD-config format
- Build runtime MD parser and prompt loader

### Decision: Option A - Python Classes Are Permanent

**Rationale:**

1. **Type Safety & IDE Support:**
   ```python
   # Python class - IDE autocomplete, type checking, refactoring support
   class DomainAnalyzer(Agent):
       def analyze(self, task: str) -> Dict[str, Any]:
           # Type hints, method completion, navigation
           ...
   
   # MD-config - string-based, no IDE support
   # config/agents/domain-analyzer.md
   # system_prompt: "Analyze the domain..."
   # No type checking, no refactoring tools
   ```

2. **Runtime Validation:**
   ```python
   # Python class can validate at instantiation
   def __init__(self, **kwargs):
       kwargs.setdefault("model_id", "Qwen3.5-35B-A3B-GGUF")
       kwargs.setdefault("max_steps", 50)
       super().__init__(**kwargs)
       # Runtime validation of configuration
   ```

3. **Testability:**
   ```python
   # Python classes can be unit tested directly
   def test_domain_analyzer():
       analyzer = DomainAnalyzer(model_id="test-model")
       result = analyzer.analyze("Build a calculator")
       assert result["primary_domain"] == "mathematics"
   
   # MD-config requires full LLM invocation for testing
   ```

4. **Consistency with GAIA Patterns:**
   - All existing agents (`ChatAgent`, `CodeAgent`, `JiraAgent`) use Python classes
   - Tool registration via `@tool` decorator is Python-native
   - State management is Python-class based

5. **Hybrid Approach Already Documented:**
   - Phase 6 commit `41ee396` established ADR-001 hybrid pattern
   - MD configs in `config/agents/` for registry discovery
   - `pipeline.entrypoint` field points to Python class
   - Best of both worlds: discovery + implementation

### Implementation Guidance

**Update `agent-ecosystem-design-spec.md` Section 5:**

```markdown
## 5. Agent Implementation Pattern

Phase 5 established Python classes as the canonical implementation pattern for pipeline agents:

```python
from gaia.agents.base.agent import Agent
from gaia.agents.base.tools import tool

class DomainAnalyzer(Agent):
    """Stage 1: Domain Analysis - Identifies knowledge domains in tasks."""
    
    def __init__(self, **kwargs):
        kwargs.setdefault("model_id", "Qwen3.5-35B-A3B-GGUF")
        kwargs.setdefault("max_steps", 50)
        super().__init__(**kwargs)
    
    def _register_tools(self):
        @tool
        def analyze_domain(task: str) -> Dict[str, Any]:
            """Analyze task to identify knowledge domains."""
            ...
```

**Registry Discovery (ADR-001 Hybrid Pattern):**

MD config files in `config/agents/` serve as discovery manifests:

```yaml
# config/agents/domain-analyzer.md (discovery manifest, not implementation)
---
id: domain-analyzer
name: Domain Analyzer
version: 1.0.0
capabilities:
  - domain-analysis
  - requirements-extraction
pipeline:
  entrypoint: "src/gaia/pipeline/stages/domain_analyzer.py::DomainAnalyzer"
---
```

The `AgentRegistry` reads MD files for discovery, then imports Python classes via `pipeline.entrypoint`.

### Consequences

**Positive:**
- Type safety and IDE support for all pipeline stage development
- Direct unit testing without LLM invocation
- Consistent with existing GAIA agent patterns
- Clear separation: MD for discovery, Python for implementation

**Negative:**
- Requires updating design spec to reflect reality
- Two-file pattern (MD + Python) may confuse new contributors
- Mitigation: Document ADR-001 hybrid pattern clearly

---

## Decision 2: WIRE-3 - AgentOrchestrator Scope

### Problem Statement

`PipelineOrchestrator` was delivered as a superset of the originally-scoped `AgentOrchestrator`. The routing-level `AgentOrchestrator` (for dynamic agent selection in `RoutingAgent`) was deprioritized.

### Options Considered

**Option A: PipelineOrchestrator Is Sufficient (SELECTED)**
- Pipeline-level orchestration resolves the core need
- Routing-level dynamic selection is handled by `RoutingEngine` + `AgentRegistry`
- Close issue as resolved

**Option B: Routing-Level AgentOrchestrator Still Needed**
- Implement as thin adapter:
  ```python
  class AgentOrchestrator:
      def select_agent(self, task, routing_context):
          pipeline_orchestrator = PipelineOrchestrator()
          agent_id = pipeline_orchestrator.select_agent(task)
          return AgentRegistry.get(agent_id).factory()
  ```

### Decision: Option A - PipelineOrchestrator Is Sufficient

**Rationale:**

1. **Separation of Concerns:**
   - `PipelineOrchestrator` (this branch): 5-stage pipeline execution with gap detection
   - `RoutingEngine` (existing): Defect-based routing to agents and phases
   - `RoutingAgent` (existing): LLM-powered natural language routing
   - Each has a distinct purpose; no need for consolidation

2. **Architecture Layers:**
   ```
   Layer 4: PipelineOrchestrator
            └─> Coordinates 5-stage pipeline with gap detection
   
   Layer 3: RoutingAgent (LLM-powered)
            └─> Routes natural language requests to appropriate agents
   
   Layer 2: RoutingEngine (rule-based)
            └─> Routes defects to agents based on type/severity
   
   Layer 1: AgentRegistry
            └─> Discovers and instantiates agents
   ```

3. **No Overlapping Responsibility:**
   - `PipelineOrchestrator` answers: "How do I execute a 5-stage pipeline?"
   - `RoutingEngine` answers: "Given a defect, which agent handles it?"
   - `RoutingAgent` answers: "Given a task description, which agent should I use?"
   - No duplication; each answers a different question

4. **Implementation Plan Evidence:**
   - Senior developer's implementation plans show no need for routing-level `AgentOrchestrator`
   - WIRE-1 focuses on resilience wiring in `RoutingEngine` (correct)
   - B3-C focuses on UI integration (correct)
   - No implementation plan references routing-level `AgentOrchestrator`

### Implementation Guidance

**Close WIRE-3 as resolved.** No implementation required.

**Update `branch-change-matrix.md` Open Item 1:**
```markdown
OI-1: AgentOrchestrator scope
Status: RESOLVED
Resolution: PipelineOrchestrator delivers 5-stage orchestration with gap detection.
            Routing-level dynamic selection is handled by RoutingEngine (rule-based)
            and RoutingAgent (LLM-powered). No separate AgentOrchestrator needed.
```

### Consequences

**Positive:**
- Avoids unnecessary abstraction layer
- Clear responsibility boundaries
- Reduces codebase complexity

**Negative:**
- None identified

---

## Decision 3: INT-2 - Registry Naming Collision

### Problem Statement

Both this branch and PR #720 created `src/gaia/agents/registry.py` from scratch with incompatible designs:
- **PR #720:** UI-facing, 3-source discovery (builtin, custom Python, YAML manifests)
- **This branch:** Pipeline-facing, YAML-only from `config/agents/`, capability-based selection

### Options Considered

**Option A: Two-Registry Pattern (SELECTED)**
- Rename our registry to `PipelineAgentRegistry`, relocate to `src/gaia/pipeline/agent_registry.py`
- Preserves PR #720's user-facing naming
- Clear separation of concerns (UI vs pipeline)
- Both registries can coexist with different purposes

**Option B: Rename PR #720's to `AgentDiscovery`**
- Conflicts with PR #720's established naming
- Requires coordination with itomek
- More disruptive to existing work

### Decision: Option A - Two-Registry Pattern

**Rationale:**

1. **Separation of Concerns:**
   ```python
   # PR #720's AgentRegistry (agents/registry.py)
   # Purpose: UI-facing agent discovery and instantiation
   # Answers: "Given agent ID user chose, give me factory"
   # Method: registry.get(agent_id).factory()
   
   # Our PipelineAgentRegistry (pipeline/agent_registry.py)
   # Purpose: Pipeline-facing capability-based selection
   # Answers: "Given task, which agent should handle it?"
   # Method: select_agent(task, phase, state) -> returns agent_id
   ```

2. **Bridge Pattern:**
   ```python
   # PipelineOrchestrator selects agent by capability
   pipeline_registry = PipelineAgentRegistry()
   agent_id = pipeline_registry.select_agent(task, phase)
   
   # Then instantiates via PR #720's registry
   agent_registry = AgentRegistry()
   agent = agent_registry.get(agent_id).factory()
   ```

3. **Minimal Conflict Surface:**
   - Our branch: 1 file to relocate (`src/gaia/agents/registry.py` -> `src/gaia/pipeline/agent_registry.py`)
   - Update import paths in:
     - `src/gaia/pipeline/routing_engine.py`
     - `src/gaia/pipeline/orchestrator.py` (if applicable)
     - Test files

4. **Future-Proof:**
   - UI registry can evolve independently
   - Pipeline registry can add pipeline-specific features
   - No coupling between the two

### Implementation Guidance

**Relocate Registry:**
```bash
# Move file
mv src/gaia/agents/registry.py src/gaia/pipeline/agent_registry.py

# Update class name
# In src/gaia/pipeline/agent_registry.py:
# class AgentRegistry -> class PipelineAgentRegistry
```

**Update Import Paths:**
```python
# In src/gaia/pipeline/routing_engine.py
# Old:
from gaia.agents.registry import AgentRegistry

# New:
from gaia.pipeline.agent_registry import PipelineAgentRegistry
```

**Update Module Exports:**
```python
# Add to src/gaia/pipeline/__init__.py
from .agent_registry import PipelineAgentRegistry

__all__ = ["PipelineAgentRegistry", ...]
```

### Consequences

**Positive:**
- Clear separation of concerns
- No coordination required with PR #720 author
- Both registries can evolve independently

**Negative:**
- One file to relocate
- Import paths to update
- Minor refactoring effort

---

## Cross-Cutting Review: Senior Developer Implementation Plans

### Overall Assessment

The senior developer's implementation plans are **ARCHITECTURALLY SOUND** with minor adjustments recommended. The plans demonstrate:
- Clear understanding of the codebase
- Appropriate risk assessment
- Comprehensive test strategy
- Proper sequencing of changes

### Issue-by-Issue Review

#### B3-C: Agent UI Pipeline Integration

**Assessment:** APPROVED with minor adjustment

**Strengths:**
- SSE streaming pattern matches existing GAIA patterns
- Backend/frontend separation is clean
- Test strategy covers both unit and integration scenarios

**Adjustment Required:**
- **Section Task 1:** The `run_pipeline` endpoint should use `asyncio.to_thread()` instead of `run_in_executor()` for Python 3.9+ compatibility:
  ```python
  # Recommended pattern:
  result = await asyncio.to_thread(
      lambda: orchestrator.run_pipeline(task_description=request.task, auto_spawn=request.auto_spawn)
  )
  ```

**Architecture Note:** This integration correctly positions `PipelineOrchestrator` as a backend service, not a routing concern.

---

#### WIRE-1: Resilience Primitives Wiring

**Assessment:** APPROVED with configuration tuning

**Strengths:**
- Correctly identifies `RoutingEngine` as the target for resilience wiring
- Decorator approach is cleaner than inline wrappers
- Monitoring metrics (`get_resilience_stats()`) are essential

**Configuration Tuning Recommendation:**
```python
# Adjust thresholds based on routing engine characteristics
_routing_circuit_breaker = CircuitBreaker(
    CircuitBreakerConfig(
        failure_threshold=7,      # Increased from 5 (routing is read-heavy)
        recovery_timeout=20.0,    # Decreased from 30 (faster recovery acceptable)
        success_threshold=2,
    )
)

_routing_bulkhead = Bulkhead(
    BulkheadConfig(
        max_concurrency=20,       # Increased from 10 (routing should be fast)
        acquire_timeout=3.0,      # Decreased from 5 (fail fast on contention)
    )
)
```

**Architecture Note:** Resilience wiring in `RoutingEngine` is correct. Do NOT wire in `PipelineExecutor` - that's a different failure domain.

---

#### ARCH-2: Capability Vocabulary Migration

**Assessment:** APPROVED

**Strengths:**
- Migration script approach is correct
- Manual review step acknowledges script limitations
- Validation in registry prevents future drift

**Architecture Note:** Consider adding a CI/CD check for capability vocabulary drift:
```yaml
# .github/workflows/validate-capabilities.yml
- name: Validate Agent Capabilities
  run: python util/validate-capabilities.py
```

---

### Architectural Patterns Being Followed

1. **Single Responsibility Principle:**
   - `PipelineOrchestrator`: Pipeline coordination
   - `RoutingEngine`: Defect-based routing
   - `PipelineAgentRegistry`: Capability-based selection
   - Each class has one reason to change

2. **Dependency Inversion:**
   - Resilience primitives are abstractions
   - `RoutingEngine` depends on abstractions, not concrete implementations
   - Easy to swap resilience implementations

3. **Separation of Concerns:**
   - Backend (Python) and Frontend (TypeScript) are cleanly separated
   - UI registry and pipeline registry have distinct purposes
   - No leaking abstractions

### Opportunities to Simplify

1. **B3-C Backend:**
   - Consider consolidating `POST /api/v1/pipeline/run` and `GET /api/v1/pipeline/status` into a single SSE endpoint
   - Current pattern creates two endpoints when one could serve both purposes

2. **WIRE-1:**
   - Consider creating a `ResilientRoutingEngine` decorator class instead of modifying `RoutingEngine` directly
   - This would make resilience an opt-in concern

3. **ARCH-2:**
   - Migration script could be replaced with a simple sed/awk one-liner for the capability mappings
   - Full Python script may be overkill for string replacement

### Risks Identified

1. **B3-C: Thread Pool Blocking**
   - Risk: `PipelineOrchestrator.run_pipeline()` may block for 30+ seconds
   - Mitigation: Add timeout handling and progress streaming
   - Recommended: Stream SSE events during execution, not just at stage boundaries

2. **WIRE-1: Circuit Breaker Calibration**
   - Risk: Circuit may open during normal operation if thresholds are too aggressive
   - Mitigation: Start with monitoring-only mode, collect metrics, then enable protection
   - Recommended: Add `monitoring_only=True` flag for initial deployment

3. **INT-2: Import Path Confusion**
   - Risk: Developers may import from wrong registry
   - Mitigation: Add deprecation warning to old import path
   - Recommended: Add `__getattr__` to `gaia/agents/__init__.py` with migration hint

---

## Recommendations for Testing-Quality-Specialist

### Priority 1: Verify Core Architecture

1. **Test Two-Registry Separation:**
   ```python
   def test_pipeline_registry_is_isolated():
       # Ensure PipelineAgentRegistry doesn't import from agents.registry
       from gaia.pipeline import agent_registry
       assert "gaia.agents.registry" not in get_imports(agent_registry)
   ```

2. **Test Resilience Wrapping:**
   ```python
   def test_routing_engine_is_resilient():
       # Verify circuit breaker trips after failures
       engine = RoutingEngine()
       for i in range(7):
           with pytest.raises(Exception):
               engine.route_defect({"description": "forced failure"})
       assert engine._routing_circuit_breaker.is_open
   ```

3. **Test Pipeline SSE Streaming:**
   ```python
   def test_pipeline_endpoint_streams_events():
       # Verify SSE events are generated
       response = client.post("/api/v1/pipeline/run", json={"task": "test"})
       assert response.headers["content-type"] == "text/event-stream"
   ```

### Priority 2: Add CI/CD Guards

1. **YAML Frontmatter Check:**
   ```bash
   # Add to CI pipeline
   python util/validate-frontmatter.py docs/spec/*.md
   ```

2. **Capability Vocabulary Check:**
   ```bash
   # Add to CI pipeline
   python util/validate-capabilities.py config/agents/*.yaml
   ```

3. **Import Path Validation:**
   ```bash
   # Ensure no imports from old registry path
   grep -r "from gaia.agents.registry import" src/gaia/pipeline/
   ```

### Priority 3: Documentation Updates

1. **Update `agent-ecosystem-design-spec.md` Section 5** with Python-class pattern
2. **Add ADR-001** to `docs/spec/architecture-decisions/` documenting hybrid pattern
3. **Update `branch-change-matrix.md`** with resolved status for ARCH-1 and WIRE-3

---

## Summary of Decisions

| Issue | Decision | Implementation Required |
|-------|----------|------------------------|
| **ARCH-1** | Python classes are permanent | Update design spec Section 5 |
| **WIRE-3** | PipelineOrchestrator is sufficient | Close issue, update matrix |
| **INT-2** | Two-registry pattern | Relocate `registry.py` to `pipeline/agent_registry.py` |
| **Cross-Cutting** | Plans approved with minor adjustments | See recommendations above |

---

**Document Version:** 1.0  
**Prepared By:** Jordan Blake, Principal Software Engineer & Technical Lead  
**Date:** 2026-04-11  
**Next Reviewer:** testing-quality-specialist
