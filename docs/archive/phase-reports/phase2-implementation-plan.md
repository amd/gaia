# Phase 2 Implementation Plan

**Document Version:** 1.0
**Date:** 2026-04-06
**Status:** READY FOR KICKOFF
**Duration:** 8 weeks (Sprint 1-3)
**Owner:** senior-developer

---

## Executive Summary

Phase 2 (Quality Enhancement) implements the Supervisor Agent pattern, Workspace Sandboxing, and Context Lens Optimization to enhance the Chronicle-enabled state management established in Phase 1. This 8-week implementation adds LLM-based quality review, hard filesystem boundaries, and smart context summarization.

### Phase 2 Overview

| Dimension | Target | Notes |
|-----------|--------|-------|
| **Duration** | 8 weeks | 3 sprints (Sprint 1-2: 4 weeks, Sprint 3: 2 weeks) |
| **FTE Effort** | 12 person-weeks | senior-developer primary |
| **Deliverables** | 3 components | SupervisorAgent, WorkspacePolicy, ContextLens |
| **Exit Criteria** | Quality Gate 3 | 7 criteria, 95+ tests |

### Phase 1 Handoff Summary

Phase 1 established the foundational state layer:
- **NexusService** (763 LOC): Unified state management singleton
- **WorkspaceIndex** (embedded): Metadata tracking with TOCTOU fix
- **ChronicleDigest** (+230 LOC): Token-efficient event summarization
- **Agent-Nexus Integration** (+140 LOC): Agent event logging
- **Pipeline-Nexus Integration** (+100 LOC): Pipeline event logging

**Phase 1 Test Coverage:** 212 tests at 100% pass rate
**Quality Gate 2:** CONDITIONAL PASS (5/7 complete, 2 partial)

### Phase 2 Action Items from Phase 1

| ID | Action Item | Priority | Sprint | Owner |
|----|-------------|----------|--------|-------|
| AI-001 | Benchmark digest generation latency | HIGH | Sprint 1 | testing-quality-specialist |
| AI-002 | Implement tiktoken for accurate token counting | MEDIUM | Sprint 2 | senior-developer |
| AI-003 | Add performance monitoring hooks | MEDIUM | Sprint 1 | senior-developer |
| AI-004 | Document token budget tuning guide | LOW | Sprint 2 | technical-writer |

---

## 1. Implementation Scope

### 1.1 Components

| Component | File | LOC Estimate | Tests | Priority | Sprint |
|-----------|------|--------------|-------|----------|--------|
| **SupervisorAgent** | `src/gaia/quality/supervisor.py` | ~400 | 35 | P0 | Sprint 1-2 |
| **Supervisor Config** | `config/agents/quality-supervisor.yaml` | ~50 | N/A | P0 | Sprint 1 |
| **ReviewConsensusTool** | `src/gaia/tools/review_ops.py` | ~150 | 15 | P0 | Sprint 1 |
| **WorkspacePolicy** | `src/gaia/security/workspace.py` | ~300 | 25 | P1 | Sprint 3 |
| **TokenCounter** | `src/gaia/state/token_counter.py` | ~150 | 15 | P0 | Sprint 2 |
| **ContextLens** | `src/gaia/state/context_lens.py` | ~300 | 25 | P0 | Sprint 2 |
| **EmbeddingRelevance** | `src/gaia/state/relevance.py` | ~200 | 15 | P1 | Sprint 2 |
| **NexusService Extension** | `src/gaia/state/nexus.py` | +100 | +10 | P0 | Sprint 2 |
| **Pipeline Integration** | `src/gaia/pipeline/engine.py` | ~50 | 10 | P0 | Sprint 1-2 |

### 1.2 What's In Scope

- Supervisor Agent for LLM-based quality review
- ReviewConsensus tool with APPROVE/REJECT decision parsing
- Pipeline integration for Supervisor invocation after QUALITY phase
- Workspace sandboxing with hard filesystem boundaries
- Context Lens optimization with embedding-based relevance scoring
- Performance benchmarks for digest latency (target: <50ms)
- Tiktoken integration for accurate token counting
- Comprehensive test suite (95+ functions)

### 1.3 What's Out of Scope

- Agent-as-Data refactoring (Phase 3)
- Service Layer Decoupling (Phase 3)
- Full mixin decomposition (Phase 3)
- ConsensusOrchestrator unification (Phase 3)

---

## 2. Sprint Schedule

### Sprint 1: Supervisor Agent Core (Weeks 1-2)

**Objective:** Implement Supervisor Agent with review_consensus tool and basic pipeline integration.

#### Week 1: Supervisor Foundation

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create `config/agents/quality-supervisor.yaml` | senior-developer | Supervisor agent definition |
| 3 | Create `src/gaia/tools/review_ops.py` | senior-developer | review_consensus tool |
| 4-5 | Implement decision parsing logic | senior-developer | APPROVE/REJECT extraction |

#### Week 2: Supervisor Unit Tests

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Unit tests for SupervisorAgent | testing-quality-specialist | 20 test functions |
| 3 | Unit tests for review_consensus tool | testing-quality-specialist | 15 test functions |
| 4 | Decision parsing tests | testing-quality-specialist | Edge case coverage |
| 5 | Performance baseline | testing-quality-specialist | Latency benchmarks |

### Sprint 2: Pipeline Integration & Context Lens (Weeks 3-6)

**Objective:** Integrate Supervisor into PipelineEngine and implement Context Lens optimization.

### Sprint 2: Context Lens Optimization (Weeks 3-6)

**Objective:** Implement Context Lens optimization with accurate token counting, embedding-based relevance scoring, and performance benchmarks.

#### Week 3: TokenCounter & ContextLens Core

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create `src/gaia/state/token_counter.py` | senior-developer | TokenCounter with tiktoken integration |
| 3 | TokenCounter unit tests | senior-developer | 15 test functions |
| 4-5 | Create `src/gaia/state/context_lens.py` | senior-developer | ContextLens core implementation |

#### Week 4: EmbeddingRelevance & Testing

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create `src/gaia/state/relevance.py` | senior-developer | EmbeddingRelevance with fallback |
| 3 | EmbeddingRelevance tests | testing-quality-specialist | 15 test functions |
| 4-5 | ContextLens tests | testing-quality-specialist | 25 test functions |

#### Week 5: Nexus Integration & Performance

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Extend `src/gaia/state/nexus.py` | senior-developer | get_optimized_context(), get_enhanced_chronicle_digest() |
| 3 | Integration tests | testing-quality-specialist | 20 test functions |
| 4-5 | Performance benchmarks | testing-quality-specialist | Latency <50ms validation |

#### Week 6: Quality Gate 2 Prep

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Full regression testing | testing-quality-specialist | All tests passing |
| 3-4 | Quality Gate 2 validation | quality-reviewer | QG2 assessment |
| 5 | Sprint 2 closeout | software-program-manager | Sprint 2 summary |

### Sprint 3: Workspace Sandboxing (Weeks 7-8)

**Objective:** Implement hard filesystem boundaries, cross-pipeline isolation, and security enforcement.

**Technical Specification:** See `docs/reference/phase2-sprint3-technical-spec.md`

#### Week 7: Security Core Implementation

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1-2 | Create `src/gaia/security/workspace.py` | senior-developer | WorkspacePolicy class (~350 LOC) |
| 3 | Create `src/gaia/security/validator.py` | senior-developer | SecurityValidator (~200 LOC) |
| 4 | Implement path validation with hard boundaries | senior-developer | TOCTOU-safe validation |
| 5 | Unit tests for WorkspacePolicy | testing-quality-specialist | 30 test functions |

#### Week 8: Integration & Quality Gate 3

| Day | Task | Owner | Deliverable |
|-----|------|-------|-------------|
| 1 | Create `src/gaia/pipeline/isolation.py` | senior-developer | PipelineIsolation (~150 LOC) |
| 2 | Extend `src/gaia/state/nexus.py` | senior-developer | +50 LOC integration |
| 3 | Security penetration tests | testing-quality-specialist | 25 penetration test functions |
| 4 | Full regression testing | testing-quality-specialist | All 110 tests passing |
| 5 | Quality Gate 3 validation | quality-reviewer | QG3 assessment |
| 6 | Performance benchmarks | testing-quality-specialist | <5% overhead validation |
| 7 | Sprint 3 closeout | software-program-manager | Sprint 3 summary |
| 8 | Phase 2 closeout | software-program-manager | Phase 2 closeout document |

### Sprint 3 Detailed Deliverables

| Component | File | LOC | Tests | Status |
|-----------|------|-----|-------|--------|
| **WorkspacePolicy** | `src/gaia/security/workspace.py` | ~350 | 30 | READY |
| **SecurityValidator** | `src/gaia/security/validator.py` | ~200 | 20 | READY |
| **PipelineIsolation** | `src/gaia/pipeline/isolation.py` | ~150 | 15 | READY |
| **NexusService Extension** | `src/gaia/state/nexus.py` | +50 | +10 | READY |
| **Security Tests** | `tests/unit/security/` | N/A | 75 | READY |
| **Integration Tests** | `tests/unit/security/` | N/A | 25 | READY |
| **Total Test Suite** | Combined | N/A | 110 | TARGET: 100% PASS |

---

## 3. Technical Design

### 3.1 Supervisor Agent Architecture

```python
# config/agents/quality-supervisor.yaml
id: quality-supervisor
name: Quality Supervisor
role: Quality Assurance / Gatekeeper
system_prompt: |
  You are the Quality Supervisor for this pipeline.
  Your role is to review the collective output of all agents
  and issue a binary APPROVE or REJECT decision.

  If you REJECT, provide specific feedback for improvement.
  Use the review_consensus tool to submit your decision.
tools:
  - review_consensus
model: Qwen3.5-35B-A3B-GGUF
quality_threshold: 0.75  # Minimum score to consider for approval
```

```python
# src/gaia/quality/supervisor.py
"""
Supervisor Agent - LLM-based Quality Gate

Provides qualitative review of pipeline output with binary APPROVE/REJECT
decision making. Complements automated QualityScorer with LLM judgment.
"""

from dataclasses import dataclass
from typing import Literal, Optional
from gaia.agents.base.agent import Agent
from gaia.state.nexus import NexusService


@dataclass
class SupervisorDecision:
    """
    Structured supervisor decision record.

    Attributes:
        decision: Binary APPROVE or REJECT
        feedback: Detailed reasoning and improvement instructions
        quality_score_override: Optional override of automated score
        timestamp: Decision timestamp
        chronicle_reference: Event IDs reviewed for decision
    """
    decision: Literal["APPROVE", "REJECT"]
    feedback: str
    quality_score_override: Optional[float] = None
    timestamp: float = None
    chronicle_reference: list = None

    def __post_init__(self):
        if self.timestamp is None:
            import time
            self.timestamp = time.time()


class SupervisorAgent:
    """
    Quality gate agent for pipeline review.

    The SupervisorAgent reviews pipeline output after automated quality
    scoring and issues a binary decision. Rejection triggers LOOP_BACK
    with specific feedback for improvement.

    Decision Parsing Strategy:
    1. Check structured 'decision' field (primary)
    2. Scan 'summary' text for APPROVE keyword (fallback)
    3. Default to REJECT if ambiguous (fail-safe)

    Example:
        >>> supervisor = SupervisorAgent()
        >>> decision = await supervisor.review(context, quality_result)
        >>> if decision.decision == "REJECT":
        ...     trigger_loop_back(decision.feedback)
    """

    def __init__(self, model_id: str = "Qwen3.5-35B-A3B-GGUF"):
        self.model_id = model_id
        self._nexus = NexusService.get_instance()
        self._agent = self._create_agent()

    def _create_agent(self) -> Agent:
        """Create internal agent instance."""
        # Load from YAML config
        return Agent.from_yaml("config/agents/quality-supervisor.yaml")

    async def review(
        self,
        context: dict,
        quality_result: dict,
    ) -> SupervisorDecision:
        """
        Review pipeline output and issue decision.

        Args:
            context: Pipeline context with chronicle digest
            quality_result: Automated quality scoring results

        Returns:
            SupervisorDecision with APPROVE/REJECT and feedback
        """
        # Build review prompt
        prompt = self._build_review_prompt(context, quality_result)

        # Invoke LLM
        response = await self._agent.chat(prompt)

        # Parse decision
        decision = self._parse_decision(response)

        # Commit to Chronicle
        self._nexus.commit(
            agent_id="SupervisorAgent",
            event_type="supervisor_review",
            payload={
                "decision": decision.decision,
                "feedback": decision.feedback,
                "quality_score": quality_result.get("score"),
            },
        )

        return decision

    def _build_review_prompt(
        self,
        context: dict,
        quality_result: dict,
    ) -> str:
        """Build comprehensive review prompt."""
        chronicle_digest = self._nexus.get_chronicle_digest(
            max_events=20,
            max_tokens=2000,
        )

        return f"""
# Quality Review Task

Review the pipeline output and issue an APPROVE or REJECT decision.

## Automated Quality Score
- Overall Score: {quality_result.get('overall_score', 'N/A')}
- Threshold: {quality_result.get('threshold', 0.75)}
- Status: {'PASS' if quality_result.get('passed') else 'FAIL'}

## Chronicle Summary
{chronicle_digest}

## Your Task
1. Review the pipeline output quality
2. Consider the automated score
3. Issue APPROVE or REJECT with detailed feedback

Use the review_consensus tool to submit your decision.
"""

    def _parse_decision(self, response: dict) -> SupervisorDecision:
        """
        Parse decision from LLM response.

        Parsing Strategy:
        1. Check structured 'decision' field (primary)
        2. Scan 'summary' text for APPROVE (fallback)
        3. Default to REJECT if ambiguous

        Args:
            response: LLM response dictionary

        Returns:
            Parsed SupervisorDecision
        """
        # Try structured extraction first
        decision_text = response.get('decision', '')

        if decision_text == 'APPROVE':
            return SupervisorDecision(
                decision='APPROVE',
                feedback=response.get('feedback', ''),
            )
        elif decision_text == 'REJECT':
            return SupervisorDecision(
                decision='REJECT',
                feedback=response.get('feedback', ''),
            )

        # Fallback: scan summary text
        summary = response.get('summary', '').upper()
        if 'APPROVE' in summary:
            return SupervisorDecision(
                decision='APPROVE',
                feedback=response.get('summary', ''),
            )

        # Default to REJECT (fail-safe)
        return SupervisorDecision(
            decision='REJECT',
            feedback="Unable to parse clear decision from response.",
        )
```

---

### 3.2 Review Consensus Tool

```python
# src/gaia/tools/review_ops.py
"""
Review Operations Tool - Supervisor Decision Interface

Provides the review_consensus tool for SupervisorAgent to
submit APPROVE/REJECT decisions with feedback.
"""

import json
import time
from typing import Literal, Optional

from gaia.agents.base.tools import tool


@tool
def review_consensus(
    decision: Literal["APPROVE", "REJECT"],
    feedback: str,
    quality_score_override: Optional[float] = None,
) -> str:
    """
    Approve or Reject the current consensus reached by the team.

    This tool is used by the Quality Supervisor agent to submit
    a binary decision after reviewing pipeline output.

    Args:
        decision: Binary APPROVE or REJECT decision
        feedback: Detailed reasoning; improvement instructions on REJECT
        quality_score_override: Optional override of automated quality score

    Returns:
        JSON confirmation message

    Example:
        >>> review_consensus(
        ...     decision="REJECT",
        ...     feedback="Code quality needs improvement: missing error handling"
        ... )
        '{"decision": "REJECT", "status": "recorded"}'
    """
    result = {
        "decision": decision,
        "feedback": feedback,
        "timestamp": time.time(),
        "status": "recorded",
    }

    if quality_score_override is not None:
        if not 0.0 <= quality_score_override <= 1.0:
            raise ValueError("quality_score_override must be between 0.0 and 1.0")
        result["quality_score_override"] = quality_score_override

    return json.dumps(result, indent=2)
```

---

### 3.3 Pipeline Integration

```python
# In PipelineEngine._execute_quality() - src/gaia/pipeline/engine.py

async def _execute_quality_phase(
    self,
    context: PipelineContext,
) -> PhaseResult:
    """
    Execute QUALITY phase with optional Supervisor review.
    """
    # Run automated quality scoring
    quality_result = await self._run_quality_scorers(context)

    # Optionally invoke Supervisor agent
    if self._supervisor_enabled:
        from gaia.quality.supervisor import SupervisorAgent

        supervisor = SupervisorAgent()

        # Get context for supervisor
        review_context = {
            "chronicle_digest": self._nexus.get_chronicle_digest(
                max_events=20,
                max_tokens=2000,
            ),
            "quality_result": quality_result,
            "workspace_summary": self._workspace.get_index(),
        }

        # Invoke Supervisor review
        supervisor_result = await supervisor.review(
            context=review_context,
            quality_result=quality_result,
        )

        # Commit supervisor decision
        self._nexus.commit(
            agent_id="PipelineEngine",
            event_type="supervisor_decision",
            payload={
                "decision": supervisor_result.decision,
                "feedback": supervisor_result.feedback,
            },
            phase=PipelinePhase.QUALITY,
        )

        # Handle rejection - trigger LOOP_BACK
        if supervisor_result.decision == "REJECT":
            return PhaseResult(
                status=PhaseStatus.LOOP_BACK,
                reason=f"Supervisor rejected: {supervisor_result.feedback}",
                feedback=supervisor_result.feedback,
            )

    # Return automated quality result
    return PhaseResult(
        status=PhaseStatus.PASSED if quality_result.passed else PhaseStatus.FAILED,
        quality_score=quality_result.score,
    )
```

---

### 3.4 WorkspacePolicy (Sandboxing)

```python
# src/gaia/security/workspace.py
"""
Workspace Policy - Hard Filesystem Boundaries

Enforces mandatory sandboxing per pipeline execution with
path traversal protection and cross-pipeline isolation.
"""

import hashlib
import os
import threading
from pathlib import Path
from typing import Dict, Optional

from gaia.utils.logging import get_logger

logger = get_logger(__name__)


class WorkspacePolicy:
    """
    Hard filesystem boundary enforcement.

    Each pipeline execution gets a dedicated workspace sandbox.
    Cross-pipeline file access is blocked.

    Security Features:
    - Per-pipeline workspace isolation
    - Path traversal prevention (TOCTOU-safe)
    - Absolute path blocking
    - Symlink resolution protection

    Example:
        >>> policy = WorkspacePolicy(pipeline_id="pipe-001")
        >>> safe_path = policy.validate_path("src/main.py")
        >>> policy.write_file("output.py", content, "CodeAgent")
    """

    def __init__(
        self,
        pipeline_id: str,
        workspace_root: str = "./workspaces",
    ):
        """
        Initialize workspace policy for specific pipeline.

        Args:
            pipeline_id: Unique pipeline identifier
            workspace_root: Root directory for all workspaces
        """
        self.pipeline_id = pipeline_id
        self._workspace_root = Path(workspace_root).resolve()
        self._workspace = self._create_workspace()
        self._lock = threading.RLock()
        self._file_index: Dict[str, dict] = {}

        logger.info(
            f"WorkspacePolicy initialized for {pipeline_id}",
            extra={"workspace": str(self._workspace)},
        )

    def _create_workspace(self) -> Path:
        """Create dedicated workspace directory."""
        # Hash pipeline_id for unique workspace name
        workspace_hash = hashlib.sha256(
            self.pipeline_id.encode()
        ).hexdigest()[:12]
        workspace = self._workspace_root / workspace_hash
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def validate_path(self, relative_path: str) -> Path:
        """
        Validate and resolve path within workspace boundary.

        SECURITY: All checks run BEFORE path normalization (TOCTOU-safe).

        Args:
            relative_path: Relative path from workspace root

        Returns:
            Resolved absolute path within workspace

        Raises:
            SecurityError: If path traversal or boundary violation detected
        """
        # CRITICAL: Check BEFORE normalization
        if not self._is_path_safe(relative_path):
            raise SecurityError(
                f"Path traversal detected: {relative_path}"
            )

        # Now safe to normalize
        normalized = self._normalize_path(relative_path)
        resolved = (self._workspace / normalized).resolve()

        # Verify resolved path is within workspace
        if not self._is_within_boundary(resolved):
            raise SecurityError(
                f"Path outside workspace boundary: {resolved}"
            )

        return resolved

    def _is_path_safe(self, path: str) -> bool:
        """
        Check path safety BEFORE normalization.

        Blocks:
        - Parent traversal (..)
        - Absolute Unix paths (/)
        - Absolute Windows paths (C:)
        - Symlinks pointing outside

        Args:
            path: Original path string (BEFORE normalization)

        Returns:
            True if safe, False if traversal detected
        """
        # Block traversal patterns
        if ".." in path:
            return False

        # Block absolute paths (BEFORE normalization strips them)
        if path.startswith("/"):
            return False

        # Block Windows absolute paths
        if len(path) > 1 and path[1] == ":":
            return False

        return True

    def _normalize_path(self, path: str) -> str:
        """Normalize path (cross-platform)."""
        # Convert backslashes
        normalized = path.replace("\\", "/")
        # Strip leading slashes
        normalized = normalized.lstrip("/")
        # Collapse multiple slashes
        while "//" in normalized:
            normalized = normalized.replace("//", "/")
        return normalized

    def _is_within_boundary(self, resolved: Path) -> bool:
        """Verify path is within workspace boundary."""
        workspace_str = str(self._workspace)
        resolved_str = str(resolved)
        return resolved_str.startswith(workspace_str + os.sep) or resolved_str == workspace_str

    def write_file(
        self,
        relative_path: str,
        content: bytes,
        modified_by: str,
    ) -> dict:
        """
        Write file within workspace boundary.

        Args:
            relative_path: Relative path from workspace root
            content: File content in bytes
            modified_by: Agent/pipeline identifier

        Returns:
            File metadata record

        Raises:
            SecurityError: If path validation fails
        """
        with self._lock:
            # Validate and resolve path
            full_path = self.validate_path(relative_path)

            # Ensure parent directory exists
            full_path.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            full_path.write_bytes(content)

            # Update index
            import time
            import hashlib

            metadata = {
                "path": relative_path,
                "size_bytes": len(content),
                "content_hash": hashlib.sha256(content).hexdigest(),
                "last_modified": time.time(),
                "modified_by": modified_by,
                "pipeline_id": self.pipeline_id,
            }

            self._file_index[relative_path] = metadata

            return metadata

    def get_index(self) -> Dict[str, dict]:
        """Get current file index."""
        with self._lock:
            return self._file_index.copy()

    def get_workspace_path(self) -> Path:
        """Get workspace root path."""
        return self._workspace


class SecurityError(Exception):
    """Raised when security boundary is violated."""
    pass
```

---

### 3.5 Context Lens Optimization

```python
# Extension to src/gaia/state/nexus.py

def get_optimized_context(
    self,
    agent_id: str,
    max_tokens: int = 3500,
    use_embeddings: bool = True,
) -> dict:
    """
    Generate optimized context with smart prioritization.

    Uses multiple signals for relevance scoring:
    1. Recency (temporal decay)
    2. Agent relevance (collaborative filtering)
    3. Event importance (type-based weighting)
    4. File modification activity

    Args:
        agent_id: Target agent identifier
        max_tokens: Maximum token budget
        use_embeddings: Use embedding-based relevance if available

    Returns:
        Context dictionary with curated content
    """
    with self._state_lock:
        # Score events by relevance
        scored_events = self._score_events_for_agent(
            agent_id,
            use_embeddings=use_embeddings,
        )

        # Select top events within token budget
        selected_events = self._select_events_within_budget(
            scored_events,
            max_tokens,
        )

        # Build optimized context
        return {
            "selected_events": selected_events,
            "workspace_files": self._workspace.get_recent(5),
            "phase_summary": self._get_phase_summary_for_agent(agent_id),
            "token_count": self._count_tokens(selected_events),
        }

def _score_events_for_agent(
    self,
    agent_id: str,
    use_embeddings: bool,
) -> list:
    """
    Score events by relevance to specific agent.

    Scoring factors:
    - Recency: More recent = higher score
    - Agent proximity: Same agent = +2, Related agent = +1
    - Event type: Quality/Decision events = +1
    - Phase relevance: Current phase = +1

    Args:
        agent_id: Target agent
        use_embeddings: Use embedding similarity (future)

    Returns:
        List of (event, score) tuples sorted by score
    """
    scored = []

    for event in reversed(self._event_cache):
        score = 1.0  # Base score

        # Recency decay (exponential)
        age_hours = (time.time() - event["timestamp"]) / 3600
        recency_score = 1.0 / (1.0 + age_hours)
        score *= recency_score

        # Agent proximity
        event_agent = event.get("agent_id", "")
        if event_agent == agent_id:
            score += 2.0
        elif self._are_agents_related(agent_id, event_agent):
            score += 1.0

        # Event type weighting
        if event["event_type"] in ["quality_evaluated", "decision_made"]:
            score += 1.0

        scored.append((event, score))

    return sorted(scored, key=lambda x: x[1], reverse=True)
```

---

## 4. Test Strategy

### 4.1 Test Matrix

| Test File | Functions | Coverage | Priority |
|-----------|-----------|----------|----------|
| `test_supervisor_agent.py` | 41 | SupervisorAgent | CRITICAL |
| `test_review_consensus_tool.py` | 15 | review_ops tool | CRITICAL |
| `test_supervisor_pipeline_integration.py` | 18 | Full integration | CRITICAL |
| `test_token_counter.py` | 15 | TokenCounter | CRITICAL |
| `test_context_lens.py` | 25 | Context optimization | CRITICAL |
| `test_relevance.py` | 15 | EmbeddingRelevance | HIGH |
| `test_context_integration.py` | 20 | End-to-end integration | HIGH |
| `test_performance_benchmarks.py` | 10 | Latency/Memory | MEDIUM |
| `test_workspace_policy.py` | 25 | WorkspacePolicy | CRITICAL |
| **Total** | **169+** | **Full coverage** | |

### 4.2 Key Test Functions

**SupervisorAgent Tests:**
```python
class TestSupervisorAgent:
    def test_supervisor_initialization(self):
        """Verify SupervisorAgent initializes correctly."""

    def test_decision_parsing_approve(self):
        """Test APPROVE decision parsing from structured response."""

    def test_decision_parsing_reject(self):
        """Test REJECT decision parsing."""

    def test_decision_parsing_fallback(self):
        """Test fallback parsing from summary text."""

    def test_decision_default_reject(self):
        """Test default-to-reject fail-safe."""

    def test_review_with_quality_pass(self):
        """Test review when automated quality passes."""

    def test_review_with_quality_fail(self):
        """Test review when automated quality fails."""

    def test_chronicle_commit(self):
        """Verify supervisor decisions committed to Chronicle."""

    def test_thread_safety(self):
        """Test concurrent supervisor invocations."""
```

**Review Consensus Tool Tests:**
```python
class TestReviewConsensusTool:
    def test_approve_decision(self):
        """Test APPROVE decision returns correct JSON."""

    def test_reject_decision(self):
        """Test REJECT decision returns correct JSON."""

    def test_quality_score_override(self):
        """Test optional quality score override."""

    def test_invalid_score_override(self):
        """Test rejection of out-of-range scores."""

    def test_feedback_required(self):
        """Test feedback field is always present."""
```

**WorkspacePolicy Tests:**
```python
class TestWorkspacePolicy:
    def test_workspace_creation(self):
        """Verify workspace directory created with hash name."""

    def test_path_traversal_blocked(self):
        """Test ../ path traversal is blocked."""

    def test_absolute_unix_path_blocked(self):
        """Test /etc/passwd style paths blocked."""

    def test_absolute_windows_path_blocked(self):
        """Test C:\\Windows style paths blocked."""

    def test_cross_pipeline_access_blocked(self):
        """Test pipeline A cannot access pipeline B's workspace."""

    def test_write_file_within_boundary(self):
        """Test file writes succeed within boundary."""

    def test_file_index_updated(self):
        """Test file metadata index updated on write."""

    def test_symlink_outside_blocked(self):
        """Test symlinks pointing outside are blocked."""
```

### 4.3 Quality Gate 2 Criteria (Sprint 2)

| Criteria | Test | Target | Priority |
|----------|------|--------|----------|
| **LENS-001** | Token counting accuracy | >95% vs tiktoken | CRITICAL |
| **LENS-002** | Relevance scoring accuracy | >80% correlation | HIGH |
| **SUP-001** | Supervisor decision parsing | 100% accuracy | CRITICAL |
| **SUP-002** | Pipeline LOOP_BACK on rejection | Automatic trigger | CRITICAL |
| **SUP-003** | Chronicle commit integrity | Hash chain preserved | CRITICAL |
| **PERF-002** | Digest generation latency | <50ms (95th %ile) | CRITICAL |
| **PERF-004** | Memory footprint | <1MB context state | HIGH |
| **BC-002** | Backward compatibility | 100% existing calls | CRITICAL |

### 4.4 Quality Gate 3 Criteria (Sprint 3)

| Criteria | Test | Target | Priority |
|----------|------|--------|----------|
| **WORK-003** | Workspace boundary enforcement | 0% bypass | CRITICAL |
| **WORK-004** | Cross-pipeline isolation | 100% isolation | CRITICAL |
| **PERF-003** | Supervisor latency | <2s per review | HIGH |

---

## 5. Risk Management

### 5.1 Active Risks

| ID | Risk | Probability | Impact | Mitigation | Owner |
|----|------|-------------|--------|------------|-------|
| R2.1 | Supervisor hallucination | MEDIUM | MEDIUM | Combine with automated scorer, make optional | senior-developer |
| R2.2 | Decision parsing failures | LOW | HIGH | Multiple fallback strategies, default-to-reject | senior-developer |
| R2.3 | Performance regression | MEDIUM | MEDIUM | Benchmark early, async supervisor invocation | testing-quality-specialist |
| R2.4 | Workspace boundary bypass | LOW | HIGH | TOCTOU-safe checks, multiple validation layers | senior-developer |
| R2.5 | Embedding availability | MEDIUM | LOW | Graceful fallback to rule-based scoring | senior-developer |
| R2.6 | Token counting variance | MEDIUM | LOW | tiktoken integration, hierarchical budget enforcement | senior-developer |
| R2.7 | Memory bloat | LOW | MEDIUM | Lazy initialization, cleanup | senior-developer |
| R2.8 | Backward compatibility break | LOW | HIGH | Extensive BC testing, deprecation warnings | senior-developer |

### 5.2 Risk Triggers

| Risk | Trigger | Action |
|------|---------|--------|
| R2.1 | Supervisor approves clearly defective output | Add evaluation harness, calibrate prompts |
| R2.2 | >5% decision parsing failures | Escalate to manual review, log for analysis |
| R2.3 | Digest latency >100ms | Optimize summarization, cache aggressively |
| R2.4 | Any workspace bypass succeeds | Immediate security review, patch deployment |
| R2.5 | Embedding service unavailable | Fall back to rule-based scoring automatically |
| R2.6 | Token variance >20% | Integrate tiktoken, adjust estimation |

---

## 6. Integration Points with Phase 1

### 6.1 NexusService Dependencies

Phase 2 extends Phase 1 components:

| Phase 1 Component | Phase 2 Extension | Integration Method |
|-------------------|-------------------|-------------------|
| `NexusService.get_digest()` | `get_optimized_context()` | Method overload |
| `WorkspaceIndex.get_index()` | `WorkspacePolicy.validate_path()` | Security enhancement |
| `AuditLogger.get_digest()` | Chronicle-based review context | Direct usage |
| `PipelineEngine._execute_quality()` | Supervisor invocation | Method wrapping |

### 6.2 Backward Compatibility

Phase 2 maintains backward compatibility:

- Supervisor is **opt-in** via `_supervisor_enabled` flag
- Existing pipelines continue without Supervisor review
- WorkspacePolicy is per-pipeline, doesn't affect existing file operations
- Context Lens optimization is additive, doesn't break existing digest calls

---

## 7. Success Metrics

### 7.1 Technical Metrics

| Metric | Baseline (Phase 1) | Target | Measurement |
|--------|-------------------|--------|-------------|
| Supervisor latency | N/A | <2s | Benchmark |
| Digest latency | Not benchmarked | <50ms | Benchmark |
| Token counting variance | ~20% | <5% | Comparison with tiktoken |
| Decision parsing accuracy | N/A | >95% | Eval harness |
| Workspace bypass attempts | N/A | 0% success | Security tests |
| Cross-pipeline contamination | N/A | 0% | Isolation tests |

### 7.2 Quality Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Test coverage | 100% | `pytest --cov` |
| Test pass rate | 100% | All tests pass |
| Thread safety | Verified | 100-thread tests |
| Security | 0% bypass | Security tests |
| Performance | All benchmarks green | perf tests |

---

## 8. Dependencies

### 8.1 Internal Dependencies

```
Phase 1 Complete
    │
    ▼
┌─────────────────┐
│  NexusService   │
│  WorkspaceIndex │
│  ChronicleDigest│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Phase 2       │
│  Supervisor     │◄───────┐
│  WorkspacePolicy│        │
│  ContextLens    │        │
└────────┬────────┘        │
         │                 │
    ┌────┴────┬────────────┘
    │         │
    ▼         ▼
┌────────┐ ┌─────────┐
│Pipeline│ │ Quality │
│Engine  │ │ Scorer  │
└────────┘ └─────────┘
```

### 8.2 External Dependencies

| Dependency | Purpose | Version | Installation |
|------------|---------|---------|--------------|
| `tiktoken` | Token counting | >=0.5.0 | `pip install tiktoken` |
| `numpy` | Embedding computation (optional) | >=1.24.0 | `pip install numpy` |
| `sentence-transformers` | Embedding model (optional) | >=2.2.0 | `pip install sentence-transformers` |

---

## 9. Effort Estimates

### 9.1 Developer Effort

| Sprint | Duration | FTE | Total Person-Weeks |
|--------|----------|-----|-------------------|
| Sprint 1 (Supervisor Core) | 2 weeks | 1.0 | 2.0 |
| Sprint 2 (Integration + Context) | 4 weeks | 1.0 | 4.0 |
| Sprint 3 (Workspace Sandboxing) | 2 weeks | 1.0 | 2.0 |
| **Total** | **8 weeks** | | **8.0** |

### 9.2 Testing Effort

| Sprint | Testing FTE | Person-Weeks | Focus |
|--------|-------------|--------------|-------|
| Sprint 1 | 0.5 | 1.0 | Supervisor tests |
| Sprint 2 | 0.5 | 2.0 | Integration + perf |
| Sprint 3 | 0.5 | 1.0 | Security tests |
| **Total** | | **4.0** | |

### 9.3 Documentation Effort

| Document | Owner | Effort | Sprint |
|----------|-------|--------|--------|
| Supervisor Agent Guide | technical-writer | 0.5 weeks | Sprint 2 |
| Workspace Policy Guide | technical-writer | 0.5 weeks | Sprint 3 |
| Token Budget Tuning | technical-writer | 0.25 weeks | Sprint 2 |
| Phase 2 Closeout Report | software-program-manager | 0.5 weeks | Sprint 3 |

---

## 10. File Modification Summary

| File | Change Type | LOC Estimate | Sprint |
|------|-------------|--------------|--------|
| `src/gaia/quality/supervisor.py` | **NEW** | ~400 | Sprint 1-2 |
| `src/gaia/tools/review_ops.py` | **NEW** | ~150 | Sprint 1 |
| `src/gaia/security/workspace.py` | **NEW** | ~300 | Sprint 3 |
| `src/gaia/state/token_counter.py` | **NEW** | ~150 | Sprint 2 |
| `src/gaia/state/context_lens.py` | **NEW** | ~300 | Sprint 2 |
| `src/gaia/state/relevance.py` | **NEW** | ~200 | Sprint 2 |
| `src/gaia/state/nexus.py` | MODIFY | +100 | Sprint 2 |
| `src/gaia/pipeline/engine.py` | MODIFY | +50 | Sprint 1-2 |
| `config/agents/quality-supervisor.yaml` | **NEW** | ~50 | Sprint 1 |
| `tests/unit/quality/test_supervisor_agent.py` | **NEW** | ~400 LOC, 41 tests | Sprint 1-2 |
| `tests/unit/tools/test_review_ops.py` | **NEW** | ~200 LOC, 15 tests | Sprint 1 |
| `tests/unit/quality/test_supervisor_integration.py` | **NEW** | ~600 LOC, 18 tests | Sprint 2 |
| `tests/unit/state/test_token_counter.py` | **NEW** | ~200 LOC, 15 tests | Sprint 2 |
| `tests/unit/state/test_context_lens.py` | **NEW** | ~350 LOC, 25 tests | Sprint 2 |
| `tests/unit/state/test_relevance.py` | **NEW** | ~250 LOC, 15 tests | Sprint 2 |
| `tests/unit/state/test_context_integration.py` | **NEW** | ~300 LOC, 20 tests | Sprint 2 |
| `tests/unit/state/test_performance_benchmarks.py` | **NEW** | ~200 LOC, 10 tests | Sprint 2 |
| `tests/unit/security/test_workspace_policy.py` | **NEW** | ~350 LOC, 25 tests | Sprint 3 |

---

## 11. Handoff Notes

### 11.1 For software-program-manager

**Resource Allocation:**
- senior-developer: 8 weeks full-time
- testing-quality-specialist: 4 weeks (Sprint 1, 2, 3)
- quality-reviewer: Week 8 for Quality Gate 3
- technical-writer: 1.25 weeks (Sprint 2-3)

**Milestone Tracking:**
- Weekly progress reviews every Friday
- Escalate R2.1/R2.4 risks immediately
- Track against sprint schedule

### 11.2 For senior-developer

**Implementation Notes:**
1. Start with Supervisor Agent + review_consensus tool (Week 1)
2. Make Supervisor opt-in initially (fail-safe)
3. Implement decision parsing with multiple fallbacks
4. WorkspacePolicy must be TOCTOU-safe (check BEFORE normalize)
5. Benchmark digest latency early (target: <50ms)

**Key Design Decisions:**
- Supervisor decision: structured field + text fallback + default-to-reject
- WorkspacePolicy: per-pipeline isolation with hash-named directories
- ContextLens: rule-based scoring first, embeddings optional

### 11.3 For testing-quality-specialist

**Test Priorities:**
1. Security tests (workspace boundary enforcement)
2. Decision parsing accuracy (Supervisor)
3. Performance benchmarks (digest latency <50ms, supervisor <2s)
4. Integration tests (Pipeline-Supervisor workflow)

**Test Infrastructure:**
- pytest 8.4.2+
- pytest-benchmark for performance
- pytest-asyncio for async tests
- tiktoken for token counting validation

---

## 12. Approval & Sign-Off

**Prepared By:** Dr. Sarah Kim, planning-analysis-strategist
**Date:** 2026-04-06
**Next Action:** senior-developer begins Sprint 1

### Sign-Off Checklist

- [x] Technical feasibility confirmed
- [x] Resource allocation confirmed
- [x] Risk assessment acceptable
- [x] Test strategy comprehensive
- [x] Quality criteria defined
- [ ] **Team approval to begin Phase 2**

---

## Appendix A: Phase 1 Closeout Reference

Per `docs/reference/phase1-sprint3-closeout.md`:

**Phase 1 Achievements:**
- NexusService: 763 LOC, 79 tests
- ChronicleDigest: +230 LOC, 59 tests
- Agent-Nexus: +140 LOC, 43 tests
- Pipeline-Nexus: +100 LOC, 31 tests
- **Total: 212 tests at 100% pass rate**

**Quality Gate 2 Result:** CONDITIONAL PASS (5/7 complete)

**Action Items Carried to Phase 2:**
- AI-001: Benchmark digest latency (HIGH)
- AI-002: Tiktoken integration (MEDIUM)
- AI-003: Performance monitoring (MEDIUM)
- AI-004: Token budget guide (LOW)

---

## Appendix B: Phase 2 Progress Tracker

**Template for Weekly Status Updates:**

```
### Week N Status (Sprint X)

| Dimension | Status | Notes |
|-----------|--------|-------|
| Schedule | ON TRACK / AT RISK / DELAYED | |
| Budget | ON BUDGET / OVER | |
| Quality | ALL TESTS PASS / ISSUES | |

**Completed:**
- Task 1
- Task 2

**Blockers:**
- Blocker 1 (owner: XXX)

**Next Week:**
- Planned task 1
- Planned task 2
```

---

**END OF PLAN**

**Distribution:** GAIA Development Team, AMD AI Framework Team
**Review Cadence:** Weekly status reviews
**Version History:**
- v1.0: Initial Phase 2 specification (2026-04-06)
