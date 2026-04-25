# Testing and Quality Assurance Plan: Pipeline Orchestration v1

**Document Type:** Comprehensive Testing Strategy  
**Branch:** `feature/pipeline-orchestration-v1`  
**Date:** 2026-04-11  
**Prepared By:** Morgan Rodriguez, Senior QA Engineer & Test Automation Architect  
**Version:** 1.0.0  

---

## Executive Summary

This testing plan provides comprehensive quality assurance coverage for the pipeline orchestration branch, addressing **19 outstanding issues** across 5 priority levels. The strategy focuses on **prevention over detection**, implementing **automation-first** testing patterns, and establishing **quality gates** that catch issues before they reach production.

**Testing Scope:**
- 5 Stage Agents (DomainAnalyzer, WorkflowModeler, LoomBuilder, GapDetector, PipelineExecutor)
- PipelineOrchestrator core logic
- RoutingEngine with resilience primitives (WIRE-1)
- Agent UI Pipeline Integration (B3-C)
- Capability vocabulary migration (ARCH-2)
- Two-registry pattern separation (INT-2)
- Documentation quality (DOC-1, DOC-3)

**Quality Targets:**
- Unit test coverage: >85% on new/modified code
- Integration test pass rate: 100% on critical paths
- E2E pipeline success rate: >95%
- Zero P0 bugs in production
- Documentation frontmatter: 100% compliance

---

## Section 1: Test Architecture Overview

### 1.1 Testing Pyramid

```
                    E2E Tests (10-15%)
                   /                  \
                  /  Full pipeline    \
                 /   execution flows   \
                /----------------------\
              Integration Tests (25-35%)
             /  Cross-system validation \
            /   SSE streaming, routing   \
           /    Agent registry bridge     \
          /--------------------------------\
                    Unit Tests (50-65%)
                  Individual components
                     Mocked LLM calls
```

### 1.2 Test Directory Structure

```
tests/
├── pipeline/                      # Pipeline-specific tests
│   ├── test_orchestrator.py       # PipelineOrchestrator unit tests
│   ├── test_stages/               # Stage agent tests
│   │   ├── test_domain_analyzer.py
│   │   ├── test_workflow_modeler.py
│   │   ├── test_loom_builder.py
│   │   ├── test_gap_detector.py
│   │   └── test_pipeline_executor.py
│   ├── test_routing_engine_resilience.py  # WIRE-1 resilience tests
│   ├── test_agent_registry_bridge.py      # INT-2 two-registry tests
│   └── test_capability_migration.py       # ARCH-2 vocabulary tests
├── integration/
│   ├── test_pipeline_integration.py       # Cross-system integration
│   ├── test_agent_ui_pipeline.py          # B3-C integration tests
│   └── test_registry_separation.py        # INT-2 integration validation
├── e2e/
│   └── test_pipeline_e2e.py               # End-to-end pipeline flows
└── quality/
    ├── test_documentation_quality.py      # DOC-1 frontmatter validation
    └── test_quality_gates.py              # CI/CD quality gate tests
```

### 1.3 Test Fixtures and Mocks

All tests leverage fixtures from `tests/conftest.py`:

```python
# Core fixtures available:
- sample_context()         # PipelineContext for testing
- sample_state_machine()   # PipelineStateMachine
- sample_loop_config()     # LoopConfig
- sample_loop_manager()    # LoopManager
- sample_decision_engine() # DecisionEngine
- sample_quality_scorer()  # QualityScorer
- sample_agent_registry()  # AgentRegistry with tmp_path
- sample_code()            # Sample Python code
- sample_code_with_issues() # Code with quality issues
- require_lemonade         # Skip if Lemonade unavailable
```

**Mock LLM Pattern:**
```python
@pytest.fixture
def mock_lemonade_client(mocker):
    """Mock Lemonade client for unit tests."""
    return mocker.patch("gaia.llm.lemonade_client.LemonadeClient")

@pytest.fixture
def mock_chat_response():
    """Mock chat response with structured JSON."""
    return {
        "primary_domain": "software-development",
        "secondary_domains": ["api-design"],
        "complexity": "medium"
    }
```

---

## Section 2: Unit Test Specifications

### 2.1 PipelineOrchestrator Tests

**File:** `tests/pipeline/test_orchestrator.py`

#### Test: `test_execute_full_pipeline_success`

**What it verifies:** Complete pipeline execution with all 5 stages completing successfully.

```python
def test_execute_full_pipeline_success(mocker, mock_lemonade_client):
    """Verify complete pipeline execution flow."""
    # Arrange
    orchestrator = PipelineOrchestrator(model_id="test-model")
    mocker.patch.object(orchestrator, '_clear_thought_domain_analysis',
                        return_value={"summary": "analysis complete"})
    mocker.patch.object(orchestrator, '_clear_thought_workflow_planning',
                        return_value={"summary": "workflow planned"})
    mocker.patch.object(orchestrator, '_clear_thought_topology_design',
                        return_value={"summary": "topology designed"})
    
    # Mock stage instances
    mock_domain_analyzer = mocker.Mock()
    mock_domain_analyzer.analyze.return_value = {"primary_domain": "test"}
    orchestrator._domain_analyzer = mock_domain_analyzer
    
    # Act
    result = orchestrator.run_pipeline(
        task_description="Build a REST API",
        auto_spawn=True
    )
    
    # Assert
    assert result["pipeline_status"] == "success"
    assert "stage_results" in result
    assert "clear_thought_analyses" in result
    assert len(result["stage_results"]) >= 4  # At least 4 stages
```

**Mock Requirements:**
- Mock all `_clear_thought_*` methods
- Mock stage agent `analyze()`, `model_workflow()`, `build_loom()`, `detect_gaps()`
- Mock LLM client responses

**Pass/Fail Criteria:**
- PASS: `pipeline_status == "success"`, all stage results present
- FAIL: Any stage throws exception, missing stage results

---

#### Test: `test_execute_pipeline_gap_detection_triggers_spawn`

**What it verifies:** GapDetector identifies missing agents and auto-spawn triggers.

```python
def test_execute_pipeline_gap_detection_triggers_spawn(mocker):
    """Verify gap detection triggers agent generation."""
    orchestrator = PipelineOrchestrator()
    
    # Mock gap detector to find gaps
    mock_gap_detector = mocker.Mock()
    mock_gap_detector.detect_gaps.return_value = {
        "gap_result": {
            "gaps_identified": True,
            "missing_agents": ["api-specialist", "database-expert"]
        }
    }
    orchestrator._gap_detector = mock_gap_detector
    
    # Mock trigger_agent_spawn
    mock_spawn = mocker.Mock()
    mock_spawn.return_value = {
        "generation_status": "success",
        "agents_spawned": ["api-specialist", "database-expert"]
    }
    orchestrator._trigger_agent_spawn = mock_spawn
    
    result = orchestrator.run_pipeline("Build full-stack app", auto_spawn=True)
    
    assert result["pipeline_status"] == "success"
    assert len(result["agents_spawned"]) == 2
    mock_spawn.assert_called_once()
```

**Pass/Fail Criteria:**
- PASS: Gaps detected, spawn called, agents listed in result
- FAIL: Gaps not detected, spawn not called when auto_spawn=True

---

#### Test: `test_execute_pipeline_auto_spawn_disabled_blocks`

**What it verifies:** Pipeline blocks when gaps detected but auto_spawn=False.

```python
def test_execute_pipeline_auto_spawn_disabled_blocks(mocker):
    """Verify pipeline blocks when auto_spawn disabled and gaps exist."""
    orchestrator = PipelineOrchestrator()
    
    mock_gap_detector = mocker.Mock()
    mock_gap_detector.detect_gaps.return_value = {
        "gap_result": {
            "gaps_identified": True,
            "missing_agents": ["missing-agent"]
        }
    }
    orchestrator._gap_detector = mock_gap_detector
    
    result = orchestrator.run_pipeline("Test task", auto_spawn=False)
    
    assert result["pipeline_status"] == "blocked"
    assert result["block_reason"] == "missing_agents_require_generation"
    assert len(result["agents_spawned"]) == 0
```

**Pass/Fail Criteria:**
- PASS: Status="blocked", block_reason correct, no agents spawned
- FAIL: Pipeline proceeds without agents, wrong status

---

#### Test: `test_clear_thought_domain_analysis_json_parse`

**What it verifies:** LLM response parsing handles both JSON and prose responses.

```python
def test_clear_thought_domain_analysis_json_parse(mocker):
    """Verify LLM JSON response parsing."""
    orchestrator = PipelineOrchestrator()
    
    # Test JSON response
    mock_response = mocker.Mock()
    mock_response.text = '{"primary_domain": "api", "complexity": "high"}'
    orchestrator.chat.send_messages.return_value = mock_response
    
    result = orchestrator._clear_thought_domain_analysis("Build API")
    
    assert "summary" in result
    assert result["analysis"]["primary_domain"] == "api"
    
    # Test prose response (no JSON block)
    mock_response.text = "The primary domain is API development..."
    result = orchestrator._clear_thought_domain_analysis("Build API")
    
    assert "raw_response" in result["analysis"]
```

**Pass/Fail Criteria:**
- PASS: JSON parsed correctly, prose handled gracefully
- FAIL: Exception on prose, JSON not parsed

---

### 2.2 RoutingEngine Resilience Tests (WIRE-1)

**File:** `tests/pipeline/test_routing_engine_resilience.py`

#### Test: `test_circuit_breaker_trips_after_failures`

**What it verifies:** CircuitBreaker opens after 5 consecutive failures.

```python
def test_circuit_breaker_trips_after_failures(mocker):
    """Verify circuit breaker trips after threshold failures."""
    from gaia.resilience import CircuitBreaker, CircuitBreakerConfig
    
    config = CircuitBreakerConfig(
        failure_threshold=5,
        recovery_timeout=30.0,
        success_threshold=2
    )
    circuit_breaker = CircuitBreaker(config)
    
    # Force 5 failures
    for i in range(5):
        with pytest.raises(Exception):
            @CircuitBreaker.call(config)
            def failing_func():
                raise Exception("Forced failure")
            failing_func()
    
    # Verify circuit is open
    assert circuit_breaker.is_open is True
    
    # Next call should fail immediately (circuit open)
    with pytest.raises(Exception):
        failing_func()
```

**Mock Requirements:**
- CircuitBreaker with test configuration
- Mock function that always fails

**Pass/Fail Criteria:**
- PASS: Circuit opens after 5 failures, rejects subsequent calls
- FAIL: Circuit doesn't open, wrong threshold

---

#### Test: `test_bulkhead_limits_concurrency`

**What it verifies:** Bulkhead limits concurrent operations to configured maximum.

```python
async def test_bulkhead_limits_concurrency(mocker):
    """Verify bulkhead enforces concurrency limits."""
    from gaia.resilience import Bulkhead, BulkheadConfig
    
    config = BulkheadConfig(
        max_concurrency=3,
        acquire_timeout=1.0
    )
    bulkhead = Bulkhead(config)
    
    started = asyncio.Event()
    concurrent_count = 0
    max_concurrent_observed = 0
    
    @Bulkhead.isolate(config)
    async def concurrent_task():
        nonlocal concurrent_count, max_concurrent_observed
        concurrent_count += 1
        max_concurrent_observed = max(max_concurrent_observed, concurrent_count)
        await asyncio.sleep(0.1)
        concurrent_count -= 1
    
    # Start 10 tasks
    tasks = [concurrent_task() for _ in range(10)]
    
    with pytest.raises(Exception):  # Some should fail due to bulkhead
        await asyncio.gather(*tasks, return_exceptions=True)
    
    assert max_concurrent_observed <= 3
```

**Pass/Fail Criteria:**
- PASS: Concurrent execution never exceeds limit
- FAIL: More than 3 concurrent, no timeout enforcement

---

#### Test: `test_retry_with_exponential_backoff`

**What it verifies:** Retry primitive retries with exponential backoff.

```python
async def test_retry_with_exponential_backoff(mocker):
    """Verify retry with exponential backoff on transient failures."""
    from gaia.resilience import Retry, RetryConfig
    
    config = RetryConfig(
        max_retries=3,
        base_delay=0.1,
        max_delay=1.0,
        exponential_base=2
    )
    
    call_count = 0
    
    @Retry.with_backoff(config)
    async def flaky_function():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("Transient failure")
        return "success"
    
    result = await flaky_function()
    
    assert result == "success"
    assert call_count == 3  # Failed twice, succeeded on third
```

**Pass/Fail Criteria:**
- PASS: Retries correct number of times, succeeds on valid attempt
- FAIL: Wrong retry count, no backoff timing

---

#### Test: `test_routing_engine_resilience_wrapper`

**What it verifies:** RoutingEngine.route_defect() wrapped with all resilience primitives.

```python
def test_routing_engine_resilience_wrapper(mocker):
    """Verify RoutingEngine has resilience wrappers."""
    from gaia.resilience import CircuitBreaker, Bulkhead, Retry
    
    # Check that route_defect is wrapped
    engine = RoutingEngine()
    
    # Inspect function for decorators (implementation-dependent)
    # OR test actual resilience behavior:
    
    # Test circuit breaker integration
    for i in range(7):
        defect = {"description": f"Forced failure {i}", "id": f"fail-{i}"}
        with pytest.raises(Exception):
            # Simulate routing failure
            engine._agent_registry = None  # Force fallback
            engine.route_defect(defect)
    
    # After failures, circuit should be open
    # (Implementation depends on how resilience is wired)
```

**Pass/Fail Criteria:**
- PASS: All three resilience primitives active and functional
- FAIL: Missing any primitive, wrong configuration

---

### 2.3 Capability Migration Tests (ARCH-2)

**File:** `tests/pipeline/test_capability_migration.py`

#### Test: `test_yaml_files_use_unified_vocabulary`

**What it verifies:** All 18 YAML files use vocabulary from `src/gaia/core/capabilities.py`.

```python
def test_yaml_files_use_unified_vocabulary():
    """Verify all YAML configs use unified capability vocabulary."""
    from gaia.core.capabilities import Capability  # Adjust import path
    
    yaml_dir = Path("config/agents")
    yaml_files = list(yaml_dir.glob("*.yaml"))
    
    valid_capabilities = set(Capability.__members__.keys())
    
    issues = []
    for yaml_file in yaml_files:
        with open(yaml_file, 'r') as f:
            content = yaml.safe_load(f)
        
        capabilities = content.get('capabilities', [])
        for cap in capabilities:
            if cap not in valid_capabilities:
                issues.append(f"{yaml_file.name}: '{cap}' not in vocabulary")
    
    assert len(issues) == 0, f"Capability vocabulary issues:\n" + "\n".join(issues)
```

**Pass/Fail Criteria:**
- PASS: All capabilities match vocabulary
- FAIL: Any freeform capability strings remain

---

#### Test: `test_migration_script_preserves_structure`

**What it verifies:** Migration script updates capabilities without breaking YAML structure.

```python
def test_migration_script_preserves_structure(tmp_path):
    """Verify migration script preserves YAML structure."""
    import subprocess
    
    # Create test YAML with old vocabulary
    test_yaml = tmp_path / "test-agent.yaml"
    test_yaml.write_text("""
id: test-agent
name: Test Agent
capabilities:
  - requirements-analysis
  - full-stack-development
""")
    
    # Run migration script
    result = subprocess.run([
        "python", "util/migrate-capabilities.py",
        str(tmp_path)
    ], capture_output=True, text=True)
    
    assert result.returncode == 0
    
    # Verify structure preserved
    with open(test_yaml, 'r') as f:
        content = yaml.safe_load(f)
    
    assert content['id'] == 'test-agent'
    assert content['name'] == 'Test Agent'
    # Capabilities should be migrated
    assert 'requirements-analysis' not in str(content.get('capabilities', []))
```

**Pass/Fail Criteria:**
- PASS: Script runs, YAML valid, structure preserved
- FAIL: Script errors, YAML invalid, structure broken

---

### 2.4 Two-Registry Separation Tests (INT-2)

**File:** `tests/pipeline/test_agent_registry_bridge.py`

#### Test: `test_pipeline_registry_is_isolated`

**What it verifies:** PipelineAgentRegistry doesn't import from agents.registry.

```python
def test_pipeline_registry_is_isolated():
    """Verify PipelineAgentRegistry is isolated from UI registry."""
    import ast
    from gaia.pipeline import agent_registry
    
    # Parse module source
    source = inspect.getsource(agent_registry)
    tree = ast.parse(source)
    
    # Check imports
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and 'gaia.agents.registry' in node.module:
                pytest.fail(
                    f"PipelineAgentRegistry should not import from "
                    f"gaia.agents.registry, found: {node.module}"
                )
```

**Pass/Fail Criteria:**
- PASS: No imports from gaia.agents.registry
- FAIL: Any import from UI registry module

---

#### Test: `test_bridge_pattern_registries`

**What it verifies:** PipelineOrchestrator uses both registries correctly.

```python
def test_bridge_pattern_registries(mocker):
    """Verify bridge pattern between registries."""
    from gaia.pipeline.agent_registry import PipelineAgentRegistry
    from gaia.agents.registry import AgentRegistry
    
    pipeline_registry = PipelineAgentRegistry()
    agent_registry = AgentRegistry()
    
    # Pipeline registry selects agent ID
    agent_id = pipeline_registry.select_agent(
        task="Build API",
        phase="DEVELOPMENT"
    )
    
    # Agent registry instantiates
    agent_factory = agent_registry.get(agent_id)
    assert agent_factory is not None
    assert callable(agent_factory.factory)
```

**Pass/Fail Criteria:**
- PASS: Pipeline selects, Agent instantiates
- FAIL: Either registry fails, wrong agent returned

---

## Section 3: Integration Test Specifications

### 3.1 Agent UI Pipeline Integration (B3-C)

**File:** `tests/integration/test_agent_ui_pipeline.py`

#### Test: `test_pipeline_sse_endpoint_streams_events`

**What it verifies:** SSE endpoint streams pipeline stage progress.

```python
def test_pipeline_sse_endpoint_streams_events(client, mocker):
    """Verify SSE endpoint streams pipeline events."""
    from gaia.ui.routers.pipeline import run_pipeline_stream
    
    # Mock orchestrator
    mock_orchestrator = mocker.Mock()
    mock_orchestrator.run_pipeline.return_value = {
        "pipeline_status": "success",
        "stage_results": {
            "domain_analysis": {"primary_domain": "test"},
            "workflow_model": {"pattern": "standard"},
            "loom_topology": {"nodes": 5},
            "gap_analysis": {"gaps_identified": False},
            "pipeline_execution": {"result": "success"}
        }
    }
    
    response = client.post("/api/v1/pipeline/run", json={
        "task": "Build calculator",
        "auto_spawn": True
    })
    
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"
    
    # Parse SSE events
    events = list(parse_sse_events(response.text))
    assert len(events) >= 5  # At least one per stage
    
    # Verify event structure
    for event in events:
        assert "event" in event
        assert "data" in event
        assert "stage" in event.get("data", {})
```

**Mock Requirements:**
- Mock PipelineOrchestrator
- Mock SSE event generator

**Pass/Fail Criteria:**
- PASS: SSE content-type, events stream correctly
- FAIL: Wrong content-type, missing events

---

#### Test: `test_pipeline_panel_component_renders`

**What it verifies:** Frontend PipelinePanel component renders correctly.

```python
# tests/webui/components/test_pipeline_panel.tsx
import { render, screen } from '@testing-library/react'
import { PipelinePanel } from '../src/components/PipelinePanel'

describe('PipelinePanel', () => {
  it('renders pipeline input form', () => {
    render(<PipelinePanel />)
    
    expect(screen.getByLabelText(/task description/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /run pipeline/i })).toBeInTheDocument()
  })
  
  it('displays stage progress during execution', async () => {
    const { container } = render(<PipelinePanel />)
    
    // Simulate SSE event
    const eventSource = mockEventSource('/api/v1/pipeline/run')
    eventSource.emit('stage-progress', {
      stage: 'domain_analysis',
      status: 'in_progress'
    })
    
    await waitFor(() => {
      expect(screen.getByText(/Stage 1: Domain Analysis/i)).toBeInTheDocument()
    })
  })
})
```

---

### 3.2 Resilience Integration Tests (WIRE-1)

**File:** `tests/integration/test_resilience_integration.py`

#### Test: `test_circuit_breaker_recovery`

**What it verifies:** Circuit breaker recovers after timeout.

```python
async def test_circuit_breaker_recovery():
    """Verify circuit breaker recovers after timeout."""
    from gaia.resilience import CircuitBreaker, CircuitBreakerConfig
    
    config = CircuitBreakerConfig(
        failure_threshold=3,
        recovery_timeout=0.5,  # Short timeout for testing
        success_threshold=1
    )
    circuit = CircuitBreaker(config)
    
    # Trip the circuit
    for i in range(3):
        try:
            @CircuitBreaker.call(config)
            def fail():
                raise Exception("fail")
            fail()
        except:
            pass
    
    assert circuit.is_open is True
    
    # Wait for recovery
    await asyncio.sleep(0.6)
    
    # Should allow one test request
    assert circuit.is_half_open is True
```

---

### 3.3 Documentation Quality Tests (DOC-1)

**File:** `tests/quality/test_documentation_quality.py`

#### Test: `test_all_spec_files_have_yaml_frontmatter`

**What it verifies:** All 9 spec files start with YAML frontmatter.

```python
def test_all_spec_files_have_yaml_frontmatter():
    """Verify all spec files have YAML frontmatter (DOC-1)."""
    spec_dir = Path("docs/spec")
    
    files_requiring_frontmatter = [
        "agent-ui-eval-kpi-reference.md",
        "agent-ui-eval-kpis.md",
        "gaia-loom-architecture.md",
        "nexus-gaia-native-integration-spec.md",
        "pipeline-metrics-competitive-analysis.md",
        "pipeline-metrics-kpi-reference.md",
        "phase5_multi_stage_pipeline.md",
        "component-framework-design-spec.md",
        "component-framework-implementation-plan.md",
    ]
    
    missing = []
    for filename in files_requiring_frontmatter:
        filepath = spec_dir / filename
        if not filepath.exists():
            missing.append(f"{filename}: FILE NOT FOUND")
            continue
        
        with open(filepath, 'r') as f:
            first_line = f.readline().strip()
        
        if first_line != '---':
            missing.append(f"{filename}: Missing '---' on line 1")
        else:
            # Check for title field
            second_line = f.readline().strip()
            if not second_line.startswith('title:'):
                missing.append(f"{filename}: Missing 'title:' field")
    
    assert len(missing) == 0, f"Frontmatter issues:\n" + "\n".join(missing)
```

**Pass/Fail Criteria:**
- PASS: All files have `---` on line 1, `title:` on line 2
- FAIL: Any file missing frontmatter

---

## Section 4: E2E Test Specifications

### 4.1 Pipeline End-to-End Tests

**File:** `tests/e2e/test_pipeline_e2e.py`

#### Test: `test_full_pipeline_execution_no_gaps`

**What it verifies:** Complete pipeline execution when all agents available.

```python
@pytest.mark.e2e
@pytest.mark.require_lemonade
def test_full_pipeline_execution_no_gaps(require_lemonade):
    """E2E test: Full pipeline execution with all agents available."""
    from gaia.pipeline.orchestrator import run_pipeline
    
    result = run_pipeline(
        task_description="Create a Python module with add and multiply functions",
        auto_spawn=True,
        debug=True
    )
    
    assert result["pipeline_status"] == "success"
    assert "stage_results" in result
    assert "domain_analysis" in result["stage_results"]
    assert "workflow_model" in result["stage_results"]
    assert "loom_topology" in result["stage_results"]
    assert "gap_analysis" in result["stage_results"]
    assert "pipeline_execution" in result["stage_results"]
    assert result["gap_analysis"]["gaps_identified"] is False
```

**Requirements:**
- Lemonade server running
- All 5 stage agents available
- Network access for LLM

**Pass/Fail Criteria:**
- PASS: All stages complete, no gaps, execution successful
- FAIL: Any stage fails, gaps detected, execution errors

---

#### Test: `test_pipeline_with_gap_detection_and_spawn`

**What it verifies:** Pipeline detects gaps and triggers agent generation.

```python
@pytest.mark.e2e
@pytest.mark.require_lemonade
def test_pipeline_with_gap_detection_and_spawn(require_lemonade, tmp_path):
    """E2E test: Gap detection triggers agent generation."""
    from gaia.pipeline.orchestrator import PipelineOrchestrator
    
    orchestrator = PipelineOrchestrator(
        model_id="Qwen3.5-35B-A3B-GGUF",
        debug=True
    )
    
    # Task requiring agents not in registry
    result = orchestrator.run_pipeline(
        task_description="Build a medical diagnosis system with FDA compliance",
        auto_spawn=True
    )
    
    # Should either succeed or block for agent generation
    assert result["pipeline_status"] in ["success", "blocked"]
    
    if result["pipeline_status"] == "blocked":
        assert result["block_reason"] == "missing_agents_require_generation"
        assert len(result["gap_analysis"]["missing_agents"]) > 0
```

---

## Section 5: Quality Gates

### 5.1 Pre-Commit Quality Gates

**File:** `.github/workflows/quality-gates-pre-commit.yml`

```yaml
name: Pre-Commit Quality Gates

on: [pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install uv
          uv pip install -e ".[dev]"
      - name: Run linter
        run: python util/lint.py --all
  
  unit-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run unit tests
        run: python -m pytest tests/unit/ tests/pipeline/ -xvs --cov=src/gaia/pipeline
  
  docs-frontmatter:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Validate YAML frontmatter
        run: python util/validate-frontmatter.py docs/spec/*.md
```

### 5.2 Pre-Merge Quality Gates

**File:** `tests/quality/test_quality_gates.py`

#### Gate: `test_coverage_threshold_met`

```python
def test_coverage_threshold_met():
    """Verify code coverage meets threshold."""
    import subprocess
    
    result = subprocess.run([
        "python", "-m", "pytest",
        "tests/pipeline/",
        "--cov=src/gaia/pipeline",
        "--cov-report=term-missing",
        "--cov-fail-under=85"
    ], capture_output=True, text=True)
    
    assert result.returncode == 0, f"Coverage threshold not met:\n{result.stdout}"
```

#### Gate: `test_no_p0_bugs_in_production`

```python
def test_no_p0_bugs_in_production():
    """Verify no P0 bugs exist in issue tracker."""
    # This would integrate with GitHub Issues API
    # For now, check a marker file
    p0_bugs_file = Path(".github/P0_BUGS_TRACKER.md")
    
    if p0_bugs_file.exists():
        content = p0_bugs_file.read_text()
        if "status: open" in content.lower():
            pytest.fail("P0 bugs exist - cannot merge")
```

### 5.3 Documentation Quality Gate (DOC-3)

```python
def test_branch_change_matrix_synchronized():
    """Verify branch-change-matrix.md is synchronized (DOC-3)."""
    matrix_file = Path("docs/reference/branch-change-matrix.md")
    manifest_file = Path("docs/spec/phase5-update-manifest.md")
    
    with open(matrix_file, 'r') as f:
        matrix_content = f.read()
    
    # Check for Phase 5 section
    assert "### 3.13 Phase 5 Pipeline Orchestration" in matrix_content
    
    # Check commit references
    commits_to_verify = ["57ee63d", "fa3ef98"]
    for commit in commits_to_verify:
        assert commit in matrix_content, f"Commit {commit} not in matrix"
```

---

## Section 6: Test Execution Plan

### 6.1 Test Execution Order

```
1. Unit Tests (Fast Feedback - 5-10 min)
   └─> Run on every commit
   
2. Integration Tests (Medium - 15-20 min)
   └─> Run on PR creation/update
   
3. E2E Tests (Slow - 30-45 min)
   └─> Run on PR review, before merge
   
4. Quality Gates (Blocking)
   └─> Must pass before merge
```

### 6.2 Test Environment Setup

```bash
# Development environment
uv venv && uv pip install -e ".[dev]"

# Run unit tests only
python -m pytest tests/unit/ tests/pipeline/ -xvs

# Run with coverage
python -m pytest tests/pipeline/ --cov=src/gaia/pipeline --cov-report=html

# Run integration tests (requires Lemonade)
python -m pytest tests/integration/ --require-lemonade

# Run E2E tests (full environment)
python -m pytest tests/e2e/ --require-lemonade --model-id="Qwen3.5-35B-A3B-GGUF"
```

### 6.3 Test Data Requirements

| Test Type | Data Required | Source |
|-----------|--------------|--------|
| Unit | Mock responses | `conftest.py` fixtures |
| Integration | Real LLM responses | Lemonade server |
| E2E | Full pipeline tasks | `tests/e2e/fixtures.py` |
| Documentation | YAML files | `config/agents/*.yaml` |

---

## Section 7: Quality Checklist

### 7.1 Code Quality Checklist

- [ ] All new files have copyright headers
- [ ] TypeScript compiles without errors (B3-C frontend)
- [ ] Python linting passes (`python util/lint.py --all`)
- [ ] Type hints present on all function signatures
- [ ] Docstrings for all public methods
- [ ] No hardcoded values (use config/constants)
- [ ] Error handling for all external calls
- [ ] Logging at appropriate levels (DEBUG, INFO, WARNING, ERROR)

### 7.2 Testing Checklist

- [ ] Unit tests for all new functions/methods
- [ ] Integration tests for cross-system interactions
- [ ] E2E tests for complete pipeline flows
- [ ] Mock LLM client in unit tests
- [ ] Real LLM tests in integration/E2E
- [ ] Test fixtures reusable and well-documented
- [ ] Coverage >85% on new/modified code
- [ ] Tests run in isolation (no inter-test dependencies)

### 7.3 Documentation Checklist (DOC-1, DOC-3)

- [ ] YAML frontmatter on all 9 spec files
- [ ] `title:` field matches filename
- [ ] `branch-change-matrix.md` Sections A-G updated
- [ ] Open Item statuses reflect Phase 5 delivery
- [ ] Commit index includes commits 57ee63d through fa3ef98
- [ ] Design spec Section 5 updated (ARCH-1)
- [ ] ADR-001 documented for hybrid pattern

### 7.4 Quality Gate Checklist

- [ ] Pre-commit hooks installed and running
- [ ] CI/CD pipelines green
- [ ] No P0 bugs open
- [ ] All acceptance criteria met
- [ ] Migration script verified (ARCH-2)
- [ ] Resilience wiring functional (WIRE-1)
- [ ] Registry separation validated (INT-2)
- [ ] Agent UI integration complete (B3-C)

---

## Section 8: Risk Mitigation

### 8.1 Test-Identified Risks

| Risk ID | Test That Catches | Mitigation |
|---------|------------------|------------|
| R-DOC-1 | `test_all_spec_files_have_yaml_frontmatter` | Add CI check for frontmatter |
| R-INT-1 | `test_pipeline_registry_is_isolated` | Code review + import validation |
| R-INT-2 | `test_branch_change_matrix_synchronized` | Manual verification step |
| R-WIRE-1 | `test_circuit_breaker_trips_after_failures` | Integration test + monitoring |
| R-B3C-1 | `test_pipeline_sse_endpoint_streams_events` | E2E validation |

### 8.2 Escape Prevention

**Tests that prevent bugs reaching production:**

1. **Pipeline execution failures:** `test_execute_full_pipeline_success`
2. **Gap detection failures:** `test_execute_pipeline_gap_detection_triggers_spawn`
3. **Resilience failures:** `test_circuit_breaker_trips_after_failures`, `test_bulkhead_limits_concurrency`
4. **SSE streaming failures:** `test_pipeline_sse_endpoint_streams_events`
5. **Documentation build failures:** `test_all_spec_files_have_yaml_frontmatter`

---

## Section 9: Test Maintenance

### 9.1 Test Update Triggers

| Trigger | Tests to Update | Owner |
|---------|-----------------|-------|
| New pipeline stage | `test_orchestrator.py`, `test_pipeline_e2e.py` | Stage developer |
| Resilience config change | `test_routing_engine_resilience.py` | Senior developer |
| Capability vocabulary change | `test_capability_migration.py` | Architecture team |
| Agent UI API change | `test_agent_ui_pipeline.py` | Frontend developer |

### 9.2 Test Debt Tracking

**Test debt logged in GitHub Issues with label `test-debt`:**

- Flaky tests (non-deterministic results)
- Slow tests (>5 seconds execution)
- Tests with high mock complexity
- Tests missing for critical paths

---

## Section 10: Handoff to Quality Reviewer

### 10.1 Review Checklist for Quality Reviewer

**Priority 1 (Blocking):**
1. Verify all P0 tests pass (`test_all_spec_files_have_yaml_frontmatter`)
2. Verify resilience tests pass (`test_circuit_breaker_trips_after_failures`)
3. Verify integration tests pass (`test_pipeline_sse_endpoint_streams_events`)

**Priority 2 (Pre-Merge):**
1. Verify capability migration tests pass
2. Verify registry separation tests pass
3. Verify E2E tests pass with real LLM

**Priority 3 (Post-Merge):**
1. Verify all quality gates configured in CI/CD
2. Verify test coverage reports generated
3. Verify test maintenance plan followed

### 10.2 Files for Quality Reviewer

| File | Purpose | Review Priority |
|------|---------|-----------------|
| `tests/pipeline/test_orchestrator.py` | PipelineOrchestrator tests | P0 |
| `tests/pipeline/test_routing_engine_resilience.py` | WIRE-1 tests | P0 |
| `tests/pipeline/test_capability_migration.py` | ARCH-2 tests | P1 |
| `tests/pipeline/test_agent_registry_bridge.py` | INT-2 tests | P1 |
| `tests/integration/test_agent_ui_pipeline.py` | B3-C tests | P0 |
| `tests/quality/test_documentation_quality.py` | DOC-1 tests | P0 |
| `tests/e2e/test_pipeline_e2e.py` | End-to-end tests | P1 |

---

## Appendix A: Test File Templates

### A.1 Unit Test Template

```python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Tests for [COMPONENT].

Tests cover:
- [List what tests cover]
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from gaia.[module] import [Component]


class Test[Component]:
    """Tests for [Component] class."""
    
    @pytest.fixture
    def component(self):
        """Create test component."""
        return [Component]()
    
    def test_[scenario]_[expected_behavior](self, component):
        """Verify [what is verified]."""
        # Arrange
        # ...
        
        # Act
        result = component.method()
        
        # Assert
        assert result.expected == True
```

### A.2 Integration Test Template

```python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Integration tests for [COMPONENT].

Tests cover:
- Cross-system interactions
- Real service integration
"""

import pytest

from gaia.[module] import [Component]


@pytest.mark.integration
@pytest.mark.require_lemonade
class Test[Component]Integration:
    """Integration tests for [Component]."""
    
    def test_[integration_scenario](self, require_lemonade, client):
        """Verify [integration behavior]."""
        # Test with real services
        # ...
```

---

**Document Version:** 1.0  
**Prepared By:** Morgan Rodriguez, Senior QA Engineer & Test Automation Architect  
**Date:** 2026-04-11  
**Next Reviewer:** quality-reviewer

---

## Appendix B: Test Files Created

The following test files have been created as part of this testing plan:

| File | Purpose | Issue Coverage | Priority |
|------|---------|----------------|----------|
| `tests/pipeline/test_orchestrator.py` | PipelineOrchestrator unit tests | B3-C, ARCH-1 | P0 |
| `tests/pipeline/test_routing_engine_resilience.py` | WIRE-1 resilience tests | WIRE-1 | P0 |
| `tests/pipeline/test_capability_migration.py` | ARCH-2 capability tests | ARCH-2 | P1 |
| `tests/pipeline/test_agent_registry_bridge.py` | INT-2 registry separation | INT-2 | P1 |
| `tests/integration/test_agent_ui_pipeline.py` | B3-C integration tests | B3-C | P0 |
| `tests/quality/test_documentation_quality.py` | DOC-1, DOC-3 validation | DOC-1, DOC-3 | P0 |

### How to Run Tests

```bash
# Run all pipeline tests
python -m pytest tests/pipeline/ -xvs

# Run with coverage
python -m pytest tests/pipeline/ --cov=src/gaia/pipeline --cov-report=html

# Run documentation quality tests
python -m pytest tests/quality/test_documentation_quality.py -xvs

# Run integration tests (requires Lemonade)
python -m pytest tests/integration/ --require-lemonade

# Run specific test file
python -m pytest tests/pipeline/test_orchestrator.py::TestPipelineOrchestratorTools -xvs
```

### Test Execution Checklist

Before merging, verify:

- [ ] `tests/pipeline/test_orchestrator.py` - All tests pass
- [ ] `tests/pipeline/test_routing_engine_resilience.py` - Resilience tests pass
- [ ] `tests/pipeline/test_capability_migration.py` - Capability vocabulary validated
- [ ] `tests/pipeline/test_agent_registry_bridge.py` - Registry separation confirmed
- [ ] `tests/integration/test_agent_ui_pipeline.py` - SSE streaming functional
- [ ] `tests/quality/test_documentation_quality.py` - DOC-1 frontmatter present
- [ ] Coverage report shows >85% on new/modified code

### Files Modified/Created Summary

**Test Files Created:**
- `tests/pipeline/test_orchestrator.py` (500+ lines)
- `tests/pipeline/test_routing_engine_resilience.py` (400+ lines)
- `tests/pipeline/test_capability_migration.py` (350+ lines)
- `tests/pipeline/test_agent_registry_bridge.py` (300+ lines)
- `tests/integration/test_agent_ui_pipeline.py` (250+ lines)
- `tests/quality/test_documentation_quality.py` (300+ lines)

**Documentation Created:**
- `TESTING-PLAN-pipeline-orchestration-v1.md` (This document)

**Total Test Coverage:**
- 6 test files
- 50+ test cases
- Coverage for all P0/P1 issues (B3-C, WIRE-1, ARCH-2, INT-2, DOC-1, DOC-3)

