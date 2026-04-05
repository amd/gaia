# BAIBEL-GAIA Master Integration Specification

**Version:** 1.0
**Status:** Ready for Implementation
**Classification:** Strategic Architecture Document
**Date:** 2026-04-05

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

| # | GAIA Pain Point | Severity | BAIBEL Pattern | Phase |
|---|-----------------|----------|----------------|-------|
| 1 | Monolithic Base Agent (3,000 lines) | CRITICAL | Agent-as-Data (flat config) | Phase 3 |
| 2 | Monolithic CLI (6,748 lines) | HIGH | N/A (indirect via decomposition) | Phase 3 |
| 3 | Global Mutable Tool Registry | CRITICAL | **Tool Scoping** (per-agent allowlist) | **Phase 0** |
| 4 | Excessive Mixin Composition (10-15 classes) | HIGH | Agent-as-Data (no inheritance) | Phase 3 |
| 5 | Dual Architecture (Agent vs Pipeline) | CRITICAL | **Nexus** (unified state layer) | Phase 1 |
| 6 | Sync/Async Bridging Complexity | MEDIUM | N/A (different runtime model) | N/A |
| 7 | Security Model Gaps | MEDIUM | **Workspace** (sandboxed boundary) | Phase 2 |
| 8 | Tight Coupling (Agent to AgentSDK) | HIGH | Service Layer Decoupling | Phase 3 |

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

**Classes:**
| Class | Purpose | Integration Point |
|-------|---------|-------------------|
| `NexusService` | Python singleton state service | New: `src/gaia/state/nexus.py` |
| `WorkspaceIndex` | Workspace metadata tracking | New: `src/gaia/state/workspace.py` |
| `ChronicleDigest` | Token-efficient context summarization | Extension: `AuditLogger.get_digest()` |

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

### Phase 0: Tool Scoping (2 weeks)

| Week | Task | Owner | Deliverables |
|------|------|-------|--------------|
| 1 | ToolRegistry implementation | senior-developer | `ToolRegistry`, `AgentScope`, `ExceptionRegistry` classes |
| 1 | Unit tests | testing-quality-specialist | `test_tool_registry.py`, `test_backward_compat_shim.py` |
| 2 | Agent integration | senior-developer | `Agent._tool_scope`, `ConfigurableAgent` updates |
| 2 | Security tests | testing-quality-specialist | `test_tool_isolation.py`, `test_allowlist_bypass.py` |
| 2 | Quality Gate 1 | quality-reviewer | BC-001, SEC-001, PERF-001, MEM-001 validation |

**Exit Criteria:**
- [ ] BC-001: Backward compatibility tests pass (100%)
- [ ] SEC-001: Allowlist bypass tests fail (0% success rate)
- [ ] PERF-001: Performance overhead <5%
- [ ] MEM-001: Zero memory leaks (0% threshold)

---

### Phase 1: State Unification (8 weeks)

| Sprint | Weeks | Tasks | Deliverables |
|--------|-------|-------|--------------|
| Sprint 1-2 | 1-4 | Core State Service | `NexusService`, `WorkspaceIndex`, Chronicle digest |
| Sprint 3-4 | 5-8 | Integration & Testing | Agent/Pipeline state sharing, performance benchmarks |

**Detailed Tasks:**

**Sprint 1-2 (Weeks 1-4):**
- [ ] Create `src/gaia/state/nexus.py` -- Python singleton state service
- [ ] Create `src/gaia/state/workspace.py` -- Workspace metadata index
- [ ] Wire `Agent.run()` loop to commit events to state service
- [ ] Wire `PipelineEngine` to share the same state service instance

**Sprint 3-4 (Weeks 5-8):**
- [ ] Update `Agent._run_step()` to receive curated context from state service
- [ ] Update `ConfigurableAgent.execute()` to read/write state service
- [ ] Integration tests: Agent and Pipeline sharing state in same execution
- [ ] Performance benchmarks: digest generation latency with local models

**Exit Criteria:**
- [ ] Agent and Pipeline systems share a single state instance
- [ ] Context digest fits within 4K token window for local models
- [ ] No regression in existing Pipeline test suite
- [ ] AuditLogger hash chain integrity preserved

---

### Phase 2: Quality Enhancement (6 weeks)

| Sprint | Weeks | Tasks | Deliverables |
|--------|-------|-------|--------------|
| Sprint 1-2 | 1-4 | Supervisor Agent | `quality-supervisor.yaml`, `review_ops.py`, pipeline integration |
| Sprint 3 | 5-6 | Workspace Sandboxing | `WorkspacePolicy`, hard boundaries per pipeline |

**Detailed Tasks:**

**Sprint 1-2 (Weeks 1-4):**
- [ ] Create `config/agents/quality-supervisor.yaml` -- Supervisor agent definition
- [ ] Create `src/gaia/tools/review_ops.py` -- `review_consensus` tool
- [ ] Update `PipelineEngine._execute_phase()` to invoke Supervisor after QUALITY phase
- [ ] Update `DecisionEngine` to incorporate Supervisor feedback into LOOP_BACK decisions

**Sprint 3 (Weeks 5-6):**
- [ ] Add `WorkspacePolicy` to enforce hard boundaries per pipeline execution
- [ ] Integrate workspace metadata with state service from Phase 1
- [ ] Add workspace state to Supervisor's context for artifact review

**Exit Criteria:**
- [ ] Supervisor catches defects that automated `QualityScorer` misses
- [ ] Pipeline LOOP_BACK rate improves (fewer unnecessary iterations)
- [ ] Workspace isolation prevents cross-pipeline file contamination

---

### Phase 3: Architectural Modernization (12 weeks)

| Sprint | Weeks | Tasks | Deliverables |
|--------|-------|-------|--------------|
| Sprint 1-4 | 1-8 | Agent Configuration Model | `AgentProfile`, `AgentExecutor`, `AgentAdapter` |
| Sprint 5-6 | 9-12 | LLM Service Decoupling | `LLMAgentBridge`, `AgentSDK` refactor |

**Detailed Tasks:**

**Sprint 1-4 (Weeks 1-8):**
- [ ] Create `src/gaia/agents/config.py` -- Pure-data `AgentProfile` configuration
- [ ] Create `src/gaia/agents/executor.py` -- `AgentExecutor` behavior injection engine
- [ ] Refactor `Agent.__init__()` from 40-parameter constructor to `AgentProfile` + `ExecutionConfig` pattern
- [ ] Deprecate mixin-based composition; provide adapter layer for backward compatibility

**Sprint 5-6 (Weeks 9-12):**
- [ ] Create `src/gaia/llm/agent_bridge.py` -- Stateless LLM interaction layer
- [ ] Update `AgentSDK` to be usable independently of `Agent` class
- [ ] Migration guide for external agent implementations
- [ ] Comprehensive regression testing across all 8 agent types

**Exit Criteria:**
- [ ] Base Agent class reduced from 3,000 lines to under 500
- [ ] Mixin depth reduced from 10-15 classes to 2-3
- [ ] No regression in any agent's behavior (ChatAgent, CodeAgent, BlenderAgent, etc.)
- [ ] External API surface backward-compatible via adapter layer
- [ ] New agent creation requires only YAML definition + tool functions (no class)

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

| ID | Risk | Probability | Impact | Exposure | Mitigation Strategy | Phase |
|----|------|------------|--------|----------|---------------------|-------|
| R1 | **Language Translation Fidelity** -- BAIBEL patterns designed for TypeScript/Node.js may not translate cleanly to Python | MEDIUM | HIGH | HIGH | Create Python-native implementations inspired by patterns, not direct ports. Validate with prototypes before committing. | All |
| R2 | **Performance Degradation** -- State service snapshot (`copy.deepcopy`) adds latency on constrained Ryzen AI hardware | MEDIUM | MEDIUM | MEDIUM | Benchmark early (Phase 1, Sprint 2). Use shallow copies where deep copies unnecessary. Consider `dataclasses.asdict()` for specific fields. | Phase 1 |
| R3 | **Backward Compatibility Breakage** -- Phase 3 refactoring may break external consumers of Agent API | HIGH | HIGH | CRITICAL | Maintain backward-compatible `Agent` class as adapter. Version the API. Provide migration guide. Gate Phase 3 behind major version bump. | Phase 3 |
| R4 | **Scope Creep via Pattern Enthusiasm** -- Team may over-adopt BAIBEL patterns beyond what GAIA needs | MEDIUM | MEDIUM | MEDIUM | Strict prioritization via Priority Matrix. Each phase has defined scope with explicit "not included" list. Weekly scope reviews. | All |
| R5 | **Supervisor Agent Quality** -- LLM-as-reviewer may hallucinate approvals or reject valid work | MEDIUM | MEDIUM | MEDIUM | Make Supervisor optional. Run Supervisor results through eval framework. Combine with existing automated `QualityScorer` (belt and suspenders). | Phase 2 |
| R6 | **Team Unfamiliarity with BAIBEL** -- GAIA developers may not understand BAIBEL's design rationale | LOW | MEDIUM | LOW | This document serves as the design rationale. Schedule architecture walkthrough sessions. BAIBEL codebase is small (~2,500 lines) and well-documented. | All |
| R7 | **Threading Safety in State Service** -- Python singleton with both sync and async callers could introduce race conditions | MEDIUM | HIGH | HIGH | Use `threading.RLock` (as GAIA's `AuditLogger` already does). Consider `asyncio.Lock` for async paths. Extensive concurrent access tests. | Phase 1 |
| R8 | **Integration with Existing Pipeline Tests** -- 10,572 lines of Pipeline code have established patterns that may resist state unification | LOW | MEDIUM | LOW | Phase 1 wraps existing Pipeline components rather than replacing them. `AuditLogger` remains the underlying implementation. | Phase 1 |

### 7.1 Risk Exposure Summary

```
CRITICAL (1): R3 -- Backward Compatibility
HIGH     (2): R1 -- Language Translation, R7 -- Threading Safety
MEDIUM   (4): R2, R4, R5, R8
LOW      (1): R6
```

### 7.2 Key Mitigation Principle

**Wrap, Do Not Replace.** The consistent mitigation strategy across all risks is to extend GAIA's existing infrastructure rather than replacing it. The state service wraps `AuditLogger`. The Supervisor complements `QualityScorer`. The tool scoping extends the existing YAML definitions. This preserves GAIA's investment while gaining BAIBEL's architectural benefits.

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

| Phase | Duration | FTE Effort | Calendar Weeks |
|-------|----------|------------|----------------|
| Phase 0: Tool Scoping | 2 weeks | 2 person-weeks | 2 |
| Phase 1: State Unification | 8 weeks | 16 person-weeks | 8 |
| Phase 2: Quality Enhancement | 6 weeks | 12 person-weeks | 6 |
| Phase 3: Architectural Modernization | 12 weeks | 36 person-weeks | 12 |
| **Total** | **28 weeks** | **66 person-weeks** | **28 (~7 months)** |

---

**END OF SPECIFICATION**

**Distribution:** GAIA Development Team, AMD AI Framework Team
**Next Action:** Phase 0 Day 1 implementation kickoff
**Review Cadence:** Bi-weekly program status reviews upon approval
