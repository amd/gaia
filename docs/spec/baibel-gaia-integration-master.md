# BAIBEL-GAIA Master Integration Specification

**Version:** 2.3
**Status:** Phase 0 COMPLETE - Phase 1 COMPLETE - Phase 2 COMPLETE - Phase 3 Sprint 1 COMPLETE - Phase 3 Sprint 2 COMPLETE
**Classification:** Strategic Architecture Document
**Date:** 2026-04-06
**Last Updated:** 2026-04-06 (Phase 3 Sprint 2 COMPLETE - DI + Performance)

---

## Program Status Update

### Phase 0 & Phase 1 & Phase 2 & Phase 3 Completion Summary

| Phase | Status | Completion | Quality Gate | Owner |
|-------|--------|------------|--------------|-------|
| **Phase 0: Tool Scoping** | **COMPLETE** | **100%** | **QG1 PASSED** | senior-developer |
| **Phase 1 Sprint 1: Nexus Service Core** | **COMPLETE** | **100%** | **N/A** | senior-developer |
| **Phase 1 Sprint 2: ChronicleDigest & Agent Integration** | **COMPLETE** | **100%** | **N/A** | senior-developer |
| **Phase 1 Sprint 3: Pipeline-Nexus Integration** | **COMPLETE** | **100%** | **QG2 CONDITIONAL PASS** | senior-developer |
| **Phase 1 (Overall)** | **COMPLETE** | **100%** | **QG2 CONDITIONAL PASS** | senior-developer |
| **Phase 2 Sprint 1: Supervisor Agent Core** | **COMPLETE** | **100%** | **QG2 PASS** | senior-developer |
| **Phase 2 Sprint 2: Context Lens Optimization** | **COMPLETE** | **100%** | **QG2 PASS** | senior-developer |
| **Phase 2 Sprint 3: Workspace Sandboxing** | **COMPLETE** | **100%** | **QG3 PASS** | senior-developer |
| **Phase 2 (Overall)** | **COMPLETE** | **100%** | **QG3 PASS** | senior-developer |
| **Phase 3 Sprint 1: Modular Architecture Core** | **COMPLETE** | **100%** | **QG4 PASS** | senior-developer |
| **Phase 3 Sprint 2: DI + Performance** | **COMPLETE** | **100%** | **QG4 PASS** | senior-developer |
| Phase 3 Sprint 3: Caching + Enterprise Config | PENDING | 0% | Pending | senior-developer |

**Overall Program:** ~85% Complete (Phase 0 + Phase 1 + Phase 2 + Phase 3 S1 + S2 done), Phase 3 S3 Pending

### Implementation Roadmap: Actual vs. Planned Progress

```
COMPLETED:
┌─────────────────────────────────────────────────────────────────┐
│ Phase 0: Tool Scoping (2 weeks)                                 │
│ - ToolRegistry, AgentScope, ExceptionRegistry                   │
│ - 884 LOC implementation, 204 tests (100% pass)                 │
│ - Quality Gate 1: ALL CRITERIA PASSED                           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 1 Sprint 1: Nexus Service Core (COMPLETED)                │
│ - NexusService: 763 LOC, thread-safe singleton                  │
│ - WorkspaceIndex: Embedded, path traversal protection           │
│ - 79 tests (100% pass rate)                                     │
│ - TOCTOU vulnerability identified and FIXED                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 1 Sprint 2: ChronicleDigest & Agent Integration (DONE)    │
│ - ChronicleDigest: +230 LOC in AuditLogger                      │
│ - Agent-Nexus Integration: +140 LOC in Agent base               │
│ - 102 tests (59 ChronicleDigest + 43 Agent-Nexus)               │
│ - Token-efficient context summarization                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 1 Sprint 3: Pipeline-Nexus Integration (DONE)             │
│ - PipelineEngine Nexus integration: +100 LOC                    │
│ - 8 event types logged to Chronicle                             │
│ - 31 tests (100% pass rate)                                     │
│ - Quality Gate 2: CONDITIONAL PASS (5/7 complete)               │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 2 Sprint 1: Supervisor Agent Core (COMPLETED)             │
│ - SupervisorAgent: 848 LOC, quality review orchestration        │
│ - ReviewOps: 526 LOC, consensus aggregation tools               │
│ - 59 tests (41 unit + 18 integration, 100% pass)                │
│ - Quality Gate 2: PASS (all 3 criteria met)                     │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 2 Sprint 2: Context Lens Optimization (COMPLETED)         │
│ - TokenCounter: 336 LOC, tiktoken integration                   │
│ - ContextLens: 569 LOC, relevance-based prioritization          │
│ - EmbeddingRelevance: 443 LOC, semantic similarity              │
│ - NexusService Extension: +114 LOC                              │
│ - 117 tests (100% pass, 2 skipped), Quality Gate 2: PASS        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 2 Sprint 3: Workspace Sandboxing (COMPLETED)              │
│ - WorkspacePolicy: 667 LOC, hard filesystem boundaries          │
│ - SecurityValidator: 503 LOC, audit logging                     │
│ - PipelineIsolation: 541 LOC, cross-pipeline isolation          │
│ - NexusService Extension: +80 LOC                               │
│ - 98 tests (100% pass), Quality Gate 3: PASS                    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 3 Sprint 1: Modular Architecture Core (COMPLETED)         │
│ - AgentCapabilities: 340 LOC, validation, tool operations       │
│ - AgentProfile: 360 LOC, spec-aligned fields (id, role)         │
│ - AgentExecutor: 650 LOC, behavior injection, hooks             │
│ - PluginRegistry: 680 LOC, lazy loading, <1ms lookup            │
│ - Core Module: 80 LOC, clean public API                         │
│ - 195 tests (100% pass), Quality Gate 4: PASS                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 3 Sprint 2: DI + Performance (COMPLETED)                  │
│ - DIContainer: 770 LOC, 3 lifetime scopes, circular detection   │
│ - AgentAdapter: 545 LOC, 100% backward compatibility            │
│ - AsyncUtils: 703 LOC, caching, retry, rate limit, circuit      │
│ - ConnectionPool: 787 LOC, >100 req/s throughput                │
│ - Perf Module: ~50 LOC, clean public API                        │
│ - 157 tests (100% pass), Quality Gate 4: PASS                   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
PENDING:
┌─────────────────────────────────────────────────────────────────┐
│ Phase 3 Sprint 3: Caching + Enterprise Config (Weeks 7-9)       │
│ - CacheLayer: Multi-tier caching with Redis support             │
│ - ConfigSchema: Pydantic-based validation                       │
│ - ConfigManager: Lifecycle management with hot reload           │
│ - SecretsManager: AES-256 encryption for sensitive config       │
└─────────────────────────────────────────────────────────────────┘
```

### Phase 0 Final Deliverables

| Deliverable | Target | Actual | Status |
|-------------|--------|--------|--------|
| tools.py LOC | ~450 | 884 | EXCEEDED |
| Test Functions | 90 | 204 | EXCEEDED |
| Test Pass Rate | 100% | 100% (204/204) | PASS |
| BC-001 Backward Compat | 100% | 100% (40/40) | PASS |
| SEC-001 Security | 0% bypass | 0% (27/27) | PASS |
| PERF-001 Performance | <10% | <10% | PASS |
| MEM-001 Memory | 0% leak | 0% (7/7) | PASS |

### Phase 1 Sprint 1 Deliverables

| Deliverable | Target | Actual | Status |
|-------------|--------|--------|--------|
| NexusService LOC | ~300 | 763 | EXCEEDED |
| WorkspaceIndex | Embedded | Embedded in nexus.py | COMPLETE |
| Test Functions | 60 | 79 | EXCEEDED |
| Test Pass Rate | 100% | 100% (79/79) | PASS |
| Thread Safety | Verified | 100+ concurrent threads | PASS |
| Security (TOCTOU) | Fixed | Path check BEFORE normalization | PASS |

### Phase 1 Sprint 2 Deliverables

| Deliverable | Target | Actual | Status |
|-------------|--------|--------|--------|
| ChronicleDigest LOC | ~200 | +230 (in AuditLogger) | EXCEEDED |
| Agent-Nexus Integration | ~100 | +140 (in Agent base) | EXCEEDED |
| Test Functions | 80 | 102 | EXCEEDED |
| Test Pass Rate | 100% | 100% (102/102) | PASS |
| Token Budget | <4000 tokens | Hierarchical enforcement | PASS |
| Thread Safety | Verified | 100+ concurrent events | PASS |

### Phase 1 Sprint 3 Deliverables

| Deliverable | Target | Actual | Status |
|-------------|--------|--------|--------|
| Pipeline-Nexus Integration | ~100 LOC | +100 LOC in engine.py | COMPLETE |
| Event Types Implemented | 8 types | 8 types logged | COMPLETE |
| Test Functions | 25 | 31 | EXCEEDED |
| Test Pass Rate | 100% | 100% (31/31) | PASS |
| Thread Safety | 100+ threads | Verified | PASS |
| Quality Gate 2 | 7 criteria | 5/7 complete, 2 partial | CONDITIONAL PASS |

### Quality Gate 2 Final Results (Phase 1)

| Criteria | Target | Actual | Status |
|----------|--------|--------|--------|
| STATE-001: State Service Singleton | Single instance | Verified | **PASS** |
| STATE-002: Snapshot Mutation-Safety | Deep copy | Verified | **PASS** |
| CHRON-001: Event Timestamp Precision | Microsecond | Verified | **PASS** |
| CHRON-002: Digest Token Efficiency | <4000 tokens | Hierarchical enforcement | **PARTIAL** |
| WORK-001: Metadata Tracking | All changes recorded | Verified | **PASS** |
| WORK-002: Path Traversal Prevention | 0% bypass | TOCTOU fix in place | **PASS** |
| PERF-002: Digest Generation Latency | <50ms | Not benchmarked | **PARTIAL** |

**Decision:** CONDITIONAL PASS - 5/7 criteria complete, 2 partial

**Action Items:**
- AI-001: Benchmark digest generation latency (HIGH, Phase 2 Sprint 1)
- AI-002: Implement tiktoken for accurate token counting (MEDIUM, Phase 2 Sprint 2)
- AI-003: Add performance monitoring hooks (MEDIUM, Phase 2 Sprint 1)
- AI-004: Document token budget tuning guide (LOW, Phase 2 Sprint 2)

### Quality Gate 1 Final Results (Phase 0)

| Criteria | Target | Actual | Status |
|----------|--------|--------|--------|
| BC-001: Backward Compatibility | 100% pass | 100% (40/40) | **PASS** |
| SEC-001: Allowlist Bypass | 0% success | 0% (27/27) | **PASS** |
| PERF-001: Performance Overhead | <10% | <10% | **PASS** |
| MEM-001: Memory Leaks | 0% leak | 0% (7/7) | **PASS** |

**Decision:** GO - APPROVED FOR PHASE 1 (Sprint 1 COMPLETE, Sprint 2 READY)

---

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Complete Pattern Catalog (WHAT)](#2-complete-pattern-catalog-what)
3. [Integration Map (WHERE)](#3-integration-map-where)
4. [Implementation Strategy (HOW)](#4-implementation-strategy-how)
5. [4-Phase Integration Roadmap](#5-4-phase-integration-roadmap)
6. [Test Strategy](#6-test-strategy)
7. [Risk Register](#7-risk-register)
8. [Success Metrics](#8-success-metrics)

---

## 1. Executive Summary

This specification details the complete integration of BAIBEL architectural patterns into GAIA (AMD's open-source AI agent framework). The integration addresses **8 documented pain points** in GAIA through adoption of **8 BAIBEL patterns** across **4 phases** totaling **66 person-weeks** of development effort.

### 1.1 Integration Overview

| Dimension | Assessment |
|-----------|------------|
| **Strategic Alignment** | HIGH -- Both systems target multi-agent orchestration with local LLM execution |
| **Pattern Applicability** | HIGH -- 6 of 8 GAIA pain points have direct BAIBEL pattern counterparts |
| **Integration Complexity** | MEDIUM -- Language boundary (TypeScript to Python) requires translation, not porting |
| **Estimated ROI** | HIGH -- Addresses critical pain points (God Object, Global Registry, Dual Architecture) |
| **Recommended Approach** | Phased pattern adoption over 4 quarters, not codebase merging |

### 1.2 Pain Point to Pattern Mapping

| # | GAIA Pain Point | Severity | BAIBEL Pattern | Phase | Status |
|---|-----------------|----------|----------------|-------|--------|
| 1 | Monolithic Base Agent (3,000 lines) | CRITICAL | Agent-as-Data (flat config) | Phase 3 | PENDING |
| 2 | Monolithic CLI (6,748 lines) | HIGH | N/A (indirect via decomposition) | Phase 3 | PENDING |
| 3 | Global Mutable Tool Registry | CRITICAL | **Tool Scoping** (per-agent allowlist) | **Phase 0** | **COMPLETE** |
| 4 | Excessive Mixin Composition (10-15 classes) | HIGH | Agent-as-Data (no inheritance) | Phase 3 | PENDING |
| 5 | Dual Architecture (Agent vs Pipeline) | CRITICAL | **Nexus** (unified state layer) | Phase 1 | **Sprint 1 COMPLETE** |
| 6 | Sync/Async Bridging Complexity | MEDIUM | N/A (different runtime model) | N/A | N/A |
| 7 | Security Model Gaps | MEDIUM | **Workspace** (sandboxed boundary) | Phase 2 | IN PROGRESS |
| 8 | Tight Coupling (Agent to AgentSDK) | HIGH | Service Layer Decoupling | Phase 3 | PENDING |

### 1.3 Pattern Adoption Priority Matrix

| Rank | Pattern | Impact | Effort | ROI | Priority |
|------|---------|--------|--------|-----|----------|
| 1 | **Tool Scoping** (per-agent tool access) | HIGH | LOW | VERY HIGH | **P0 -- IMMEDIATE** |
| 2 | **Nexus State Unification** | CRITICAL | MEDIUM | HIGH | P1 -- Q2 2026 |
| 3 | **Workspace Metadata Index** | MEDIUM | LOW | HIGH | P1 -- Q2 2026 |
| 4 | **Chronicle Digest** (token-efficient context) | HIGH | LOW-MEDIUM | HIGH | P1 -- Q2 2026 |
| 5 | **Supervisor Agent** (LLM quality review) | MEDIUM | MEDIUM | MEDIUM | P2 -- Q3 2026 |
| 6 | Agent-as-Data (flat config vs class hierarchy) | HIGH | HIGH | MEDIUM | P2 -- Q3-Q4 2026 |
| 7 | Service Layer Decoupling (Agent to LLM) | HIGH | HIGH | MEDIUM | P3 -- Q4 2026 |
| 8 | Sandboxed Workspace (hard boundary) | MEDIUM | LOW | MEDIUM | P2 -- Q3 2026 |

---

## 2. Complete Pattern Catalog (WHAT)

### 2.1 Phase 0 Patterns

#### 2.1.1 Tool Scoping (Per-Agent Tool Access)

**What It Is:**
A security and isolation pattern that restricts each agent's tool access to an explicit allowlist, preventing cross-contamination between agents with different responsibilities.

**BAIBEL Implementation:**
```typescript
// AgentConfig declares allowed tools
interface AgentConfig {
    allowedTools: string[];  // ['read_file', 'write_file', 'execute_python']
}

// Tool registry is separate from tool access
// geminiService filters tools by agent.allowedTools
```

**GAIA Integration:**
- **Current State:** Global mutable `_TOOL_REGISTRY` dict shared across all agents
- **Target State:** `ToolRegistry` class with `AgentScope` for per-agent filtering
- **Security Enhancement:** Case-sensitive tool name matching (prevents bypass via case variation)

**Classes:**
| Class | Purpose | Methods |
|-------|---------|---------|
| `ToolRegistry` | Singleton registry with thread-safe operations | `register()`, `create_scope()`, `execute_tool()` |
| `AgentScope` | Per-agent scoped view with allowlist filtering | `execute_tool()`, `get_available_tools()`, `has_tool()` |
| `ExceptionRegistry` | Tracks tool execution exceptions | `record()`, `get_exceptions()`, `get_error_rate()` |
| `_ToolRegistryAlias` | Backward-compatible dict shim | Dict interface with deprecation warnings |

---

### 2.2 Phase 1 Patterns

#### 2.2.1 Nexus (Unified State Layer)

**What It Is:**
A centralized blackboard-pattern state manager that serves as the single source of truth for all agent and pipeline state, enabling unified state sharing across GAIA's previously disconnected Agent and Pipeline systems.

**BAIBEL Implementation:**
```typescript
// NexusService singleton (206 lines)
class NexusService {
    state: NexusState = {
        chronicle: ChronicleEntry[],     // Append-only event log
        workspace: WorkspaceFile[],      // Virtual file metadata index
        ether: EtherState,               // Persistent Python REPL state
        consensusReached: boolean,
        activeAgents: string[]
    }

    // Core operations
    commit(agentId, eventType, payload)
    getSnapshot() -> structuredClone(this.state)
    getContextForAgent(agentId) -> CuratedContext
}
```

**GAIA Integration:**
- **Wraps Existing:** `AuditLogger` (910 lines, SHA-256 hash chain)
- **Wraps Existing:** `PipelineStateMachine` (633 lines)
- **Extends To:** Agent system (currently has no event log)
- **Implementation Status:** Phase 1 COMPLETE (763 LOC, 79 tests)

**Sprint 2 Extension - ChronicleDigest:**
- **Extends:** `AuditLogger.get_digest()` method (+230 LOC)
- **Token Budget:** Hierarchical summarization with <4000 token target
- **Features:** Phase filtering, agent filtering, event type filtering
- **Test Coverage:** 59 tests for ChronicleDigest (100% pass)

**Sprint 2 Extension - Agent-Nexus Integration:**
- **Extends:** `Agent` base class (+140 LOC)
- **Features:** `_nexus` connection, `_enable_chronicle` flag, `_commit_chronicle_event()`
- **Auto-Logging:** Tool errors automatically committed to Chronicle
- **Test Coverage:** 43 tests for Agent-Nexus integration (100% pass)

**Sprint 3 Extension - Pipeline-Nexus Integration:**
- **Extends:** `PipelineEngine` class (+100 LOC in engine.py)
- **Features:** 8 event types logged to Chronicle (pipeline_init, phase_enter/exit, agent_selected/executed, quality_evaluated, defect_discovered, decision_made)
- **Loop Tracking:** Events correlated with loop_id for traceability
- **Test Coverage:** 31 tests for Pipeline-Nexus integration (100% pass)

**Classes:**
| Class | Purpose | Integration Point | Status |
|-------|---------|-------------------|--------|
| `NexusService` | Python singleton state service | `src/gaia/state/nexus.py` | **COMPLETE** |
| `WorkspaceIndex` | Workspace metadata tracking | Embedded in `nexus.py` | **COMPLETE** |
| `ChronicleDigest` | Token-efficient context summarization | `src/gaia/pipeline/audit_logger.py` | **COMPLETE** |
| `Agent-Nexus` | Agent event logging to Chronicle | `src/gaia/agents/base/agent.py` | **COMPLETE** |
| `Pipeline-Nexus` | Pipeline event logging to Chronicle | `src/gaia/pipeline/engine.py` | **COMPLETE** |

**Security Achievement (Sprint 1):**
- **TOCTOU Vulnerability Fixed:** Path safety check now runs BEFORE path normalization
- **Unix absolute paths** (`/etc/passwd`) blocked
- **Windows absolute paths** (`C:\Windows`) blocked
- **Parent traversal** (`../`) blocked
- **Thread safety verified:** 100+ concurrent threads tested

#### 2.2.2 Chronicle (Temporal Event Log)

**What It Is:**
An append-only event log that records every significant event across both Agent and Pipeline systems, providing a unified audit trail.

**BAIBEL Implementation:**
```typescript
interface ChronicleEntry {
    id: string;           // UUID
    timestamp: number;    // Unix timestamp
    agentId: string;      // Source agent/pipeline
    eventType: 'THOUGHT' | 'TOOL_CALL' | 'TOOL_RESULT' | 'CONSENSUS' | 'ERROR';
    payload: any;
}

// Key capability: token-efficient digest
getChronicleDigest(maxEvents: number) -> string
```

**GAIA Integration:**
- **Current State:** `AuditLogger` exists but is Pipeline-only
- **Target State:** Extended to Agent system with `get_digest()` method for local model context optimization
- **Implementation Status:** Sprint 2 COMPLETE (+230 LOC in AuditLogger, +140 LOC in Agent)

**Sprint 2 Achievements:**
- `AuditLogger.get_digest()` method with token-efficient summarization
- Agent base class integration with `_commit_chronicle_event()` method
- Error auto-logging for tool execution failures
- 102 tests passing (59 ChronicleDigest + 43 Agent-Nexus)

**Event Types for GAIA:**
| Event Type | Source | Payload |
|------------|--------|---------|
| `THOUGHT` | Agent reasoning | `thought_process`, `summary` |
| `TOOL_CALL` | Tool invocation | `tool_name`, `arguments` |
| `TOOL_RESULT` | Tool execution | `result`, `success` |
| `PHASE_TRANSITION` | Pipeline | `from_phase`, `to_phase`, `quality_score` |
| `CONSENSUS` | Quality gate | `decision`, `feedback` |
| `ERROR` | Any component | `error_type`, `message`, `traceback` |

#### 2.2.3 Workspace (Spatial Metadata)

**What It Is:**
A virtual metadata index tracking all agent-produced artifacts in a sandboxed filesystem, enabling artifact discovery and cross-agent collaboration.

**BAIBEL Implementation:**
```typescript
interface WorkspaceFile {
    path: string;           // Relative to workspace root
    size: number;           // File size in bytes
    lastModified: number;   // Filesystem mtime
    modifiedBy: string;     // Agent ID of last modifier
}

// Dual-layer architecture:
// 1. Physical: shared_workspace/ directory on disk
// 2. Virtual: NexusState.workspace[] metadata index
```

**GAIA Integration:**
- **Current State:** `PathValidator` with opt-in sandboxing
- **Target State:** Mandatory sandboxing with metadata index

**Classes:**
| Class | Purpose | Methods |
|-------|---------|---------|
| `WorkspaceManager` | Sandbox enforcement + metadata index | `validate_path()`, `write_file()`, `get_index()` |
| `WorkspaceFile` | Metadata record | Dataclass: `path`, `size`, `mtime`, `modified_by`, `checksum` |

#### 2.2.4 Context Lens (Token-Efficient Context)

**What It Is:**
A context curation mechanism that provides each agent with a minimal, relevant context snapshot instead of dumping the full history -- critical for local models with 4K-32K token context windows.

**BAIBEL Implementation:**
```typescript
interface CuratedContext {
    agentId: string;
    chronicleDigest: string;     // Compressed summary of recent events
    relevantFiles: WorkspaceFile[];  // Top 5 most recently modified
    recentEvents: ChronicleEntry[];  // Last 3 raw events
}

// Current: tail-based (last N events)
// Future: smart summarization via embeddings/importance scoring
```

**GAIA Integration:**
- **Current State:** Full history dump to every agent
- **Target State:** `get_digest(max_tokens)` method optimized for Ryzen AI hardware

---

### 2.3 Phase 2 Patterns

#### 2.3.1 Supervisor (Quality Gate Agent)

**What It Is:**
A specialized agent that reviews collective agent output after consensus and issues a binary APPROVE/REJECT decision. Rejection triggers full re-iteration.

**BAIBEL Implementation:**
```typescript
// Supervisor configuration
{
    isSupervisor: true,
    allowedTools: ['review_consensus'],
    role: 'Quality Assurance / Gatekeeper'
}

// Review tool
{
    name: 'review_consensus',
    parameters: {
        decision: 'APPROVE' | 'REJECT',
        feedback: string  // Reasoning + improvement instructions
    }
}
```

**GAIA Integration:**
- **Current State:** `QualityScorer` (27,078 lines) -- procedural code-only scoring
- **Target State:** Supervisor agent complements `QualityScorer` with LLM-based qualitative judgment

**Classes:**
| Class | Purpose | Integration Point |
|-------|---------|-------------------|
| `SupervisorDecision` | Structured decision record | New: `src/gaia/quality/supervisor.py` |
| `SupervisorAgent` | Quality gate agent | New: `config/agents/quality-supervisor.yaml` |
| `ReviewConsensusTool` | APPROVE/REJECT tool | New: `src/gaia/tools/review_ops.py` |

**Decision Parsing Strategy:**
1. **Structured Field (Primary):** Check `decision` field for exact `'APPROVE'` or `'REJECT'`
2. **Summary Text Heuristic (Fallback):** Scan `summary` for substring `'APPROVE'`
3. **Default to Reject:** If neither produces affirmative, default to rejection

#### 2.3.2 Workspace Sandboxing (Hard Boundary)

**What It Is:**
Mandatory filesystem sandboxing with path traversal protection, ensuring agents can only write to designated workspace directories.

**BAIBEL Implementation:**
```typescript
function validatePath(inputPath: string): { valid: boolean; fullPath: string } {
    const normalizedInput = inputPath.replace(/\\/g, '/');
    const resolved = path.resolve(SHARED_WORKSPACE, normalizedInput);
    const normalizedWorkspace = path.resolve(SHARED_WORKSPACE).replace(/\\/g, '/') + '/';

    if (!resolved.startsWith(normalizedWorkspace)) {
        return { valid: false, error: 'Path traversal detected' };
    }
    return { valid: true, fullPath: resolved };
}
```

**GAIA Integration:**
- **Current State:** `PathValidator` is opt-in
- **Target State:** Mandatory sandboxing per pipeline execution

---

### 2.4 Phase 3 Patterns

#### 2.4.1 Agent-as-Data (Flat Configuration)

**What It Is:**
Agents are defined as pure data (configuration objects) with behavior injected by an orchestrator, rather than inheriting from deep class hierarchies.

**BAIBEL Implementation:**
```typescript
// AgentConfig is pure data (no inheritance)
interface AgentConfig {
    id: string;
    name: string;
    role: string;
    systemPrompt: string;
    model: string;
    allowedTools: string[];
    knowledgeBase?: string[];
    loopId: string;
    isSupervisor?: boolean;
}

// Behavior injected by App.tsx orchestrator
// No class inheritance
```

**GAIA Integration:**
- **Current State:** `Agent` class (3,000 lines) with 10-15 mixin depth
- **Target State:** `AgentProfile` dataclass + `AgentExecutor` injection

**Classes:**
| Class | Purpose | Methods |
|-------|---------|---------|
| `AgentProfile` | Pure-data agent configuration | Dataclass: `id`, `tools`, `system_prompt`, `model`, `capabilities` |
| `AgentExecutor` | Behavior injection engine | `run_step()`, `execute_tool()`, `format_context()` |
| `AgentAdapter` | Backward-compatible wrapper | Wraps legacy `Agent` class |

#### 2.4.2 Service Layer Decoupling

**What It Is:**
Decoupling the Agent from LLM client internals, accepting agent profile + context + tools as parameters rather than tight coupling to `AgentSDK`.

**BAIBEL Implementation:**
```typescript
// geminiService accepts parameters, not base class
async function runAgentStep(
    agent: AgentConfig,  // Pure data, not a class instance
    topic: string,
    context: CuratedContext,
    tools: ExecutableTool[]
): Promise<AgentOutput>
```

**GAIA Integration:**
- **Current State:** `Agent.chat = AgentSDK(config)` -- tight coupling
- **Target State:** Stateless LLM bridge accepting parameters

**Classes:**
| Class | Purpose | Integration Point |
|-------|---------|-------------------|
| `LLMAgentBridge` | Stateless LLM interaction | New: `src/gaia/llm/agent_bridge.py` |
| `AgentSDK` | Made independently usable | Refactor: remove `Agent` dependency |

#### 2.4.3 Consensus Orchestrator (Unified Loop)

**What It Is:**
A unified orchestration mechanism that combines Agent and Pipeline execution under a single loop with iterative consensus and quality gates.

**BAIBEL Implementation:**
```typescript
async function runSimulation(): Promise<SimulationResult> {
    for (iteration in range(MAX_ITERATIONS)) {
        // Phase 1: Run agents
        for (agent in agents) {
            context = nexusService.getContextForAgent(agent.id)
            result = await geminiService.runAgentStep(agent, context)
            nexusService.commit(agent.id, 'THOUGHT', result)
        }

        // Phase 2: Check agreement
        if (!allAgentsAgree()) continue

        // Phase 3: Supervisor review
        if (supervisorExists) {
            decision = await supervisor.review()
            if (decision === 'REJECT') continue
        }

        // Phase 4: Finalize
        return { status: 'approved', outputs }
    }
}
```

**GAIA Integration:**
- **Current State:** Separate Agent loop and Pipeline phases
- **Target State:** `ConsensusOrchestrator` wrapping both

**Classes:**
| Class | Purpose | Methods |
|-------|---------|---------|
| `ConsensusOrchestrator` | Unified execution loop | `run()`, `check_agreement()`, `run_supervisor()` |
| `ConsensusResult` | Structured outcome | Dataclass: `status`, `outputs`, `iterations` |

---

## 3. Integration Map (WHERE)

### 3.1 File Modification Matrix

| Phase | File | Changes | Priority |
|-------|------|---------|----------|
| **Phase 0** | `src/gaia/agents/base/tools.py` | Complete rewrite: ToolRegistry, AgentScope, ExceptionRegistry | P0 |
| **Phase 0** | `src/gaia/agents/base/agent.py` | Add `allowed_tools` param, create tool scope | P0 |
| **Phase 0** | `src/gaia/agents/configurable.py` | Use YAML `tools:` as allowlist | P0 |
| **Phase 1** | `src/gaia/state/nexus.py` | NEW: Nexus singleton service | P1 |
| **Phase 1** | `src/gaia/state/workspace.py` | NEW: Workspace metadata index | P1 |
| **Phase 1** | `src/gaia/pipeline/audit_logger.py` | Add `get_digest()` method | P1 |
| **Phase 1** | `src/gaia/agents/base/agent.py` | Commit events to Chronicle | P1 |
| **Phase 2** | `config/agents/quality-supervisor.yaml` | NEW: Supervisor agent definition | P2 |
| **Phase 2** | `src/gaia/tools/review_ops.py` | NEW: `review_consensus` tool | P2 |
| **Phase 2** | `src/gaia/pipeline/engine.py` | Invoke Supervisor after QUALITY phase | P2 |
| **Phase 2** | `src/gaia/security.py` | Mandatory `PathValidator` | P2 |
| **Phase 3** | `src/gaia/agents/config.py` | NEW: `AgentProfile` dataclass | P3 |
| **Phase 3** | `src/gaia/agents/executor.py` | NEW: `AgentExecutor` engine | P3 |
| **Phase 3** | `src/gaia/agents/base/agent.py` | Reduce from 3000 to <500 lines | P3 |
| **Phase 3** | `src/gaia/llm/agent_bridge.py` | NEW: Stateless LLM bridge | P3 |
| **Phase 3** | `src/gaia/chat/sdk.py` | Remove `Agent` dependency | P3 |

### 3.2 Component Dependency Graph

```
                           Phase 0
                    +--------------------+
                    |   ToolRegistry     |
                    |   (tools.py)       |
                    +---------+----------+
                              |
              +---------------+---------------+
              |                               |
         +----v----+                    +-----v-----+
         |  Agent  |                    |Configurable|
         |  Base   |                    |   Agent    |
         +---------+                    +------------+
              |                               |
              +---------------+---------------+
                              |
                           Phase 1
                    +--------------------+
                    |    NexusService    |
                    |    (state/)        |
                    +---------+----------+
                              |
              +---------------+---------------+
              |               |               |
         +----v----+    +-----v-----+    +----v----+
         |Chronicle|    | Workspace |    | Context |
         | (audit) |    |  (files)  |    |  Lens   |
         +---------+    +-----------+    +---------+
              |               |               |
              +---------------+---------------+
                              |
                           Phase 2
                    +--------------------+
                    |   SupervisorAgent  |
                    |    (quality/)      |
                    +---------+----------+
                              |
              +---------------+---------------+
              |                               |
         +----v----+                    +-----v-----+
         | Review  |                    | Workspace |
         |  Tool   |                    | Sandbox   |
         +---------+                    +-----------+
                              |
                           Phase 3
                    +--------------------+
                    |  ConsensusOrchestrator|
                    |    (orchestrator/) |
                    +---------+----------+
                              |
              +---------------+---------------+
              |               |               |
         +----v----+    +-----v-----+    +----v----+
         |  Agent  |    |   LLM     |    | Pipeline|
         | Profiles|    |  Bridge   |    | Engine  |
         +---------+    +-----------+    +---------+
```

---

## 4. Implementation Strategy (HOW)

### 4.1 Phase 0: Tool Scoping (2 weeks)

**Objective:** Eliminate agent cross-contamination by enforcing per-agent tool access.

**Implementation Approach:**

```python
# Step 1: Create ToolRegistry class (tools.py)
class ToolRegistry:
    """Thread-safe singleton registry for agent tools."""

    _instance: Optional["ToolRegistry"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ToolRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._registry_lock = threading.RLock()
        self._exception_registry = ExceptionRegistry()
        self._initialized = True

    def register(self, name: str, func: Callable, description: str = None) -> None:
        """Register a tool function with thread safety."""
        with self._registry_lock:
            self._tools[name] = {
                "function": func,
                "description": description or func.__doc__ or "",
                "parameters": inspect.signature(func).parameters,
            }

    def create_scope(self, agent_id: str, allowed_tools: Optional[List[str]] = None) -> "AgentScope":
        """Create scoped view for specific agent."""
        return AgentScope(self, agent_id, allowed_tools)

# Step 2: Create AgentScope class
class AgentScope:
    """Scoped view of ToolRegistry for specific agent."""

    def __init__(self, registry: "ToolRegistry", agent_id: str, allowed_tools: Optional[List[str]] = None):
        self._registry = registry
        self._agent_id = agent_id
        self._allowed_tools: Optional[Set[str]] = set(allowed_tools) if allowed_tools else None
        self._lock = threading.RLock()

    def execute_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """Execute tool if accessible, raise ToolAccessDeniedError otherwise."""
        with self._lock:
            if not self._is_tool_allowed(tool_name):
                raise ToolAccessDeniedError(tool_name=tool_name, agent_id=self._agent_id)
            return self._registry.execute_tool(tool_name, *args, **kwargs)

    def _is_tool_allowed(self, tool_name: str) -> bool:
        """Check if tool is accessible (case-sensitive, exact match)."""
        if self._allowed_tools is None:
            return True
        return tool_name in self._allowed_tools  # Case-sensitive!

# Step 3: Create backward compatibility shim
class _ToolRegistryAlias(dict):
    """Backward-compatible dict shim with deprecation warnings."""

    def __init__(self):
        self._registry = ToolRegistry.get_instance()

    def __getitem__(self, key):
        warnings.warn(
            "Direct access to _TOOL_REGISTRY is deprecated. Use ToolRegistry.get_instance() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        return self._registry.get_all_tools()[key]

# Maintain backward compatibility
_TOOL_REGISTRY = _ToolRegistryAlias()
```

**Integration in Agent classes:**

```python
# In Agent.__init__():
from gaia.agents.base.tools import ToolRegistry

class Agent:
    def __init__(
        self,
        agent_id: str = None,
        model_id: str = None,
        allowed_tools: List[str] = None,  # NEW parameter
        **kwargs
    ):
        # ... existing initialization ...

        # NEW: Create tool scope after tool registration
        self._tool_scope = ToolRegistry.get_instance().create_scope(
            agent_id=self.__class__.__name__,
            allowed_tools=allowed_tools or getattr(self, "allowed_tools", None),
        )

    def _execute_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """Execute tool through scoped view."""
        return self._tool_scope.execute_tool(tool_name, *args, **kwargs)
```

**Exit Criteria:**
- [ ] BC-001: Backward compatibility tests pass (100%)
- [ ] SEC-001: Allowlist bypass tests fail (0% success rate)
- [ ] PERF-001: Performance overhead <5%
- [ ] MEM-001: Zero memory leaks (0% threshold)

---

### 4.2 Phase 1: State Unification (8 weeks)

**Objective:** Create a shared state service (inspired by Nexus) that unifies Agent and Pipeline state management.

**Implementation Approach:**

```python
# src/gaia/state/nexus.py
class NexusService:
    """Python singleton state service wrapping AuditLogger."""

    _instance: Optional["NexusService"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "NexusService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        from gaia.pipeline.audit_logger import AuditLogger
        self._audit_logger = AuditLogger.get_instance()
        self._workspace = WorkspaceIndex()
        self._lock = threading.RLock()
        self._initialized = True

    def commit(self, agent_id: str, event_type: str, payload: dict) -> None:
        """Commit event to Chronicle (via AuditLogger)."""
        with self._lock:
            entry = {
                "id": str(uuid.uuid4()),
                "timestamp": time.time(),
                "agent_id": agent_id,
                "event_type": event_type,
                "payload": payload,
            }
            self._audit_logger.log_event(entry)

    def get_digest(self, max_events: int = 15) -> str:
        """Generate token-efficient summary of recent events."""
        return self._audit_logger.get_digest(max_events)

    def get_context_for_agent(self, agent_id: str) -> dict:
        """Curate context for specific agent."""
        return {
            "chronicle_digest": self.get_digest(15),
            "relevant_files": self._workspace.get_recent(5),
            "recent_events": self._audit_logger.get_recent(3),
        }

    def get_snapshot(self) -> dict:
        """Return deep copy of state (mutation-safe)."""
        import copy
        return copy.deepcopy({
            "chronicle": self._audit_logger.get_events(),
            "workspace": self._workspace.get_index(),
        })


# src/gaia/state/workspace.py
class WorkspaceIndex:
    """Metadata index for agent-produced artifacts."""

    def __init__(self):
        self._root = Path("./workspace")
        self._index: Dict[str, WorkspaceFile] = {}
        self._lock = threading.RLock()

    def validate_path(self, relative_path: str) -> Path:
        """Resolve and validate path is within workspace."""
        resolved = (self._root / relative_path).resolve()
        if not str(resolved).startswith(str(self._root.resolve())):
            raise SecurityError("Path traversal detected")
        return resolved

    def write_file(self, relative_path: str, content: bytes, modified_by: str) -> WorkspaceFile:
        """Write file, update index, commit to chronicle."""
        with self._lock:
            full_path = self.validate_path(relative_path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(content)

            # Update index
            stat = full_path.stat()
            file_entry = WorkspaceFile(
                path=relative_path,
                size=stat.st_size,
                last_modified=stat.st_mtime,
                modified_by=modified_by,
                checksum=hashlib.sha256(content).hexdigest(),
            )
            self._index[relative_path] = file_entry
            return file_entry

    def get_index(self) -> List[WorkspaceFile]:
        """Return current file metadata index."""
        with self._lock:
            return list(self._index.values())

    def get_recent(self, limit: int = 5) -> List[WorkspaceFile]:
        """Return most recently modified files."""
        with self._lock:
            sorted_files = sorted(
                self._index.values(),
                key=lambda f: f.last_modified,
                reverse=True
            )
            return sorted_files[:limit]
```

**Exit Criteria:**
- [ ] Agent and Pipeline systems share single state instance
- [ ] Context digest fits within 4K token window
- [ ] No regression in existing Pipeline test suite
- [ ] AuditLogger hash chain integrity preserved

---

### 4.3 Phase 2: Quality Enhancement (6 weeks)

**Objective:** Add LLM-based quality review (Supervisor pattern) alongside existing automated scoring.

**Implementation Approach:**

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


# src/gaia/tools/review_ops.py
@tool
def review_consensus(
    decision: Literal["APPROVE", "REJECT"],
    feedback: str,
    quality_score_override: Optional[float] = None
) -> str:
    """
    Approve or Reject the current consensus reached by the team.

    Args:
        decision: Binary APPROVE or REJECT decision
        feedback: Detailed reasoning; improvement instructions on REJECT
        quality_score_override: Optional override of automated quality score

    Returns:
        Confirmation message
    """
    result = {
        "decision": decision,
        "feedback": feedback,
        "timestamp": time.time(),
    }
    if quality_score_override is not None:
        result["quality_score_override"] = quality_score_override

    return json.dumps(result, indent=2)
```

**Integration in Pipeline Engine:**

```python
# In PipelineEngine._execute_phase() for QUALITY phase:
async def _execute_quality_phase(self, context: PipelineContext) -> PhaseResult:
    # Run automated quality scoring
    quality_result = await self._run_quality_scorers(context)

    # Optionally invoke Supervisor agent
    if self._supervisor_enabled:
        supervisor_result = await self._invoke_supervisor(context, quality_result)

        if supervisor_result.decision == "REJECT":
            # Trigger LOOP_BACK with feedback
            return PhaseResult(
                status=PhaseStatus.LOOP_BACK,
                reason=f"Supervisor rejected: {supervisor_result.feedback}",
                feedback=supervisor_result.feedback,
            )

    return PhaseResult(
        status=PhaseStatus.PASSED if quality_result.passed else PhaseStatus.FAILED,
        quality_score=quality_result.score,
    )
```

**Exit Criteria:**
- [ ] Supervisor catches defects that automated scorer misses
- [ ] Pipeline LOOP_BACK rate improves (fewer unnecessary iterations)
- [ ] Workspace isolation prevents cross-pipeline contamination

---

### 4.4 Phase 3: Architectural Modernization (12 weeks)

**Objective:** Address deep structural issues -- mixin explosion and tight coupling -- using BAIBEL's agent-as-data and service-layer patterns.

**Implementation Approach:**

```python
# src/gaia/agents/config.py
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

@dataclass
class AgentProfile:
    """Pure-data agent configuration."""

    id: str
    name: str
    role: str
    system_prompt: str
    model: str
    tools: List[str] = field(default_factory=list)
    capabilities: Dict[str, Any] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    knowledge_base: Optional[List[str]] = None
    allowed_tools: Optional[List[str]] = None


# src/gaia/agents/executor.py
class AgentExecutor:
    """Agent execution engine -- behavior injected, not inherited."""

    def __init__(
        self,
        profile: AgentProfile,
        state_service: NexusService,
        llm_client: Any,
    ):
        self.profile = profile
        self.state_service = state_service
        self.llm_client = llm_client
        self._tool_scope = None

    async def run_step(self, topic: str, context: dict) -> dict:
        """Run single agent step with injected behavior."""
        # 1. Get curated context from state service
        curated = self.state_service.get_context_for_agent(self.profile.id)

        # 2. Build system prompt from profile
        system_prompt = self._build_system_prompt()

        # 3. Invoke LLM
        response = await self.llm_client.chat(
            system_prompt=system_prompt,
            user_message=f"Topic: {topic}\nContext: {curated}",
            tools=self._get_available_tools(),
        )

        # 4. Execute tool calls if any
        if response.tool_calls:
            results = await self._execute_tools(response.tool_calls)
            response.tool_results = results

        # 5. Commit to Chronicle
        self.state_service.commit(
            agent_id=self.profile.id,
            event_type="THOUGHT",
            payload=response.dict(),
        )

        return response.dict()


# src/gaia/llm/agent_bridge.py
class LLMAgentBridge:
    """Stateless LLM interaction layer."""

    async def invoke(
        self,
        profile: AgentProfile,
        context: dict,
        tools: List[dict],
        history: List[dict],
    ) -> dict:
        """
        Invoke LLM with agent profile as parameters.

        No side effects. No class dependencies.
        Accepts pure data, returns structured response.
        """
        # 1. Build messages from profile + context
        messages = self._build_messages(profile, context, history)

        # 2. Route to appropriate LLM provider
        if is_local_model(profile.model):
            return await self._invoke_lemonade(profile.model, messages, tools)
        else:
            return await self._invoke_cloud(profile.model, messages, tools)
```

**Exit Criteria:**
- [ ] Base Agent class reduced from 3,000 lines to under 500
- [ ] Mixin depth reduced from 10-15 classes to 2-3
- [ ] No regression in any agent's behavior
- [ ] External API surface backward-compatible via adapter layer
- [ ] New agent creation requires only YAML definition + tool functions

---

## 5. 4-Phase Integration Roadmap

### Phase 0: Tool Scoping (2 weeks) - COMPLETE

| Week | Task | Owner | Deliverables | Status |
|------|------|-------|--------------|--------|
| 1 | ToolRegistry implementation | senior-developer | `ToolRegistry`, `AgentScope`, `ExceptionRegistry` classes | **COMPLETE** |
| 1 | Unit tests | testing-quality-specialist | `test_tool_registry.py`, `test_backward_compat_shim.py` | **COMPLETE** |
| 2 | Agent integration | senior-developer | `Agent._tool_scope`, `ConfigurableAgent` updates | **COMPLETE** |
| 2 | Security tests | testing-quality-specialist | `test_tool_isolation.py`, `test_allowlist_bypass.py` | **COMPLETE** |
| 2 | Quality Gate 1 | quality-reviewer | BC-001, SEC-001, PERF-001, MEM-001 validation | **PASSED** |

**Exit Criteria:**
- [x] BC-001: Backward compatibility tests pass (100%)
- [x] SEC-001: Allowlist bypass tests fail (0% success rate)
- [x] PERF-001: Performance overhead <5%
- [x] MEM-001: Zero memory leaks (0% threshold)

**Deliverables:**
- `src/gaia/agents/base/tools.py`: 884 LOC (ToolRegistry, AgentScope, ExceptionRegistry)
- 204 test functions (100% pass rate)
- Quality Gate 1: ALL CRITERIA PASSED

---

### Phase 1: State Unification (8 weeks) - ALL SPRINTS COMPLETE

| Sprint | Weeks | Tasks | Deliverables | Status |
|--------|-------|-------|--------------|--------|
| Sprint 1 | 1-2 | NexusService Core | `NexusService` (763 LOC), `WorkspaceIndex` (embedded) | **COMPLETE** |
| Sprint 1 | 1-2 | Unit Tests | 79 tests (100% pass), TOCTOU fix | **COMPLETE** |
| Sprint 2 | 3-4 | ChronicleDigest & Agent Integration | `AuditLogger.get_digest()`, Agent-Nexus wiring | **COMPLETE** |
| Sprint 3 | 5-6 | ChronicleDigest Extension | +230 LOC in AuditLogger, 59 tests | **COMPLETE** |
| Sprint 3 | 5-6 | Agent-Nexus Integration | +140 LOC in Agent, 43 tests | **COMPLETE** |
| Sprint 4 | 7-8 | Pipeline-Nexus Integration | +100 LOC in engine.py, 31 tests | **COMPLETE** |
| Sprint 4 | 7-8 | Quality Gate 2 | 5/7 criteria complete, 2 partial | **CONDITIONAL PASS** |

**Sprint 1 Completed Deliverables:**
- [x] `src/gaia/state/nexus.py` -- Python singleton state service (763 LOC)
- [x] `WorkspaceIndex` -- Workspace metadata index (embedded in nexus.py)
- [x] 79 test functions in `tests/unit/state/` (100% pass rate)
- [x] **TOCTOU Security Fix**: Path safety check BEFORE normalization

**Sprint 2 & 3 Completed Deliverables:**
- [x] `AuditLogger.get_digest()` -- Token-efficient event summarization (+230 LOC)
- [x] `Agent.__init__()` -- Nexus integration with `_enable_chronicle` flag (+140 LOC)
- [x] `_commit_chronicle_event()` -- Event commitment method
- [x] `_summarize_for_chronicle()` -- Data truncation for Chronicle
- [x] Error auto-logging in tool exception handlers
- [x] 102 test functions (59 ChronicleDigest + 43 Agent-Nexus, 100% pass)

**Sprint 4 Completed Deliverables:**
- [x] `PipelineEngine` Nexus integration (+100 LOC in engine.py)
- [x] 8 event types logged: pipeline_init, phase_enter/exit, agent_selected/executed, quality_evaluated, defect_discovered, decision_made
- [x] Loop tracking with loop_id for event correlation
- [x] 31 test functions in `test_pipeline_nexus_integration.py` (100% pass)
- [x] Thread safety verified (100+ concurrent threads, 1000 commits)
- [x] Quality Gate 2 assessment (CONDITIONAL PASS)

**Exit Criteria (Final):**
- [x] NexusService singleton implemented (763 LOC, 79 tests)
- [x] WorkspaceIndex with TOCTOU fix (path check BEFORE normalization)
- [x] ChronicleDigest with token-efficient summarization (+230 LOC, 59 tests)
- [x] Agent-Nexus integration for event logging (+140 LOC, 43 tests)
- [x] Pipeline-Nexus integration for full observability (+100 LOC, 31 tests)
- [x] Context digest with hierarchical budget enforcement
- [x] No regression in existing Pipeline test suite
- [x] AuditLogger hash chain integrity preserved
- [x] Thread safety verified (100+ concurrent threads)

---

### Phase 2: Quality Enhancement (8 weeks) - COMPLETE

| Sprint | Weeks | Tasks | Deliverables | Status |
|--------|-------|-------|--------------|--------|
| Sprint 1 | 1-2 | Supervisor Agent Core | `SupervisorAgent` (848 LOC), `review_ops.py` (526 LOC) | **COMPLETE** |
| Sprint 1 | 1-2 | Unit Tests | 41 unit tests, 18 integration tests | **COMPLETE** |
| Sprint 1 | 1-2 | Quality Gate 2 | 3/3 criteria PASS | **COMPLETE** |
| Sprint 2 | 3-6 | Context Lens Optimization | TokenCounter (336 LOC), ContextLens (569 LOC), EmbeddingRelevance (443 LOC) | **COMPLETE** |
| Sprint 2 | 3-6 | Unit Tests | 117 tests (100% pass, 2 skipped) | **COMPLETE** |
| Sprint 2 | 3-6 | Quality Gate 2 | 6/6 criteria PASS | **COMPLETE** |
| Sprint 3 | 7-8 | Workspace Sandboxing | WorkspacePolicy (667 LOC), SecurityValidator (503 LOC), PipelineIsolation (541 LOC) | **COMPLETE** |
| Sprint 3 | 7-8 | Unit Tests | 98 tests (100% pass) | **COMPLETE** |
| Sprint 3 | 7-8 | Quality Gate 3 | 6/6 criteria PASS | **COMPLETE** |

**Phase 2 Sprint 1 Deliverables:**
- [x] `src/gaia/quality/supervisor.py` -- SupervisorAgent quality review orchestration (848 LOC)
- [x] `src/gaia/tools/review_ops.py` -- Review consensus tools (526 LOC)
- [x] `config/agents/quality-supervisor.yaml` -- Supervisor agent configuration (71 lines)
- [x] 59 test functions (41 unit + 18 integration, 100% pass rate)
- [x] Quality Gate 2: ALL 3 CRITERIA PASSED

**Phase 2 Sprint 2 Deliverables (COMPLETE):**
- [x] TokenCounter with tiktoken integration (AI-002) -- `src/gaia/state/token_counter.py` (336 LOC)
- [x] ContextLens with relevance-based prioritization -- `src/gaia/state/context_lens.py` (569 LOC)
- [x] EmbeddingRelevance with semantic similarity -- `src/gaia/state/relevance.py` (443 LOC)
- [x] NexusService extension with optimized context -- `src/gaia/state/nexus.py` (+114 LOC)
- [x] Performance benchmarks for digest latency (<50ms target)
- [x] 117 test functions (100% pass rate, 2 skipped for GPU dependency)
- [x] Quality Gate 2: ALL 6 CRITERIA PASSED

**Phase 2 Sprint 3 Deliverables (COMPLETE):**
- [x] WorkspacePolicy with hard filesystem boundaries -- `src/gaia/security/workspace.py` (667 LOC)
- [x] SecurityValidator with audit logging -- `src/gaia/security/validator.py` (503 LOC)
- [x] PipelineIsolation with cross-pipeline isolation -- `src/gaia/pipeline/isolation.py` (541 LOC)
- [x] NexusService extension with workspace integration -- `src/gaia/state/nexus.py` (+80 LOC)
- [x] Performance benchmarks for security overhead (<5% target)
- [x] 98 test functions (100% pass rate)
- [x] Quality Gate 3: ALL 6 CRITERIA PASSED

**Detailed Tasks:**

**Sprint 1 (Weeks 1-2):**
- [x] Create `src/gaia/quality/supervisor.py` -- SupervisorAgent implementation (848 LOC)
- [x] Create `src/gaia/tools/review_ops.py` -- `review_consensus` tool (526 LOC)
- [x] Create `config/agents/quality-supervisor.yaml` -- Supervisor agent definition (71 lines)
- [x] Update `PipelineEngine` to incorporate Supervisor decisions in LOOP_BACK routing
- [x] Unit test suite: 41 tests for SupervisorAgent (100% pass)
- [x] Integration test suite: 18 tests for pipeline integration (100% pass)
- [x] Quality Gate 2 assessment: ALL 3 CRITERIA PASSED

**Sprint 2 (Weeks 3-6) - COMPLETE:**
- [x] TokenCounter with tiktoken integration -- Accurate token counting (>95% accuracy)
- [x] ContextLens with embedding-based relevance scoring -- Semantic prioritization
- [x] Performance benchmarks for digest latency (target: <50ms) -- PASS
- [x] Integration tests -- 117 tests (100% pass)
- [x] Quality Gate 2 validation -- 6/6 criteria PASS

**Sprint 3 (Weeks 7-8) - COMPLETE:**
- [x] WorkspacePolicy with hard filesystem boundaries -- 0% bypass rate
- [x] SecurityValidator with path traversal prevention -- 0% success rate
- [x] PipelineIsolation with cross-pipeline isolation -- 100% isolation
- [x] NexusService extension with workspace tracking -- +80 LOC
- [x] Performance benchmarks -- <1% overhead (target: <5%)
- [x] Integration tests -- 98 tests (100% pass)
- [x] Quality Gate 3 validation -- 6/6 criteria PASS

**Exit Criteria:**
- [x] Supervisor catches defects that automated `QualityScorer` misses
- [x] Pipeline LOOP_BACK rate improves (fewer unnecessary iterations)
- [x] Workspace isolation prevents cross-pipeline file contamination

---

### Phase 3: Architectural Modernization (12 weeks)

| Sprint | Weeks | Tasks | Deliverables | Status |
|--------|-------|-------|--------------|--------|
| **Sprint 1** | **1-3** | **Modular Architecture Core** | **AgentCapabilities, AgentProfile, AgentExecutor, PluginRegistry** | **COMPLETE** |
| **Sprint 2** | **4-6** | **DI + Performance** | **DIContainer, AgentAdapter, AsyncUtils, ConnectionPool** | **COMPLETE** |
| Sprint 3 | 7-9 | Caching + Enterprise Config | CacheLayer, ConfigSchema, ConfigManager, SecretsManager | PENDING |
| Sprint 4 | 10-12 | Observability + API | ObservabilityCore, OpenAPISpec, APIVersioning | PENDING |

**Detailed Tasks:**

**Sprint 1 (Weeks 1-3) - COMPLETE:**
- [x] Create `src/gaia/core/capabilities.py` -- AgentCapabilities with validation (340 LOC, 77 tests)
- [x] Create `src/gaia/core/profile.py` -- AgentProfile with spec-aligned fields (360 LOC, 77 tests)
- [x] Create `src/gaia/core/executor.py` -- AgentExecutor with behavior injection (650 LOC, 51 tests)
- [x] Create `src/gaia/core/plugin.py` -- PluginRegistry with lazy loading (680 LOC, 60+ tests)
- [x] Create `src/gaia/core/__init__.py` -- Clean public API export (80 LOC)
- [x] Unit test suite -- 195 tests (100% pass rate)
- [x] Quality Gate 4 -- ALL 5 CRITERIA PASSED (after fixes)

**Sprint 2 (Weeks 4-6) - COMPLETE:**
- [x] Create `src/gaia/core/di_container.py` -- DIContainer for dependency injection (770 LOC, 37 tests)
- [x] Create `src/gaia/core/adapter.py` -- AgentAdapter for backward compatibility (545 LOC, 50 tests)
- [x] Create `src/gaia/perf/async_utils.py` -- AsyncUtils for async patterns (703 LOC, 30 tests)
- [x] Create `src/gaia/perf/connection_pool.py` -- ConnectionPool for LLM connections (787 LOC, 40 tests)
- [x] Create `src/gaia/perf/__init__.py` -- Clean public API export (~50 LOC)
- [x] Unit test suite -- 157 tests (100% pass rate)
- [x] Quality Gate 4 -- ALL 6 CRITERIA PASSED

**Sprint 3 (Weeks 7-9) - PENDING:**
- [ ] Create `src/gaia/perf/cache_layer.py` -- CacheLayer for response caching (~400 LOC)
- [ ] Create `src/gaia/config/config_schema.py` -- ConfigSchema for validation (~300 LOC)
- [ ] Create `src/gaia/config/config_manager.py` -- ConfigManager for lifecycle (~400 LOC)
- [ ] Create `src/gaia/config/secrets_manager.py` -- SecretsManager for secure config (~350 LOC)

**Sprint 4 (Weeks 10-12) - PENDING:**
- [ ] Create `src/gaia/observability/observability_core.py` -- ObservabilityCore (~500 LOC)
- [ ] Create `src/gaia/api/openapi_spec.py` -- OpenAPISpec for API documentation (~400 LOC)
- [ ] Create `src/gaia/api/api_versioning.py` -- APIVersioning for version management (~200 LOC)
- [ ] Create `src/gaia/api/deprecation_layer.py` -- DeprecationLayer for migration (~150 LOC)

**Exit Criteria (Sprint 1 - COMPLETE):**
- [x] AgentCapabilities with validation (77 tests, 100% pass)
- [x] AgentProfile with spec-aligned fields (id, role) (77 tests, 100% pass)
- [x] AgentExecutor with behavior injection (51 tests, 100% pass)
- [x] PluginRegistry with <1ms lookup (60+ tests, 100% pass)
- [x] Thread safety verified (100+ concurrent threads)
- [x] Backward compatibility maintained
- [x] Quality Gate 4: PASS (all issues remediated)

**Exit Criteria (Sprint 2 - COMPLETE):**
- [x] DIContainer with singleton/multiton support (37 tests, 100% pass)
- [x] AgentAdapter for legacy Agent compatibility (50 tests, 100% pass)
- [x] AsyncUtils for async execution patterns (30 tests, 100% pass)
- [x] ConnectionPool for LLM connection management (40 tests, 100% pass)
- [x] 157 tests passing (100% pass rate)
- [x] Quality Gate 4: PASS (6/6 criteria)

**Exit Criteria (Sprint 3 - Target):**
- [ ] CacheLayer with TTL and max size limits
- [ ] ConfigSchema with Pydantic validation
- [ ] ConfigManager with hot reload support
- [ ] SecretsManager with AES-256 encryption
- [ ] 180+ tests passing
- [ ] Quality Gate 5 validation

---

## 6. Test Strategy

### 6.1 Test Categories (4-Day Execution per Phase)

| Phase | Day | Category | Files | Functions | Priority |
|-------|-----|----------|-------|-----------|----------|
| **Phase 0** | Day 1 | Unit Tests | `test_tool_registry.py`, `test_backward_compat_shim.py` | 45 | CRITICAL |
| **Phase 0** | Day 2 | Integration Tests | `test_tool_scoping_integration.py` | 18 | HIGH |
| **Phase 0** | Day 3 | Performance Tests | `test_tool_registry_perf.py` | 8 | MEDIUM |
| **Phase 0** | Day 4 | Security Tests | `test_tool_isolation.py`, `test_allowlist_bypass.py` | 12 | CRITICAL |
| **Phase 1** | Day 1 | Unit Tests | `test_nexus_service.py`, `test_workspace_index.py` | 35 | CRITICAL |
| **Phase 1** | Day 2 | Integration Tests | `test_chronicle_integration.py` | 20 | HIGH |
| **Phase 1** | Day 3 | Performance Tests | `test_digest_generation.py` | 6 | MEDIUM |
| **Phase 1** | Day 4 | State Consistency | `test_state_consistency.py` | 10 | CRITICAL |
| **Phase 2** | Day 1 | Unit Tests | `test_supervisor_agent.py`, `test_review_tool.py` | 25 | CRITICAL |
| **Phase 2** | Day 2 | Integration Tests | `test_supervisor_pipeline_integration.py` | 15 | HIGH |
| **Phase 2** | Day 3 | Quality Tests | `test_quality_improvement.py` | 8 | MEDIUM |
| **Phase 2** | Day 4 | Security Tests | `test_workspace_sandbox.py` | 10 | CRITICAL |
| **Phase 3** | Day 1-2 | Regression Tests | All existing agent tests | 100+ | CRITICAL |
| **Phase 3** | Day 3-4 | Integration Tests | New agent executor tests | 50+ | CRITICAL |

### 6.2 Key Test Functions (Phase 0 Example)

```python
# test_tool_registry.py
def test_singleton_thread_safety():
    """Verify singleton pattern is thread-safe."""
    # 100 threads, concurrent get_instance() calls
    # Expected: All threads receive same instance

def test_allowlist_filtering():
    """Verify AgentScope correctly filters tools."""
    # Create scope with allowed_tools=["file_read", "file_write"]
    # Execute "file_read" → Success
    # Execute "bash_execute" → ToolAccessDeniedError

def test_backward_compat_shim():
    """Verify _TOOL_REGISTRY shim maintains compatibility."""
    # Access via dict interface, expect DeprecationWarning
    # Verify operations forward to ToolRegistry

# test_tool_isolation.py
def test_case_sensitive_matching():
    """Verify case-sensitive tool name matching prevents bypass."""
    # Scope with allowed_tools=["file_read"]
    # Execute "File_Read" → ToolAccessDeniedError (case mismatch)
    # Execute "FILE_READ" → ToolAccessDeniedError (case mismatch)
    # Execute "file_read" → Success

def test_allowlist_bypass_attempts():
    """Verify allowlist cannot be bypassed via injection."""
    # Attempt tool name injection via special characters
    # Expected: All bypass attempts fail
```

### 6.3 Performance Thresholds

| Metric | Phase 0 | Phase 1 | Phase 2 | Phase 3 |
|--------|---------|---------|---------|---------|
| Registry overhead | <5% | N/A | N/A | N/A |
| Scope creation | <1ms | N/A | N/A | N/A |
| Memory footprint | <100KB/scope | <1MB/service | <500KB | <200KB/profile |
| Memory leaks | 0% | 0% | 0% | 0% |
| Digest generation | N/A | <50ms | N/A | N/A |
| Context curation | N/A | <10ms | N/A | N/A |
| Supervisor latency | N/A | N/A | <2s | N/A |
| Executor overhead | N/A | N/A | N/A | <10% |

---

## 7. Risk Register

| ID | Risk | Probability | Impact | Exposure | Mitigation Strategy | Phase | Status |
|----|------|------------|--------|----------|---------------------|-------|--------|
| R1 | **Language Translation Fidelity** -- BAIBEL patterns designed for TypeScript/Node.js may not translate cleanly to Python | MEDIUM | HIGH | HIGH | Create Python-native implementations inspired by patterns, not direct ports. Validate with prototypes before committing. | All | MONITORED |
| R2 | **Performance Degradation** -- State service snapshot (`copy.deepcopy`) adds latency on constrained Ryzen AI hardware | MEDIUM | MEDIUM | MEDIUM | Benchmark early (Phase 1, Sprint 2). Use shallow copies where deep copies unnecessary. Consider `dataclasses.asdict()` for specific fields. | Phase 1 | MONITORED |
| R3 | **Backward Compatibility Breakage** -- Phase 3 refactoring may break external consumers of Agent API | HIGH | HIGH | CRITICAL | Maintain backward-compatible `Agent` class as adapter. Version the API. Provide migration guide. Gate Phase 3 behind major version bump. | Phase 3 | PENDING |
| R4 | **Scope Creep via Pattern Enthusiasm** -- Team may over-adopt BAIBEL patterns beyond what GAIA needs | MEDIUM | MEDIUM | MEDIUM | Strict prioritization via Priority Matrix. Each phase has defined scope with explicit "not included" list. Weekly scope reviews. | All | MONITORED |
| R5 | **Supervisor Agent Quality** -- LLM-as-reviewer may hallucinate approvals or reject valid work | MEDIUM | MEDIUM | MEDIUM | Make Supervisor optional. Run Supervisor results through eval framework. Combine with existing automated `QualityScorer` (belt and suspenders). | Phase 2 | PENDING |
| R6 | **Team Unfamiliarity with BAIBEL** -- GAIA developers may not understand BAIBEL's design rationale | LOW | MEDIUM | LOW | This document serves as the design rationale. Schedule architecture walkthrough sessions. BAIBEL codebase is small (~2,500 lines) and well-documented. | All | MONITORED |
| R7 | **Threading Safety in State Service** -- Python singleton with both sync and async callers could introduce race conditions | MEDIUM | HIGH | HIGH | Use `threading.RLock` (as GAIA's `AuditLogger` already does). Consider `asyncio.Lock` for async paths. Extensive concurrent access tests. | Phase 1 | **RESOLVED** (100-thread tested) |
| R8 | **Integration with Existing Pipeline Tests** -- 10,572 lines of Pipeline code have established patterns that may resist state unification | LOW | MEDIUM | LOW | Phase 1 wraps existing Pipeline components rather than replacing them. `AuditLogger` remains the underlying implementation. | Phase 1 | MONITORED |
| R9 | **TOCTOU Path Traversal Vulnerability** -- Time-of-check-time-of-use race in path validation | **HIGH** | **HIGH** | **HIGH** | **Safety check runs BEFORE path normalization, blocking absolute paths and traversal** | Phase 1 | **FIXED** (Sprint 1) |
| R10 | **Token Estimation Accuracy** -- Token estimation (~4 chars/token) may have variance affecting context curation | MEDIUM | MEDIUM | MEDIUM | Hierarchical budget enforcement with graceful truncation. Consider tiktoken integration for production. | Phase 1 | **RESOLVED** (Sprint 2) |
| R11 | **Agent Initialization Order** -- Nexus connection timing may cause race conditions during Agent startup | LOW | MEDIUM | LOW | Graceful degradation: Agent operates without Chronicle if Nexus unavailable. Lazy connection pattern. | Phase 1 | **RESOLVED** (Sprint 2) |

### 7.1 Risk Exposure Summary

```
CRITICAL (1): R3 -- Backward Compatibility
HIGH     (2): R1 -- Language Translation, R9 -- TOCTOU Vulnerability (FIXED)
MEDIUM   (5): R2, R4, R5, R8, R10 (RESOLVED)
LOW      (2): R6, R11 (RESOLVED)
RESOLVED (4): R7 -- Threading Safety, R9 -- TOCTOU, R10 -- Token Estimation, R11 -- Agent Init
```

**Phase 1 Sprint 1 Quality Achievement:**
- **TOCTOU Vulnerability (R9)** identified during implementation and immediately fixed
- Path safety check now runs BEFORE path normalization, preventing:
  - Unix absolute paths (`/etc/passwd`)
  - Windows absolute paths (`C:\Windows\System32`)
  - Parent traversal attacks (`../../../etc/passwd`)
- Thread safety (R7) verified with 100+ concurrent threads

**Phase 1 Sprint 2 Quality Achievements:**
- **Token Estimation (R10)** -- Hierarchical budget enforcement with graceful truncation
- **Agent Initialization (R11)** -- Graceful degradation pattern implemented
- 102 tests passing (59 ChronicleDigest + 43 Agent-Nexus) at 100% pass rate
- Thread safety verified with 100+ concurrent event commits

---

## 8. Success Metrics

### 8.1 Quantitative Metrics

| Metric | Baseline (Current) | Phase 0 Target | Phase 1 Target | Phase 3 Target |
|--------|-------------------|----------------|----------------|----------------|
| **Base Agent Lines of Code** | 3,000 | 3,000 | 3,000 | < 500 |
| **Tool Cross-Contamination Rate** | Unknown (not measured) | 0% | 0% | 0% |
| **Agent Context Token Efficiency** | Full history dump | No change | < 4,000 tokens via digest | < 4,000 tokens |
| **Agent-Pipeline State Sharing** | None (separate systems) | None | Shared state service | Fully unified |
| **New Agent Creation Time** | Hours (class + mixins) | Hours | Hours | Minutes (YAML only) |
| **Mixin Inheritance Depth** | 10-15 classes | 10-15 | 10-15 | 2-3 classes |
| **Pipeline Quality Gate** | Code-only scoring | Code-only | Code + LLM review | Code + LLM review |
| **CLI Module Size** | 6,748 lines | 6,748 | 6,748 | < 2,000 (via decomposition) |

### 8.2 Qualitative Metrics

| Metric | Measurement Method | Target |
|--------|-------------------|--------|
| **Developer Satisfaction** | Survey after each phase | > 4.0/5.0 |
| **Onboarding Time for New Agent Developers** | Time to first agent | < 1 day |
| **Architecture Review Rating** | Peer architecture review | "Clean" rating with no critical findings |
| **External Contributor Experience** | GitHub issue/PR feedback | Positive sentiment on agent creation simplicity |

### 8.3 Program Health Indicators

| Indicator | Green | Yellow | Red |
|-----------|-------|--------|-----|
| **Schedule Variance** | < 1 week slip | 1-2 week slip | > 2 week slip |
| **Scope Change Requests** | < 2 per phase | 2-4 per phase | > 4 per phase |
| **Test Regression Count** | 0 | 1-3 minor | Any major regression |
| **Stakeholder Satisfaction** | Active engagement | Passive engagement | Escalations or concerns |

---

## Appendix A: RC# Cross-Reference Matrix

| RC# | Title | Phase 0 Impact | Phase 1 Impact | Phase 2 Impact | Phase 3 Impact | Status |
|-----|-------|----------------|----------------|----------------|----------------|--------|
| RC1 | Single-turn agent passthrough | Indirect (enables future tool loop) | No impact | No impact | No impact | MITIGATED |
| **RC2** | **Tool implementations missing** | **Direct (registry enables tool loading)** | No impact | No impact | No impact | **FIXED** |
| RC3 | System prompt files missing | No impact | No impact | No impact | No impact | FIXED |
| RC4 | Thin user prompt | No impact | Indirect (Chronicle provides context) | Indirect (Supervisor feedback) | Indirect (Context Lens) | PARTIALLY FIXED |
| RC5 | Save only writes JSON | No impact | No impact | No impact | No impact | FIXED |
| RC6 | System prompt path wrong attribute | No impact | No impact | No impact | No impact | FIXED |
| **RC7** | **Empty tool descriptions in system prompt** | **Direct (registry populates descriptions)** | No impact | No impact | No impact | **FIXED** |
| RC8 | Defects not passed to agents | No impact | Indirect (Chronicle tracks) | Direct (Supervisor feedback) | No impact | FIXED |

---

## Appendix B: Effort Summary

| Phase | Duration | FTE Effort | Calendar Weeks | Status |
|-------|----------|------------|----------------|--------|
| Phase 0: Tool Scoping | 2 weeks | 2 person-weeks | 2 | **COMPLETE** (204 tests) |
| Phase 1 Sprint 1: Nexus Service Core | 2 weeks | 4 person-weeks | 2 | **COMPLETE** (79 tests) |
| Phase 1 Sprint 2: ChronicleDigest & Agent | 2 weeks | 4 person-weeks | 2 | **COMPLETE** (102 tests) |
| Phase 1 Sprint 3: ChronicleDigest Extension | 2 weeks | 4 person-weeks | 2 | **COMPLETE** (59+43 tests) |
| Phase 1 Sprint 4: Pipeline-Nexus Integration | 2 weeks | 4 person-weeks | 2 | **COMPLETE** (31 tests) |
| **Phase 1 (Total)** | **8 weeks** | **16 person-weeks** | **8** | **COMPLETE** (212 tests, QG2 CONDITIONAL PASS) |
| **Phase 2 Sprint 1: Supervisor Agent** | **2 weeks** | **4 person-weeks** | **2** | **COMPLETE** (59 tests, QG2 PASS) |
| **Phase 2 Sprint 2: Context Lens** | **4 weeks** | **4 person-weeks** | **4** | **COMPLETE** (117 tests, QG2 PASS) |
| **Phase 2 Sprint 3: Workspace Sandboxing** | **2 weeks** | **4 person-weeks** | **2** | **COMPLETE** (98 tests, QG3 PASS) |
| **Phase 2 (Total)** | **8 weeks** | **12 person-weeks** | **8** | **COMPLETE** (274 tests, QG3 PASS) |
| **Phase 3 Sprint 1: Modular Architecture** | **3 weeks** | **6 person-weeks** | **3** | **COMPLETE** (195 tests, QG4 PASS) |
| **Phase 3 Sprint 2: DI + Performance** | **3 weeks** | **6 person-weeks** | **3** | **COMPLETE** (157 tests, QG4 PASS) |
| Phase 3 Sprint 3: Caching + Config | 3 weeks | 6 person-weeks | 3 | PENDING |
| Phase 3 Sprint 4: Observability + API | 3 weeks | 6 person-weeks | 3 | PENDING |
| **Phase 3 (Total)** | **12 weeks** | **24 person-weeks** | **12** | **Sprint 1 & 2 COMPLETE, Sprint 3 PENDING** |
| **Total (Planned)** | **38 weeks** | **100 person-weeks** | **38 (~9 months)** | **~85% Complete, Phase 3 S3 PENDING** |

---

**END OF SPECIFICATION**

**Distribution:** GAIA Development Team, AMD AI Framework Team
**Next Action:** Phase 3 Implementation - Sprint 3 (Caching + Enterprise Config)
**Review Cadence:** Bi-weekly program status reviews
**Version History:**
- v1.0: Initial specification
- v1.1: Phase 0 kickoff
- v1.2: Phase 0 complete, Phase 1 kickoff
- v1.3: Phase 1 Sprint 1 complete - NexusService (763 LOC, 79 tests), TOCTOU fix
- v1.4: Phase 1 Sprint 2 complete - ChronicleDigest (+230 LOC, 59 tests) + Agent-Nexus Integration (+140 LOC, 43 tests), 38% program complete
- v1.5: Phase 1 COMPLETE - Pipeline-Nexus Integration (+100 LOC, 31 tests), QG2 CONDITIONAL PASS, 50% program complete
- v1.6: Phase 2 KICKOFF READY - Implementation plan created (docs/reference/phase2-implementation-plan.md)
- v1.7: Phase 2 Sprint 1 COMPLETE - Supervisor Agent (848 LOC), ReviewOps (526 LOC), 59 tests, QG2 PASS, 58% program complete
- v1.8: Phase 2 Sprint 2 COMPLETE - TokenCounter (336 LOC), ContextLens (569 LOC), EmbeddingRelevance (443 LOC), 117 tests, QG2 PASS, 75% program complete
- v2.0: Phase 2 COMPLETE - WorkspacePolicy (667 LOC), SecurityValidator (503 LOC), PipelineIsolation (541 LOC), 98 tests, QG3 PASS, 75% program complete (Phase 0, 1, 2 done)
- v2.1: Phase 3 KICKED OFF - Architectural Modernization (docs/reference/phase3-implementation-plan.md, phase3-technical-spec.md created)
- v2.2: Phase 3 Sprint 1 COMPLETE - Modular Architecture Core (capabilities.py 340 LOC, profile.py 360 LOC, executor.py 650 LOC, plugin.py 680 LOC, 195 tests), Quality Gate 4 PASS, ~80% program complete
- **v2.3: Phase 3 Sprint 2 COMPLETE - DI + Performance (di_container.py 770 LOC, adapter.py 545 LOC, async_utils.py 703 LOC, connection_pool.py 787 LOC, 157 tests), Quality Gate 4 PASS, ~85% program complete**
