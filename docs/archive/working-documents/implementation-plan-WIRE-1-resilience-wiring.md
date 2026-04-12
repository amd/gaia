# Implementation Plan: WIRE-1 - Resilience Primitives Wiring

**Priority:** P1 (Post-Merge Integration) | **Effort:** 2-3 hours | **Owner:** senior-developer

---

## Problem Statement

`CircuitBreaker`, `Bulkhead`, and `Retry` exist in `src/gaia/resilience/` with full test coverage but are not imported or invoked in `engine.py`, `loop_manager.py`, or `routing_engine.py`. The resilience primitives need to be wired around agent invocation call sites to prevent cascading failures.

---

## Current State Analysis

### Resilience Module Status (COMPLETE)

**Location:** `src/gaia/resilience/`

1. **CircuitBreaker** (`circuit_breaker.py`):
   - States: CLOSED (normal), OPEN (failing fast), HALF_OPEN (testing recovery)
   - Configurable: `failure_threshold=5`, `recovery_timeout=30.0`, `success_threshold=2`
   - Thread-safe with RLock
   - Methods: `call()`, `acall()`, decorator support via `__call__()`

2. **Bulkhead** (`bulkhead.py`):
   - Limits concurrent operations using semaphores
   - Configurable: `max_concurrency=10`, `acquire_timeout=30.0`
   - Methods: `execute()`, `aexecute()`, decorator support
   - Tracks: `active_count`, `available_permits`, `utilization`

3. **Retry** (`retry.py`):
   - Exponential backoff with jitter
   - Configurable: `max_retries=3`, `base_delay=1.0`, `max_delay=60.0`, `jitter_factor=0.1`
   - Methods: `retry()` decorator, `RetryExecutor` class
   - Callback support via `on_retry` parameter

4. **Module Exports** (`__init__.py`):
```python
from gaia.resilience.circuit_breaker import (
    CircuitBreaker, CircuitBreakerState, CircuitBreakerConfig, CircuitOpenError,
)
from gaia.resilience.bulkhead import Bulkhead, BulkheadConfig, BulkheadFullError
from gaia.resilience.retry import retry, RetryConfig, RetryError
```

### Routing Engine Status (TARGET FOR WIRING)

**Location:** `src/gaia/pipeline/routing_engine.py`

**Key Call Sites Identified:**

1. **Line 432-433:** `select_specialist()` - Agent selection
   - Calls: `self._agent_registry.get_agent(specialist_id)`
   - Risk: Registry lookup could fail or timeout

2. **Line 445-459:** `route_defect()` - Main routing method
   - Calls: `RoutingDecision.create()` with multiple operations
   - Risk: Exception during decision creation

3. **Line 623, 645:** `select_specialist()` - Registry lookups
   - Two separate registry access points
   - Need protection against registry failures

---

## Implementation Tasks

### Task 1: Add Resilience Imports to Routing Engine (15 min)

**File:** `src/gaia/pipeline/routing_engine.py`

**Change:** Add imports after line 11 (after existing imports):

```python
# Add after line 11 (after "from typing import Any, Dict, List, Optional, Tuple")
from gaia.resilience import CircuitBreaker, Bulkhead, retry
from gaia.resilience.circuit_breaker import CircuitOpenError
from gaia.resilience.bulkhead import BulkheadFullError
```

---

### Task 2: Add Resilience Instances to RoutingEngine Class (30 min)

**File:** `src/gaia/pipeline/routing_engine.py`

**Change:** Add class-level resilience instances after line 229 (after DEFAULT_RULES definition):

```python
# Add after line 342 (after DEFAULT_RULES list, before FALLBACK_PHASES)

# Resilience primitives for agent routing operations
# These are class-level to be shared across all RoutingEngine instances
_routing_circuit_breaker = CircuitBreaker(
    CircuitBreakerConfig(
        failure_threshold=5,      # Open after 5 consecutive failures
        recovery_timeout=30.0,    # Wait 30 seconds before testing recovery
        success_threshold=2,      # Need 2 successes to close circuit
    )
)

_routing_bulkhead = Bulkhead(
    BulkheadConfig(
        max_concurrency=10,       # Max 10 concurrent routing operations
        acquire_timeout=5.0,      # Wait max 5 seconds for a permit
    )
)

# Retry configuration for transient failures
_routing_retry_config = RetryConfig(
    max_retries=3,                # Retry up to 3 times
    base_delay=0.5,               # Start with 500ms delay
    max_delay=5.0,                # Cap at 5 seconds
    jitter=True,                  # Add jitter to prevent thundering herd
    jitter_factor=0.2,            # 20% jitter range
)
```

---

### Task 3: Wrap route_defect Method with Resilience Primitives (45 min)

**File:** `src/gaia/pipeline/routing_engine.py`

**Change:** Modify the `route_defect` method signature and add wrapper (lines 388-471):

**Option A: Decorator Approach (Recommended)**

Add wrapper method after line 387 (before `def route_defect`):

```python
@_routing_circuit_breaker
@_routing_bulkhead
@retry(_routing_retry_config)
def route_defect(
    self,
    defect: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> RoutingDecision:
    """
    Route a single defect to appropriate agent and phase.

    This is the main routing method. It:
    1. Detects defect type from description
    2. Evaluates routing rules in priority order
    3. Selects specialist agent
    4. Creates routing decision

    Protected by resilience primitives:
    - CircuitBreaker: Fails fast after 5 consecutive failures
    - Bulkhead: Limits to 10 concurrent routing operations
    - Retry: Retries up to 3 times with exponential backoff

    Args:
        defect: Defect dictionary with at least 'description' field
        context: Optional context (current_phase, severity, etc.)

    Returns:
        RoutingDecision with routing instructions

    Raises:
        CircuitOpenError: If circuit breaker is open (too many failures)
        BulkheadFullError: If at maximum concurrent capacity
        RetryError: If all retry attempts exhausted

    Example:
        >>> defect = {
        ...     "id": "d-001",
        ...     "description": "SQL injection in login",
        ...     "severity": "critical"
        ... }
        >>> decision = engine.route_defect(defect)
        >>> print(decision.target_agent)
        'security-auditor'
    """
    # ... rest of existing implementation unchanged ...
```

**Option B: Inline Wrapper Approach (More Control)**

If decorator approach causes issues with `self` parameter, use inline wrapping:

```python
def route_defect(
    self,
    defect: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> RoutingDecision:
    """
    Route a single defect to appropriate agent and phase.
    Wrapped with resilience primitives for fault tolerance.
    """
    
    def _do_route():
        """Internal routing logic wrapped by resilience primitives."""
        description = defect.get("description", "")
        defect_id = defect.get("id", "unknown")

        # Step 1: Detect defect type
        defect_type = self.detect_defect_type(description)
        logger.debug(
            f"Detected defect type: {defect_type.name} for {defect_id}",
            extra={"defect_id": defect_id, "defect_type": defect_type.name},
        )

        # Step 2: Evaluate routing rules
        matched_rule, rule_phase = self.evaluate_rules(defect_type, context)

        # Step 3: Select specialist agent (wrapped separately)
        target_agent = self._select_specialist_resilient(defect_type, matched_rule)

        # Step 4: Determine if loop back is needed
        loop_back = matched_rule.loop_back if matched_rule else True

        # Step 5: Create routing decision
        guidance = (
            matched_rule.guidance
            if matched_rule
            else self._generate_guidance(defect_type)
        )

        decision = RoutingDecision.create(
            target_agent=target_agent,
            target_phase=rule_phase or "DEVELOPMENT",
            defect_type=defect_type,
            loop_back=loop_back,
            guidance=guidance,
            matched_rule=matched_rule.rule_id if matched_rule else "default",
            confidence=self._calculate_confidence(defect_type, description),
            alternatives=get_defect_specialists(defect_type)[1:],  # Exclude primary
            metadata={
                "defect_id": defect_id,
                "description_preview": description[:100] if description else "",
                "rules_evaluated": len(self._rules),
            },
        )

        logger.info(
            f"Routed defect {defect_id} to {target_agent} in {decision.target_phase}",
            extra={
                "defect_id": defect_id,
                "target_agent": target_agent,
                "target_phase": decision.target_phase,
                "defect_type": defect_type.name,
            },
        )

        return decision
    
    # Execute with resilience
    try:
        return RoutingEngine._routing_bulkhead.execute(
            RoutingEngine._routing_retry.execute,
            _do_route,
        )
    except CircuitOpenError as e:
        logger.error(
            f"Circuit breaker open for routing - failing fast",
            extra={"time_until_retry": e.time_until_retry}
        )
        # Return safe fallback decision
        return RoutingDecision.create(
            target_agent="senior-developer",
            target_phase="DEVELOPMENT",
            defect_type=DefectType.UNKNOWN,
            guidance="Circuit breaker open - routing to fallback agent",
            matched_rule="circuit_breaker_fallback",
        )
    except BulkheadFullError as e:
        logger.error(
            f"Bulkhead at capacity - routing rejected",
            extra={"max_concurrency": e.max_concurrency}
        )
        raise  # Re-raise to let caller handle
```

---

### Task 4: Wrap select_specialist Method (30 min)

**File:** `src/gaia/pipeline/routing_engine.py`

**Change:** Add resilient wrapper for agent registry lookups (modify lines 598-664):

```python
def _select_specialist_resilient(
    self,
    defect_type: DefectType,
    matched_rule: Optional[RoutingRule] = None,
) -> str:
    """
    Select specialist agent with resilience protection.
    
    Wraps the base select_specialist logic with circuit breaker
    to protect against registry failures.
    
    Args:
        defect_type: Type of defect
        matched_rule: Matching routing rule (if any)
    
    Returns:
        Agent ID of selected specialist
    """
    
    def _do_select():
        """Internal selection logic."""
        # Check if rule specifies agent
        if matched_rule and matched_rule.target_agent:
            # Verify agent exists if registry available
            if self._agent_registry:
                agent = self._agent_registry.get_agent(matched_rule.target_agent)
                if agent:
                    return matched_rule.target_agent
                logger.warning(
                    f"Rule-specified agent {matched_rule.target_agent} not found, finding alternative"
                )
            else:
                return matched_rule.target_agent

        # Get specialists from mapping
        specialists = get_defect_specialists(defect_type)

        if not specialists:
            logger.warning(
                f"No specialists defined for {defect_type.name}, using default",
                extra={"defect_type": defect_type.name},
            )
            return "senior-developer"

        # Try each specialist in order of preference
        for specialist_id in specialists:
            if self._agent_registry:
                agent = self._agent_registry.get_agent(specialist_id)
                if agent:
                    logger.debug(
                        f"Selected specialist {specialist_id} for {defect_type.name}",
                        extra={
                            "specialist_id": specialist_id,
                            "defect_type": defect_type.name,
                        },
                    )
                    return specialist_id
            else:
                # No registry - return first specialist
                return specialist_id

        # Fall back to senior-developer
        logger.info(
            f"No available specialist for {defect_type.name}, using senior-developer",
            extra={"defect_type": defect_type.name},
        )
        return "senior-developer"
    
    # Execute with circuit breaker protection
    try:
        return RoutingEngine._routing_circuit_breaker.call(_do_select)
    except CircuitOpenError:
        logger.warning(
            f"Circuit breaker open during specialist selection - using fallback"
        )
        return "senior-developer"  # Safe fallback
```

**Update line 433** to call the resilient wrapper:
```python
# Old (line 433):
target_agent = self.select_specialist(defect_type, matched_rule)

# New:
target_agent = self._select_specialist_resilient(defect_type, matched_rule)
```

---

### Task 5: Add Resilience Monitoring Metrics (30 min)

**File:** `src/gaia/pipeline/routing_engine.py`

**Change:** Add method to expose resilience metrics (add after line 789):

```python
def get_resilience_stats(self) -> Dict[str, Any]:
    """
    Get resilience primitives statistics.
    
    Returns monitoring data for circuit breaker, bulkhead, and retry status.
    
    Returns:
        Dictionary with resilience statistics:
        - circuit_breaker: {state, failure_count, is_closed, is_open}
        - bulkhead: {available_permits, active_count, utilization, total_rejected}
        - retry: {config: {max_retries, base_delay, max_delay}}
    """
    return {
        "circuit_breaker": {
            "state": RoutingEngine._routing_circuit_breaker.state.name,
            "failure_count": RoutingEngine._routing_circuit_breaker.failure_count,
            "is_closed": RoutingEngine._routing_circuit_breaker.is_closed,
            "is_open": RoutingEngine._routing_circuit_breaker.is_open,
            "is_half_open": RoutingEngine._routing_circuit_breaker.is_half_open,
        },
        "bulkhead": {
            "available_permits": RoutingEngine._routing_bulkhead.available_permits,
            "active_count": RoutingEngine._routing_bulkhead.active_count,
            "utilization": RoutingEngine._routing_bulkhead.utilization,
            "total_acquired": RoutingEngine._routing_bulkhead.total_acquired,
            "total_rejected": RoutingEngine._routing_bulkhead.total_rejected,
            "max_concurrency": RoutingEngine._routing_bulkhead.max_concurrency,
        },
        "retry": {
            "config": {
                "max_retries": RoutingEngine._routing_retry_config.max_retries,
                "base_delay": RoutingEngine._routing_retry_config.base_delay,
                "max_delay": RoutingEngine._routing_retry_config.max_delay,
                "jitter_enabled": RoutingEngine._routing_retry_config.jitter,
            }
        },
    }
```

---

### Task 6: Wire Resilience into Pipeline Executor (Optional - 45 min)

**File:** `src/gaia/pipeline/stages/pipeline_executor.py`

**Note:** This is an OPTIONAL extension if time permits. The primary target is `routing_engine.py`.

**Changes:** Similar pattern to routing engine:
1. Add resilience imports
2. Create instance-level circuit breaker and bulkhead
3. Wrap agent execution calls with resilience primitives

---

## Test Strategy

### Unit Tests

**File:** `tests/unit/test_routing_engine_resilience.py` (CREATE NEW)

```python
"""Tests for RoutingEngine resilience primitives."""

import pytest
from unittest.mock import Mock, patch
from gaia.pipeline.routing_engine import RoutingEngine
from gaia.resilience import CircuitBreakerConfig, BulkheadConfig
from gaia.resilience.circuit_breaker import CircuitOpenError
from gaia.resilience.bulkhead import BulkheadFullError


class TestRoutingEngineResilience:
    """Test resilience primitives in RoutingEngine."""
    
    def test_circuit_breaker_trips_after_failures(self):
        """Circuit breaker opens after consecutive failures."""
        engine = RoutingEngine()
        
        # Simulate failures to trip circuit
        for i in range(5):
            with pytest.raises(Exception):  # Circuit should still allow calls until open
                # Trigger failure scenario
                pass
        
        # Circuit should now be open
        assert not engine._routing_circuit_breaker.is_closed
    
    def test_bulkhead_limits_concurrency(self):
        """Bulkhead rejects requests when at capacity."""
        engine = RoutingEngine()
        
        # Acquire all permits
        permits = []
        for _ in range(engine._routing_bulkhead.max_concurrency):
            permit = engine._routing_bulkhead.try_acquire()
            if permit:
                permits.append(permit)
        
        # Next acquire should fail
        with pytest.raises(BulkheadFullError):
            engine._routing_bulkhead.try_acquire()
    
    def test_get_resilience_stats(self):
        """Resilience stats method returns expected structure."""
        engine = RoutingEngine()
        stats = engine.get_resilience_stats()
        
        assert "circuit_breaker" in stats
        assert "bulkhead" in stats
        assert "retry" in stats
        assert stats["circuit_breaker"]["state"] == "CLOSED"
        assert stats["bulkhead"]["available_permits"] == engine._routing_bulkhead.max_concurrency
```

### Integration Tests

```python
def test_routing_with_circuit_breaker_recovery():
    """Test circuit breaker recovery after failures."""
    engine = RoutingEngine()
    
    # Trip the circuit
    engine._routing_circuit_breaker.trip()
    assert engine._routing_circuit_breaker.is_open
    
    # Wait for recovery timeout
    import time
    time.sleep(31)  # recovery_timeout + 1
    
    # Circuit should transition to half-open
    assert engine._routing_circuit_breaker.is_half_open


def test_bulkhead_releases_permits():
    """Test bulkhead permit release after execution."""
    engine = RoutingEngine()
    initial_permits = engine._routing_bulkhead.available_permits
    
    # Execute through bulkhead
    def dummy_op():
        return "success"
    
    result = engine._routing_bulkhead.execute(dummy_op)
    assert result == "success"
    
    # Permits should be released
    assert engine._routing_bulkhead.available_permits == initial_permits
```

---

## Risk Assessment

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Decorator causes issues with self parameter | MEDIUM | MEDIUM | Use inline wrapper approach (Option B) |
| Circuit breaker opens during normal operation | HIGH | LOW | Calibrate thresholds based on testing |
| Bulkhead timeout too aggressive | MEDIUM | MEDIUM | Set reasonable timeout (5s) with monitoring |
| Retry causes latency spikes | MEDIUM | MEDIUM | Use exponential backoff with jitter |
| Thread safety issues with shared instances | HIGH | LOW | Resilience classes are already thread-safe |

---

## Acceptance Criteria

- [ ] All 3 call sites wrapped with resilience primitives
- [ ] Circuit breaker trips after 5 consecutive failures
- [ ] Bulkhead limits concurrent agent execution to 10
- [ ] Retry with exponential backoff on transient failures
- [ ] `get_resilience_stats()` method returns monitoring data
- [ ] Unit tests verify resilience behavior
- [ ] No performance regression (>10% latency increase)
- [ ] No runtime errors from resilience primitives

---

## Files to Modify

| File | Action | Lines | Complexity |
|------|--------|-------|------------|
| `src/gaia/pipeline/routing_engine.py` | MODIFY | Add ~120 lines | MEDIUM |
| `tests/unit/test_routing_engine_resilience.py` | CREATE | ~80 lines | LOW |

---

## Dependencies

- **None** - This task is unblocked
- **Requires:** Resilience module (already complete)
- **Blocks:** None

---

## Configuration Tuning Guide

After implementation, monitor these metrics and tune as needed:

### Circuit Breaker Tuning

| Metric | Current | Adjust If | Recommendation |
|--------|---------|-----------|----------------|
| failure_threshold | 5 | Too many false opens | Increase to 7-10 |
| recovery_timeout | 30s | Recovery too slow | Decrease to 15-20s |
| success_threshold | 2 | Circuit flapping | Increase to 3-4 |

### Bulkhead Tuning

| Metric | Current | Adjust If | Recommendation |
|--------|---------|-----------|----------------|
| max_concurrency | 10 | High rejection rate | Increase to 15-20 |
| acquire_timeout | 5s | Timeouts on slow ops | Increase to 10s |

### Retry Tuning

| Metric | Current | Adjust If | Recommendation |
|--------|---------|-----------|----------------|
| max_retries | 3 | Too many retries | Decrease to 2 |
| base_delay | 0.5s | Latency too high | Decrease to 0.25s |
| max_delay | 5s | Retries too slow | Decrease to 2s |

---

**Document Version:** 1.0  
**Prepared By:** Jordan Lee, Senior Software Developer  
**Date:** 2026-04-11
