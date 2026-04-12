# GAIA Pipeline Orchestration — Refined Demo Plan v2.0

**Produced by:** Dr. Sarah Kim, Technical Product Strategist & Engineering Lead
**Status:** REFINED — addresses all CRITICAL and HIGH gaps from quality review (score 64.70/100, LOOP_BACK)
**Branch:** `feature/pipeline-orchestration-v1`
**Date:** 2026-03-30

---

## Sequential Thinking Trace

The following reasoning trace documents the strategic corrections applied to this plan. Each
thought corresponds to one gap identified in the quality review.

**Thought 1 — Template namespace collision (CRITICAL)**
The original plan used `"STANDARD"`, `"RAPID"`, and `"ENTERPRISE"` as template names in
`PipelineEngine` initialization snippets. These names belong to `QualityTemplate`
(`quality/templates.py`) and are only valid when calling `get_template()` from the quality
scoring subsystem. The `PipelineEngine.initialize()` method reads the template name and passes
it to `get_recursive_template()` (`pipeline/recursive_template.py`), which only recognizes
`"generic"`, `"rapid"`, and `"enterprise"` (all lowercase). Every engine code snippet in the
original plan was therefore broken at the import boundary. Correction: all engine snippets must
use `"generic"`, `"rapid"`, or `"enterprise"`. A dedicated explainer section is required.

**Thought 2 — Async execution context missing (CRITICAL)**
All snippets containing `await` were presented as bare top-level Python, which raises a
`SyntaxError` outside of an async context. Demo attendees running these in a terminal or
plain script would see immediate failures. Correction: wrap every engine snippet in
`async def main(): ... asyncio.run(main())` and annotate Jupyter cell usage explicitly.

**Thought 3 — AMD/hardware audience absent (HIGH)**
The original plan made no mention of how this system benefits AMD Ryzen AI NPU users.
`concurrent_loops` and `worker_pool_size` in `PipelineEngine.__init__` map directly to
bounded concurrency on NPU worker threads. Local execution means zero cloud data egress,
which is a first-order concern for enterprise AMD deployments. Correction: add a dedicated
AMD/hardware section with two talking points and at least one code reference.

**Thought 4 — Failure/error mode not demonstrated (HIGH)**
The original plan showed only happy-path scenarios. A quality score of 0.99 is very difficult
to satisfy with minimal artifacts, guaranteeing `LOOP_BACK`. This scenario must be shown
explicitly so that audiences understand the self-correcting nature of the pipeline. Correction:
add Act 7 Scenario B with `quality_threshold=0.99` forcing the `LOOP_BACK` decision and
displaying the chronicle entry.

**Thought 5 — Documentation file list absent (HIGH)**
The original plan referenced documentation without specifying exact file paths or `docs.json`
placement. Correction: enumerate the four required MDX files with exact paths and their
`docs.json` navigation section.

**Thought 6 — Prerequisites section absent (MEDIUM)**
Attendees arriving at Act 1 without context could hit import errors or confusion about
`agents_dir`. Correction: add a prerequisites box before Act 1.

**Thought 7 — Single demo ordering (MEDIUM)**
Engineering audiences want to walk through the system bottom-up. Executive/stakeholder
audiences need the "so what" first. Correction: provide two named orderings.

**Thought 8 — Decision framework: three-audience structure**
Use a three-track audience model (Engineering, Product/Leadership, AMD/Hardware) to structure
talking points across all eight acts. Each act carries context-appropriate annotation.

---

## Decision Framework: Audience-Driven Demo Strategy

| Criterion | Engineering Track | Product/Leadership Track | AMD/Hardware Track |
|---|---|---|---|
| Primary question | "How does it work?" | "What does it deliver?" | "Why on-device?" |
| Entry point | Act 1 — Architecture | Act 7 — Live run | AMD section |
| Success metric | Code compiles, tests pass | Pipeline completes autonomously | Zero egress confirmed |
| Key risk addressed | State machine correctness | Delivery velocity | Data sovereignty |
| Recommended depth | All 8 acts, 90 min | 4 acts, 30 min | 20 min standalone |

---

## Prerequisites

Before running any code snippet in this plan, verify the following:

**Python version:** 3.11 or higher (required for `asyncio.TaskGroup` compatibility used
internally by `LoopManager`).

**Installation:**
```bash
cd /path/to/gaia
uv venv && uv pip install -e ".[dev]"
```

**`agents_dir` behavior:** When `agents_dir=None` (the default), `AgentRegistry` scans
`.claude/agents/` relative to the working directory. Pass an explicit path only if running
from outside the repository root. For the demo, `agents_dir=None` is always correct when
the shell's current directory is the repository root.

**Lemonade server:** NOT required for pipeline orchestration demos. The `PipelineEngine`
does not call an LLM backend during these acts. Acts 1–8 run entirely with mock/simulated
quality scores.

**Import verification:**
```python
from gaia.pipeline import (
    PipelineEngine, PipelineContext, PipelineState,
    DecisionEngine, DecisionType,
    AuditLogger, AuditEventType,
    RecursivePipelineTemplate,
)
print("All pipeline imports OK")
```

---

## Template Systems Explainer

Two distinct template systems exist in this codebase. They share vocabulary but serve
entirely different layers. Conflating them is the most common source of confusion.

### System A — QualityTemplate (`src/gaia/quality/templates.py`)

Used exclusively by `QualityScorer` to govern pass/fail thresholds and agent execution
sequences within the scoring subsystem.

**Valid names:** `"STANDARD"`, `"RAPID"`, `"ENTERPRISE"`, `"DOCUMENTATION"` (uppercase)

**Import path:**
```python
from gaia.quality.templates import get_template, QualityTemplate
qt = get_template("STANDARD")   # QualityTemplate object
print(qt.threshold)             # 0.90
print(qt.auto_pass)             # 0.95
print(qt.agent_sequence)        # ['planning-analysis-strategist', ...]
```

**What it controls:** `auto_pass`, `auto_fail`, `manual_review_range`, and the ordered
`agent_sequence` list that `QualityScorer` walks when evaluating artifacts.

**Never use these names with `PipelineEngine`.** Passing `"STANDARD"` to `PipelineConfig`
will cause `get_recursive_template("STANDARD")` to raise `KeyError` and fall back silently
to `"generic"`, which is confusing and incorrect.

### System B — RecursivePipelineTemplate (`src/gaia/pipeline/recursive_template.py`)

Used by `PipelineEngine` to drive phase-level agent selection, routing rules, and loop-back
configuration for the overall orchestration lifecycle.

**Valid names:** `"generic"`, `"rapid"`, `"enterprise"` (lowercase)

**Import path:**
```python
from gaia.pipeline.recursive_template import get_recursive_template, RECURSIVE_TEMPLATES
print(list(RECURSIVE_TEMPLATES.keys()))  # ['generic', 'rapid', 'enterprise']

tmpl = get_recursive_template("generic")
print(tmpl.quality_threshold)   # 0.90
print(tmpl.max_iterations)      # 10
print(tmpl.agent_categories)    # {'planning': [...], 'development': [...], ...}
```

**What it controls:** Which agents are active per phase, how many iterations are allowed,
and which `RoutingRule` conditions trigger phase loop-backs.

### Summary table

| Property | QualityTemplate (System A) | RecursivePipelineTemplate (System B) |
|---|---|---|
| Module | `gaia.quality.templates` | `gaia.pipeline.recursive_template` |
| Used by | `QualityScorer` | `PipelineEngine` |
| Valid names | STANDARD, RAPID, ENTERPRISE, DOCUMENTATION | generic, rapid, enterprise |
| Case | Uppercase | Lowercase |
| Controls | Artifact scoring thresholds | Phase agents and routing rules |

---

## Two Demo Orderings

### Ordering 1 — Engineering Deep-Dive (90 minutes, 8 acts)

Recommended for: engineering teams, technical reviewers, contributors.

```
Act 1 → Act 2 → Act 3 → Act 4 → Act 5 → Act 6 → Act 7A → Act 7B → Act 8
```

Walk the system bottom-up: state machine first, then components, then the integrated
engine, then failure modes. Every snippet is run live.

### Ordering 2 — Executive / Stakeholder Overview (30 minutes, 4 acts)

Recommended for: product leadership, business stakeholders, AMD partner audiences.

```
Act 7A (happy path) → Act 7B (failure/loop-back) → Act 6 (audit trail) → Act 3 (architecture) → Q&A
```

Open with the working demo so the audience sees autonomous pipeline execution immediately.
Follow with the failure scenario to demonstrate self-correction. Show the audit trail for
governance and compliance messaging. Close with the architecture diagram to anchor the
"how" for curious stakeholders. Keep all code execution pre-baked to avoid live typing delays.

For AMD hardware audiences, insert the AMD/NPU section (below) immediately after Act 7A.

---

## Act 1 — System Architecture and Import Map

**Duration:** 10 minutes
**Audience annotation:** Engineering (required) | Product (optional) | AMD (skip)

### 1.1 Package layout

```
src/gaia/pipeline/
    __init__.py             # Public API, lazy imports for complex deps
    engine.py               # PipelineEngine — main orchestrator
    state.py                # PipelineState, PipelineContext, PipelineSnapshot
    decision_engine.py      # DecisionEngine, Decision, DecisionType
    loop_manager.py         # LoopManager, LoopConfig, LoopStatus
    recursive_template.py   # RecursivePipelineTemplate (System B — engine templates)
    routing_engine.py       # RoutingEngine, RoutingDecision
    phase_contract.py       # PhaseContract, ContractTerm, ValidationResult
    audit_logger.py         # AuditLogger — hash-chain tamper detection
    defect_router.py        # DefectRouter, Defect, DefectType, DefectSeverity
    defect_remediation_tracker.py  # DefectRemediationTracker
    defect_types.py         # DefectType taxonomy (comprehensive)
    template_loader.py      # TemplateLoader — YAML-based template loading

src/gaia/quality/
    templates.py            # QualityTemplate (System A — scoring templates)
    scorer.py               # QualityScorer — artifact evaluation
    models.py               # QualityWeightConfig, QualityDimension
    weight_config.py        # Weight profiles (balanced, security_heavy, ...)
    validators/             # Per-dimension validators (code, docs, tests, ...)
```

### 1.2 Data flow (4 phases)

```
PipelineContext (immutable goal + config)
        |
        v
  [PLANNING phase]  <---------+
        |                     |
        v                     |  LOOP_BACK
  [DEVELOPMENT phase]         |  (quality < threshold
        |                     |   AND iteration < max)
        v                     |
  [QUALITY phase] -------> QualityScorer
        |                     |
        v                     |
  [DECISION phase] -----------+
        |
        v (COMPLETE or FAIL)
  PipelineSnapshot + Chronicle
```

### 1.3 Talking points

Engineering: "The state machine enforces valid transitions. You cannot reach COMPLETED from
RUNNING without passing through all four phases."

Product: "Every pipeline execution produces an immutable chronicle. That is your audit trail
for compliance, retrospectives, and cost attribution."

---

## Act 2 — State Machine and Context

**Duration:** 8 minutes
**Audience annotation:** Engineering (required) | Product (skip) | AMD (skip)

```python
import asyncio
from gaia.pipeline.state import PipelineState, PipelineContext, PipelineStateMachine


async def main():
    # PipelineContext is frozen — it cannot be mutated after creation.
    context = PipelineContext(
        pipeline_id="demo-act-2",
        user_goal="Add pagination to the user list API endpoint",
        quality_threshold=0.90,
        max_iterations=5,
        concurrent_loops=4,
    )

    machine = PipelineStateMachine(context)

    print(f"Initial state: {machine.current_state.name}")  # INITIALIZING

    machine.transition(PipelineState.READY, "Initialized successfully")
    machine.transition(PipelineState.RUNNING, "Pipeline started")
    machine.set_phase("PLANNING")

    snapshot = machine.snapshot
    print(f"Current state:  {snapshot.state.name}")      # RUNNING
    print(f"Current phase:  {snapshot.current_phase}")   # PLANNING
    print(f"Iteration:      {snapshot.iteration_count}") # 0

    # Demonstrate an artifact being stored
    machine.add_artifact("technical_plan", {"approach": "cursor-based pagination"})
    machine.increment_iteration()

    machine.transition(PipelineState.COMPLETED, "All phases passed")
    print(f"Final state: {machine.current_state.name}")  # COMPLETED
    print(f"Chronicle entries: {len(machine.chronicle)}")


asyncio.run(main())
```

**Key insight for engineering audience:** `PipelineContext` is a frozen dataclass. Passing
`quality_threshold=0.90` here does not touch `QualityTemplate` (System A). It is stored
directly on the context and consumed by `DecisionEngine.evaluate()` at the end of each cycle.

---

## Act 3 — RecursivePipelineTemplate (System B)

**Duration:** 10 minutes
**Audience annotation:** Engineering (required) | Product (optional) | AMD (optional)

This act demonstrates System B templates. Do not use uppercase names here.

```python
import asyncio
from gaia.pipeline.recursive_template import (
    get_recursive_template,
    RecursivePipelineTemplate,
    RoutingRule,
    RECURSIVE_TEMPLATES,
)


async def main():
    # Show all available engine templates
    print("Available engine templates:", list(RECURSIVE_TEMPLATES.keys()))
    # Output: ['generic', 'rapid', 'enterprise']

    # Load the generic template
    generic = get_recursive_template("generic")
    print(f"\nTemplate: {generic.name}")
    print(f"Quality threshold: {generic.quality_threshold}")  # 0.90
    print(f"Max iterations:    {generic.max_iterations}")     # 10
    print(f"Agent categories:")
    for category, agents in generic.agent_categories.items():
        print(f"  {category}: {agents}")

    # Inspect routing rules
    print(f"\nRouting rules ({len(generic.routing_rules)}):")
    for rule in generic.routing_rules:
        print(f"  condition='{rule.condition}' -> route_to='{rule.route_to}' loop_back={rule.loop_back}")

    # Load the rapid template — lower threshold, fewer iterations
    rapid = get_recursive_template("rapid")
    print(f"\nRapid template threshold: {rapid.quality_threshold}")  # 0.75
    print(f"Rapid template max_iter:  {rapid.max_iterations}")       # 5

    # Load the enterprise template — higher threshold, more reviewers
    enterprise = get_recursive_template("enterprise")
    print(f"\nEnterprise threshold: {enterprise.quality_threshold}")  # 0.95
    print(f"Enterprise quality agents: {enterprise.agent_categories.get('quality', [])}")

    # Demonstrate should_loop_back logic
    should_loop = generic.should_loop_back(
        quality_score=0.82,
        iteration=2,
        has_defects=True,
    )
    print(f"\nShould loop back (score=0.82, iter=2): {should_loop}")  # True

    should_not_loop = generic.should_loop_back(
        quality_score=0.95,
        iteration=2,
        has_defects=False,
    )
    print(f"Should loop back (score=0.95, iter=2): {should_not_loop}")  # False


asyncio.run(main())
```

**Talking points:**

Engineering: "Notice that `should_loop_back` is a pure function on the template. The
`PipelineEngine` calls it during the DECISION phase. The template holds policy; the engine
holds execution."

AMD/hardware: "The `max_iterations` cap on the `rapid` template (5 vs 10 for `generic`)
is deliberate. On NPU-constrained hardware where cycle time matters, you tune this value
to match available compute budget."

---

## Act 4 — Decision Engine

**Duration:** 8 minutes
**Audience annotation:** Engineering (required) | Product (optional) | AMD (skip)

```python
import asyncio
from gaia.pipeline.decision_engine import DecisionEngine, DecisionType


async def main():
    engine = DecisionEngine(config={"critical_patterns": ["security", "injection"]})

    # Scenario 1: quality threshold met on final phase -> COMPLETE
    decision = engine.evaluate(
        phase_name="DECISION",
        quality_score=0.93,
        quality_threshold=0.90,
        defects=[],
        iteration=1,
        max_iterations=10,
        is_final_phase=True,
    )
    print(f"Scenario 1: {decision.decision_type.name}")  # COMPLETE
    print(f"  Reason: {decision.reason}")

    # Scenario 2: quality below threshold, iterations remaining -> LOOP_BACK
    decision = engine.evaluate(
        phase_name="DECISION",
        quality_score=0.72,
        quality_threshold=0.90,
        defects=[
            {"description": "missing unit tests for edge cases", "severity": "medium"},
            {"description": "no docstrings on public methods", "severity": "low"},
        ],
        iteration=2,
        max_iterations=10,
        is_final_phase=True,
    )
    print(f"\nScenario 2: {decision.decision_type.name}")  # LOOP_BACK
    print(f"  Target phase: {decision.target_phase}")      # PLANNING
    print(f"  Defects:      {len(decision.defects)}")

    # Scenario 3: critical defect detected -> PAUSE
    decision = engine.evaluate(
        phase_name="DECISION",
        quality_score=0.85,
        quality_threshold=0.90,
        defects=[{"description": "SQL injection risk in query builder", "severity": "high"}],
        iteration=1,
        max_iterations=10,
        is_final_phase=True,
    )
    print(f"\nScenario 3: {decision.decision_type.name}")  # PAUSE
    print(f"  Critical: {decision.metadata.get('critical')}")

    # Scenario 4: max iterations exceeded -> FAIL
    decision = engine.evaluate(
        phase_name="DECISION",
        quality_score=0.70,
        quality_threshold=0.90,
        defects=[{"description": "persistent test failures", "severity": "medium"}],
        iteration=10,
        max_iterations=10,
        is_final_phase=True,
    )
    print(f"\nScenario 4: {decision.decision_type.name}")  # FAIL


asyncio.run(main())
```

**Key engineering insight:** The decision priority order is fixed in `DecisionEngine.evaluate()`:
1. Critical defects (PAUSE)
2. Quality threshold met (COMPLETE or CONTINUE)
3. Max iterations exceeded (FAIL)
4. Default (LOOP_BACK)

This ordering cannot be changed without modifying the engine. If you need a different priority,
subclass `DecisionEngine` and override `evaluate()`.

---

## Act 5 — AuditLogger and Hash Chain Integrity

**Duration:** 8 minutes
**Audience annotation:** Engineering (required) | Product (optional) | AMD (optional)

```python
import asyncio
from gaia.pipeline.audit_logger import AuditLogger, AuditEventType


async def main():
    audit = AuditLogger(logger_id="demo-pipeline-001")

    # Log a sequence of pipeline events
    audit.log(
        AuditEventType.PIPELINE_START,
        pipeline_id="demo-001",
        user_goal="Add pagination to user list API",
    )
    audit.log(AuditEventType.PHASE_ENTER, phase="PLANNING", inputs_available=["user_goal"])
    audit.log(
        AuditEventType.AGENT_SELECTED,
        agent_id="planning-analysis-strategist",
        capabilities=["requirements_analysis", "roadmap_development"],
    )
    audit.log(
        AuditEventType.AGENT_EXECUTED,
        agent_id="planning-analysis-strategist",
        execution_time_ms=1200,
    )
    audit.log(AuditEventType.PHASE_EXIT, phase="PLANNING", outputs_produced=["technical_plan"])
    audit.log(AuditEventType.QUALITY_EVALUATED, score=0.88, threshold=0.90)
    audit.log(
        AuditEventType.DECISION_MADE,
        decision_type="LOOP_BACK",
        target_phase="PLANNING",
        defect_count=2,
    )

    # Verify hash chain integrity
    is_valid = audit.verify_integrity()
    print(f"Chain integrity valid: {is_valid}")  # True

    # Query events by category
    decisions = audit.query(event_type=AuditEventType.DECISION_MADE)
    print(f"Decision events: {len(decisions)}")

    quality_events = audit.query(event_type=AuditEventType.QUALITY_EVALUATED)
    print(f"Quality events:  {len(quality_events)}")

    # Export to JSON
    chronicle_json = audit.export_json()
    import json
    chronicle = json.loads(chronicle_json)
    print(f"Total events in chronicle: {len(chronicle)}")

    # Demonstrate tamper detection
    if audit._events:
        original_data = audit._events[0].data.copy()
        audit._events[0].data["tampered"] = True
        try:
            audit.verify_integrity()
        except Exception as e:
            print(f"Tamper detected: {type(e).__name__}")
        # Restore
        audit._events[0].data = original_data


asyncio.run(main())
```

**Product/Leadership talking point:** "Every pipeline execution produces a cryptographic
hash chain. Tampering with any event in the log is immediately detectable. This is the
audit trail that satisfies compliance and governance requirements."

---

## Act 6 — Phase Contracts

**Duration:** 7 minutes
**Audience annotation:** Engineering (required) | Product (skip) | AMD (skip)

```python
import asyncio
from gaia.pipeline import (
    create_default_phase_contracts,
    PhaseContractRegistry,
    ContractViolationError,
)
from gaia.pipeline.state import PipelineContext


async def main():
    # Create all four default phase contracts
    contracts = create_default_phase_contracts()
    registry = PhaseContractRegistry()

    for contract in contracts:
        registry.register(contract)

    print(f"Registered contracts: {[c.phase_name for c in contracts]}")

    # Validate PLANNING phase with correct inputs
    context = PipelineContext(
        pipeline_id="demo-contracts",
        user_goal="Refactor authentication module",
        quality_threshold=0.90,
        max_iterations=5,
        concurrent_loops=4,
    )

    planning_contract = registry.get("PLANNING")
    if planning_contract:
        snapshot_data = {"user_goal": context.user_goal, "pipeline_id": context.pipeline_id}
        result = planning_contract.validate_inputs(snapshot_data)
        print(f"PLANNING input validation: {'PASS' if result.is_valid else 'FAIL'}")
        if not result.is_valid:
            for violation in result.violations:
                print(f"  Violation: {violation.message} [{violation.severity.name}]")

    # Show what DEVELOPMENT requires from PLANNING
    dev_contract = registry.get("DEVELOPMENT")
    if dev_contract:
        print(f"\nDEVELOPMENT required inputs:")
        for term in dev_contract.input_terms:
            print(f"  {term.name} ({term.input_type.name}): {term.description}")


asyncio.run(main())
```

---

## Act 7A — Full Pipeline Run (Happy Path)

**Duration:** 12 minutes
**Audience annotation:** Engineering (required) | Product (required — lead with this) | AMD (required)

This act uses the `"generic"` engine template (System B). Do not substitute `"STANDARD"` here.

```python
import asyncio
from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.state import PipelineContext


async def main():
    # Initialize engine with bounded concurrency
    # worker_pool_size=4 maps to 4 concurrent worker threads —
    # see AMD/NPU section for hardware alignment.
    engine = PipelineEngine(
        agents_dir=None,        # scans .claude/agents/ from repo root
        max_concurrent_loops=20,
        worker_pool_size=4,
    )

    context = PipelineContext(
        pipeline_id="demo-happy-path-001",
        user_goal="Add cursor-based pagination to the /users REST endpoint",
        quality_threshold=0.90,
        max_iterations=10,
        concurrent_loops=4,
    )

    # IMPORTANT: template must be a System B name — lowercase only
    await engine.initialize(
        context=context,
        config={
            "template": "generic",          # NOT "STANDARD" — see Template Systems Explainer
            "quality_threshold": 0.90,
            "concurrent_loops": 4,
            "enable_hooks": True,
        },
    )

    print("Pipeline initialized. Starting execution...")
    snapshot = await engine.start()

    print(f"\nFinal state:     {snapshot.state.name}")
    print(f"Iterations:      {snapshot.iteration_count}")
    print(f"Quality score:   {snapshot.quality_score:.2f}" if snapshot.quality_score else "Quality score: N/A")
    print(f"Artifacts:       {list(snapshot.artifacts.keys())}")

    # Read the decision artifact
    decision = snapshot.artifacts.get("decision", {})
    if decision:
        print(f"\nDecision type:   {decision.get('decision_type')}")
        print(f"Decision reason: {decision.get('reason')}")

    # Read the chronicle
    chronicle = engine.get_chronicle()
    print(f"\nChronicle entries: {len(chronicle)}")

    engine.shutdown()


asyncio.run(main())
```

**Talking points by audience:**

Engineering: "Watch the `concurrent_loops` value propagate from `PipelineContext` into
`LoopManager.max_concurrent`. The semaphore in `PipelineEngine._semaphore` is set to
`max_concurrent_loops`. The `_worker_semaphore` is set to `worker_pool_size`. These two
semaphores provide dual-level backpressure."

Product/Leadership: "The pipeline selected its own agents, evaluated quality, and made a
progression decision — all without a human in the loop. The `chronicle` is the complete
event log for that autonomous execution."

AMD/hardware: "See the AMD/NPU section immediately following this act for how these
parameters map to Ryzen AI NPU resource allocation."

---

## AMD / Ryzen AI NPU Section

**Placement in Engineering ordering:** immediately after Act 7A
**Placement in Executive ordering:** immediately after Act 7A, before Act 7B

### Why pipeline orchestration matters on AMD Ryzen AI hardware

**Talking point 1 — Local execution and data sovereignty**

GAIA's pipeline orchestration runs entirely on-device. No agent output, no quality report,
no chronicle event, and no defect description leaves the local machine. For enterprise
customers processing proprietary code, medical records, or financial data, this is a
non-negotiable requirement that cloud-based pipeline orchestration cannot satisfy.

```python
# There is no network call in PipelineEngine.
# QualityScorer.evaluate() runs local validators from gaia/quality/validators/.
# The AuditLogger writes to an in-process list — not a remote endpoint.
from gaia.quality.scorer import QualityScorer
from gaia.pipeline.audit_logger import AuditLogger

scorer = QualityScorer()   # no URL, no API key
audit = AuditLogger()      # no remote sink
# All compute stays on the AMD Ryzen AI device.
```

**Talking point 2 — `concurrent_loops` and `worker_pool_size` align to NPU worker threads**

The `PipelineEngine` constructor exposes two concurrency parameters:

```python
from gaia.pipeline.engine import PipelineEngine

# Ryzen AI 300 series: 50 NPU TOPS available
# Recommended starting point: worker_pool_size = (NPU compute units / task weight)
engine = PipelineEngine(
    max_concurrent_loops=20,   # upper bound on simultaneous pipeline loops
    worker_pool_size=4,        # maps to asyncio worker semaphore — tune to NPU allocation
)
```

`worker_pool_size` controls `self._worker_semaphore = asyncio.Semaphore(worker_pool_size)`.
Each pipeline phase that calls `execute_with_backpressure()` acquires this semaphore before
dispatching to the thread pool. Setting `worker_pool_size` equal to the number of NPU
compute units reserved for this workload prevents resource contention with the LLM inference
stack (Lemonade) running concurrently.

`concurrent_loops` controls the outer `self._semaphore = asyncio.Semaphore(max_concurrent_loops)`.
This limits how many pipeline instances can be active simultaneously. On Ryzen AI hardware
under the Hybrid mode scheduler, this prevents the pipeline engine from starving NPU
bandwidth needed by the active LLM serving context.

**Code reference:**
```python
# src/gaia/pipeline/engine.py, PipelineEngine.__init__
self._semaphore = asyncio.Semaphore(max_concurrent_loops)
self._worker_semaphore = asyncio.Semaphore(worker_pool_size)
```

**Talking point 3 — Rapid template for NPU-constrained scenarios**

When running on devices with lower NPU TOPS (e.g., embedded Ryzen AI configurations),
use the `"rapid"` template (5 max iterations, 0.75 threshold) to reduce total loop count
and match the available compute budget:

```python
await engine.initialize(
    context=context,
    config={
        "template": "rapid",     # 5 iterations max, 0.75 threshold
        "concurrent_loops": 2,   # conservative for constrained NPU
    },
)
```

---

## Act 7B — Failure / Loop-Back Scenario

**Duration:** 10 minutes
**Audience annotation:** Engineering (required) | Product (required) | AMD (recommended)

This scenario deliberately forces a `LOOP_BACK` decision by setting `quality_threshold=0.99`
with minimal artifacts. It demonstrates that the pipeline self-corrects rather than silently
accepting low-quality output.

```python
import asyncio
from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.state import PipelineContext
from gaia.pipeline.decision_engine import DecisionType


async def main():
    engine = PipelineEngine(
        agents_dir=None,
        max_concurrent_loops=10,
        worker_pool_size=2,
    )

    # quality_threshold=0.99 is intentionally unreachable with minimal artifacts.
    # This forces the DecisionEngine to emit LOOP_BACK on the first iteration.
    context = PipelineContext(
        pipeline_id="demo-loop-back-scenario",
        user_goal="Add rate limiting middleware with near-perfect quality",
        quality_threshold=0.99,   # Deliberately high — will not be met
        max_iterations=2,         # Low cap so demo completes quickly
        concurrent_loops=2,
    )

    await engine.initialize(
        context=context,
        config={
            "template": "enterprise",   # System B name — enterprise has 0.95 threshold
                                        # but we override via context.quality_threshold=0.99
            "quality_threshold": 0.99,
            "concurrent_loops": 2,
            "enable_hooks": True,
        },
    )

    print("Starting pipeline with quality_threshold=0.99 (intentionally unreachable)...")
    snapshot = await engine.start()

    print(f"\nFinal state:     {snapshot.state.name}")
    print(f"Iterations run:  {snapshot.iteration_count}")
    print(f"Quality score:   {snapshot.quality_score:.2f}" if snapshot.quality_score else "Quality: N/A")

    # Inspect the decision artifact
    decision_artifact = snapshot.artifacts.get("decision", {})
    if decision_artifact:
        decision_type = decision_artifact.get("decision_type", "UNKNOWN")
        reason = decision_artifact.get("reason", "")
        print(f"\nDecision type:  {decision_type}")
        print(f"Decision reason: {reason}")

        if decision_type == "LOOP_BACK":
            print("\n[CONFIRMED] LOOP_BACK decision observed.")
            print("The pipeline attempted to return to PLANNING for remediation.")
            print("After max_iterations=2, the DecisionEngine transitioned to FAIL")
            print("because the quality threshold was never reachable.")
        elif decision_type == "FAIL":
            print("\n[CONFIRMED] FAIL decision observed.")
            print("The pipeline exhausted its iteration budget without meeting quality_threshold=0.99.")

    # Read the chronicle to show the LOOP_BACK event
    chronicle = engine.get_chronicle()
    print(f"\nChronicle entries: {len(chronicle)}")

    loop_back_events = [
        e for e in chronicle
        if "LOOP_BACK" in str(e) or "loop_back" in str(e).lower()
    ]
    print(f"LOOP_BACK events in chronicle: {len(loop_back_events)}")
    for event in loop_back_events[:3]:
        print(f"  {event}")

    engine.shutdown()


asyncio.run(main())
```

**Narration script:**

"We set `quality_threshold=0.99` — essentially perfect — with only two iterations allowed.
Watch what happens: the pipeline runs PLANNING, DEVELOPMENT, and QUALITY normally. When it
reaches DECISION, the `DecisionEngine` calculates the quality score from the `QualityScorer`
output. With minimal artifacts — no tests, no documentation, just a goal string — the score
will be well below 0.99. The engine issues `LOOP_BACK` with a target of `PLANNING`."

"After `max_iterations=2` with no improvement, the engine issues `FAIL`. But notice: it did
not skip quality enforcement. It did not silently pass. Every failed attempt is recorded in
the chronicle with its quality score and defect list. This is the self-correction mechanism
— not magic, but explicit, auditable, configurable enforcement."

**Product/Leadership framing:** "This is the difference between 'the AI tried its best'
and 'the AI enforced your quality bar.' When the threshold is not met, the pipeline loops
back with the specific defects that caused the failure, giving the next iteration a concrete
remediation target."

---

## Act 8 — Backpressure and Concurrent Execution

**Duration:** 8 minutes
**Audience annotation:** Engineering (required) | Product (skip) | AMD (optional)

```python
import asyncio
from gaia.pipeline.engine import PipelineEngine
from gaia.pipeline.state import PipelineContext


async def main():
    # Single engine instance handles multiple workloads with bounded concurrency.
    engine = PipelineEngine(
        agents_dir=None,
        max_concurrent_loops=10,
        worker_pool_size=3,   # Only 3 workers run in parallel at any time
    )

    # Create a batch of workloads (here represented as simple strings;
    # in production each would be a PipelineContext or task descriptor)
    workloads = [f"workload-{i}" for i in range(8)]
    completed_count = 0

    def on_progress(result):
        nonlocal completed_count
        completed_count += 1
        print(f"  Completed {completed_count}/{len(workloads)}: {result}")

    print(f"Submitting {len(workloads)} workloads with worker_pool_size=3...")
    results = await engine.execute_with_backpressure(
        workloads=workloads,
        progress_callback=on_progress,
    )

    print(f"\nAll workloads processed: {len(results)}")
    exceptions = [r for r in results if isinstance(r, Exception)]
    print(f"Exceptions: {len(exceptions)}")

    engine.shutdown()


asyncio.run(main())
```

**Engineering talking points:**

"The dual semaphore model is `_semaphore` (outer, `max_concurrent_loops`) and
`_worker_semaphore` (inner, `worker_pool_size`). The outer semaphore prevents the engine
from accepting more work than it can track. The inner semaphore prevents the worker pool
from saturating the thread executor. Each workload must acquire both before executing."

"Results are returned via `asyncio.gather(..., return_exceptions=True)`, so a single
failing workload does not abort the batch. Failed results appear as exception objects in
the output list."

---

## Talking Points by Audience — Complete Reference

### Engineering Track

1. The two template systems (System A and System B) are intentionally separate. System A
   lives in `gaia.quality` and is consumed by `QualityScorer`. System B lives in
   `gaia.pipeline` and is consumed by `PipelineEngine`. They share vocabulary but not
   objects.

2. `PipelineContext` is a frozen dataclass. Once created, it cannot be mutated. All mutable
   state lives in `PipelineSnapshot` inside the `PipelineStateMachine`.

3. `DecisionEngine.evaluate()` applies a fixed priority order: critical defects first
   (PAUSE), then quality threshold check (COMPLETE/CONTINUE), then iteration cap (FAIL),
   then default (LOOP_BACK).

4. `AuditLogger` builds a SHA-256 hash chain. Any post-hoc modification to an event's
   `data` dict is detectable via `verify_integrity()`.

5. The `PipelineEngine` does not require Lemonade server. It is an orchestration layer.
   LLM calls happen inside individual agents, which are invoked by the `LoopManager`.

6. `worker_pool_size` controls the inner semaphore that throttles `ThreadPoolExecutor`
   access. `max_concurrent_loops` controls the outer semaphore. Both are set at engine
   construction time and are fixed for the lifetime of the engine instance.

### Product / Leadership Track

1. Pipeline execution is fully autonomous. The system selects agents, evaluates quality,
   and decides whether to proceed or loop back — without human intervention.

2. Every execution produces a tamper-proof chronicle. This is the foundation for delivery
   velocity metrics, quality trend analysis, and compliance reporting.

3. The `quality_threshold` is a first-class configuration parameter, not a hardcoded
   value. Product teams can raise or lower it per project without touching code.

4. Self-correction (LOOP_BACK) is not a failure mode — it is a deliberate design. The
   pipeline's job is to ensure output meets the threshold before marking completion.

5. The three engine templates (`generic`, `rapid`, `enterprise`) represent pre-built
   tradeoffs between speed and rigor. Product teams select the template; the system
   handles the rest.

### AMD / Hardware Track

1. Zero cloud data egress. All quality evaluation, agent orchestration, and audit logging
   runs in-process on the local machine. No data leaves the Ryzen AI device.

2. `worker_pool_size` is the primary tuning knob for NPU alignment. Set it to the number
   of NPU compute units allocated to the pipeline workload.

3. `concurrent_loops` prevents the pipeline engine from starving LLM inference bandwidth
   on Hybrid mode schedulers. Set it conservatively when sharing NPU resources with
   Lemonade server.

4. The `rapid` template (5 max iterations, 0.75 threshold) is the recommended starting
   configuration for NPU-constrained or latency-sensitive deployments.

5. GAIA's pipeline orchestration is designed to run on Ryzen AI hardware without cloud
   dependency. This is AMD's open-source commitment to accessible, private AI.

---

## Documentation File Enumeration

The following MDX files are required. Create them in the listed order. Use
`docs/sdk/infrastructure/mcp.mdx` as the structural template (front matter, `<Info>` source
block, `<Note>` import block, `<Badge>` status, numbered sections).

### File 1 — User Guide

**Path:** `docs/guides/pipeline.mdx`
**docs.json section:** Under the `"User Guides"` group, after `docs/guides/routing.mdx`

```json
{
  "group": "User Guides",
  "pages": [
    "guides/chat",
    "guides/code",
    "guides/routing",
    "guides/pipeline"
  ]
}
```

**Content scope:** What the pipeline does, how to run a pipeline from the CLI or Python,
the three engine templates, and a simple end-to-end example. No internals.

### File 2 — SDK Infrastructure Reference

**Path:** `docs/sdk/infrastructure/pipeline.mdx`
**docs.json section:** Under `"sdk/infrastructure"` group, after `docs/sdk/infrastructure/mcp.mdx`

```json
{
  "group": "Infrastructure",
  "pages": [
    "sdk/infrastructure/mcp",
    "sdk/infrastructure/api-server",
    "sdk/infrastructure/pipeline"
  ]
}
```

**Content scope:** `PipelineEngine` API, `PipelineContext` fields, `PipelineConfig` fields,
`DecisionType` enum, `AuditLogger` API, the two template systems with full comparison table,
`execute_with_backpressure` signature.

### File 3 — CLI Reference update

**Path:** `docs/reference/cli.mdx` (update existing file, add pipeline section)
**docs.json section:** No change to `docs.json` — this is an update to an existing page.

**Content scope:** Add a `## gaia pipeline` section documenting any CLI commands that expose
`PipelineEngine` (e.g., `gaia pipeline run`, `gaia pipeline status`). If no CLI commands
exist yet, add a placeholder section with a `<Badge text="coming soon" />` marker.

### File 4 — Technical Specification

**Path:** `docs/spec/pipeline-engine.mdx`
**docs.json section:** Under the `"Specifications"` group (currently `docs/spec/`)

```json
{
  "group": "Specifications",
  "pages": [
    "spec/pipeline-engine",
    "spec/mcp-server"
  ]
}
```

**Content scope:** Full technical specification. Phase state machine diagram, valid state
transitions table, `DecisionEngine` priority logic, `AuditLogger` hash chain algorithm,
`PhaseContract` input/output terms for all four phases, threading model, semaphore topology.

### Structural template reference

`docs/sdk/infrastructure/mcp.mdx` demonstrates the correct file structure:
- Front matter: `title` only
- `<Info>` block with GitHub source link
- `<Note>` block with import statement
- Horizontal rule separator
- `<Badge>` status indicator
- Numbered section headings (e.g., `## 8.1 MCP Agent Base`)
- Code blocks for all API examples

Follow this pattern exactly for `docs/sdk/infrastructure/pipeline.mdx`.

---

## Quality Self-Check

| Gap from review | Addressed in this plan |
|---|---|
| Template namespace collision (CRITICAL) | Template Systems Explainer section; all engine snippets use `"generic"`, `"rapid"`, `"enterprise"` |
| Async execution context missing (CRITICAL) | Every snippet wrapped in `async def main(): ... asyncio.run(main())` |
| AMD/hardware coverage absent (HIGH) | Dedicated AMD/Ryzen AI NPU section with 3 talking points and 2 code references |
| Failure/error mode missing (HIGH) | Act 7B with `quality_threshold=0.99`, forced LOOP_BACK, chronicle inspection |
| Documentation file enumeration missing (HIGH) | 4 files enumerated with exact paths and `docs.json` placement |
| Prerequisites section absent (MEDIUM) | Prerequisites box before Act 1 |
| Single demo ordering (MEDIUM) | Two orderings: Engineering Deep-Dive and Executive/Stakeholder |
| Three-audience talking points (HIGH) | Complete talking points table for Engineering, Product/Leadership, AMD/Hardware |
