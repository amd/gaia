# EtherREPL - Python REPL Implementation Specification

**Version:** 1.0.0  
**Date:** 2026-04-12  
**Status:** Implemented

---

## Overview

EtherREPL is a persistent Python REPL (Read-Eval-Print Loop) implementation for GAIA that enables safe, sandboxed code execution with session state persistence across multiple evaluation calls. It uses subprocess-based isolation rather than in-process `eval()` for security.

---

## Architecture

### Core Components

```
┌─────────────────────────────────────────────────────────────────┐
│                        EtherREPL                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  REPLSession 1  │  REPLSession 2  │  REPLSession N      │   │
│  │  ┌───────────┐  │  ┌───────────┐  │  ┌───────────┐      │   │
│  │  │ Workspace │  │  │ Workspace │  │  │ Workspace │      │   │
│  │  │ (isolated)│  │  │ (isolated)│  │  │ (isolated)│      │   │
│  │  │ state.pkl │  │  │ state.pkl │  │  │ state.pkl │      │   │
│  │  └───────────┘  │  └───────────┘  │  └───────────┘      │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐  │
│  │ PipelineIsolation│  │ WorkspacePolicy │  │ ComponentLoader│  │
│  │ (workspace mgmt) │  │ (path security) │  │ (templates)   │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Subprocess-based execution** | Security isolation, prevents code from accessing agent process memory |
| **Pickle-based state persistence** | Simple, Python-native, preserves complex object graphs |
| **PipelineIsolation integration** | Reuses existing workspace isolation infrastructure |
| **Hash-named workspaces** | Privacy-preserving, prevents session ID enumeration |
| **Context manager API** | Pythonic, ensures proper cleanup |

---

## API Reference

### EtherREPL Class

```python
from gaia.agents.code.tools.ether_repl import EtherREPL

repl = EtherREPL(
    workspace_root="~/.gaia/ether_repl",  # Optional
    default_timeout=60,                    # Optional
    persist_sessions=False                 # Optional
)
```

#### Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `create_session(session_id, timeout)` | Create new REPL session | `REPLSession` |
| `get_session(session_id)` | Get existing session | `REPLSession` |
| `cleanup(session_id)` | Clean up session workspace | `bool` |
| `cleanup_all()` | Clean up all sessions | `Dict[str, bool]` |
| `get_statistics()` | Get usage statistics | `Dict[str, Any]` |

### REPLSession Class

```python
with repl.create_session("analysis-001") as session:
    result = session.eval("x = [1, 2, 3, 4, 5]")
    result = session.eval("sum(x)")  # Returns 15
```

#### Methods

| Method | Description | Returns |
|--------|-------------|---------|
| `eval(code, timeout)` | Execute Python code | `ExecutionResult` |
| `cleanup()` | Clean up workspace | `bool` |

#### ExecutionResult Dataclass

```python
@dataclass
class ExecutionResult:
    success: bool
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    duration_sec: float = 0.0
    timed_out: bool = False
    state_changed: bool = False
    session_id: Optional[str] = None
```

---

## Tool Integration

### Registration

```python
from gaia.agents.code.tools.ether_repl import create_ether_repl

# Factory function creates REPL and registers all tools
repl = create_ether_repl(
    workspace_root="/tmp/ether",
    default_timeout=120,
    register_tools=True
)
```

### Available Tools

| Tool | Description |
|------|-------------|
| `ether_create_session(session_id, timeout)` | Create persistent REPL session |
| `ether_eval(session_id, code, timeout)` | Execute code in session |
| `ether_cleanup_session(session_id)` | Clean up session |
| `ether_get_statistics()` | Get REPL statistics |
| `read_component_template(component_path)` | Read component template |
| `write_component_template(path, content, frontmatter)` | Write template |
| `list_component_templates(component_type)` | List templates |
| `render_component_template(path, variables)` | Render with variables |

### Example Agent Usage

```python
from gaia.agents.base.tools import ToolRegistry

registry = ToolRegistry.get_instance()
scope = registry.create_scope("code-agent", allowed_tools=[
    "ether_create_session",
    "ether_eval",
    "ether_cleanup_session",
])

# Create session
scope.execute_tool("ether_create_session", "analysis-001")

# Execute code with persistent state
scope.execute_tool("ether_eval", "analysis-001", "import pandas as pd")
scope.execute_tool("ether_eval", "analysis-001", "df = pd.read_csv('data.csv')")
result = scope.execute_tool("ether_eval", "analysis-001", "df.describe()")

# Cleanup
scope.execute_tool("ether_cleanup_session", "analysis-001")
```

---

## Security Model

### Isolation Layers

1. **Process Isolation**: Code runs in subprocess, not agent process
2. **Workspace Isolation**: Each session has hash-named workspace via `PipelineIsolation`
3. **Path Validation**: All paths validated via `WorkspacePolicy`
4. **Timeout Enforcement**: Subprocess killed if exceeds timeout
5. **Output Truncation**: Large outputs truncated to prevent memory issues

### Blocked Patterns

```python
DANGEROUS_PATTERNS = [
    "os.system(",
    "subprocess.call(",
    "subprocess.Popen(",
    "__import__('os')",
    "__import__('subprocess')",
    "eval(__import__",
    "exec(__import__",
]
```

### WorkspacePolicy Integration

```python
# All EtherREPL operations use WorkspacePolicy for path validation
self._workspace_policy = WorkspacePolicy(
    allowed_paths=[str(self._workspace_root)],
    workspace_root=str(self._workspace_root),
)

# Path traversal blocked
session.eval("open('../../../etc/passwd')")  # Blocked by workspace isolation

# Shell injection blocked
session.eval("os.system('rm -rf /')")  # Blocked by pattern detection
```

---

## State Persistence Design

### Pickle-Based State

State is persisted between `eval()` calls using Python's `pickle` module:

```python
# _build_execution_script() generates:
"""
import pickle

# Load previous state
state = {}
if state_path.exists():
    with open(state_path, "rb") as f:
        state = pickle.load(f)

# Execute user code
exec(compile(code, "<repl>", "exec"), state)

# Save updated state
with open(state_path, "wb") as f:
    pickle.dump(state, f)
"""
```

### State File Format

```
~/.gaia/ether_repl/
└── ws_<hash>/
    ├── _ether_state.pkl    # Session state (pickled dict)
    └── eval_<timestamp>.py # Temporary execution script (deleted after run)
```

### Persistence Modes

| Mode | Behavior |
|------|----------|
| `persist=False` (default) | State persists during session, workspace deleted on cleanup |
| `persist=True` | Workspace preserved after cleanup for debugging |

---

## Component Framework Integration

### Living Templates

The Component Framework integration makes static templates "living" by allowing agents to:

1. **Read** templates from `component-framework/` directory
2. **Write** new templates programmatically
3. **List** available templates by type
4. **Render** templates with variable substitution

### Template Types

```python
VALID_TEMPLATE_TYPES = [
    "memory",      # Working memory, short-term, long-term
    "knowledge",   # Domain knowledge bases
    "tasks",       # Task definitions
    "commands",    # Command templates
    "documents",   # Document templates
    "checklists",  # Quality checklists
    "personas",    # Agent personas
    "workflows",   # Workflow definitions
    "templates",   # Meta-templates
]
```

### Example: Agent Updates Checklist

```python
# Agent reads existing checklist
template = read_component_template("checklists/code-review.md")

# Agent renders with variables
rendered = render_component_template(
    "tasks/new-feature.md",
    {"{{FEATURE_NAME}}": "EtherREPL", "{{VERSION}}": "1.0.0"}
)

# Agent writes new checklist
write_component_template(
    "checklists/repl-security.md",
    content="# REPL Security Checklist\n\n- [ ] Subprocess isolation...",
    frontmatter={
        "template_id": "repl-security-checklist",
        "template_type": "checklists",
        "version": "1.0.0",
        "description": "Security checklist for REPL implementation"
    }
)
```

---

## Concurrent Session Management

### Thread Safety

All `EtherREPL` and `REPLSession` methods are thread-safe:

```python
# Thread-safe session creation
repl = EtherREPL()

# Thread 1
session1 = repl.create_session("thread-1")

# Thread 2 (concurrent)
session2 = repl.create_session("thread-2")

# Both sessions isolated via hash-named workspaces
```

### Session Limits

```python
# Get active session count
stats = repl.get_statistics()
print(f"Active sessions: {stats['active_sessions']}")

# Recommended: Limit concurrent sessions per agent
MAX_CONCURRENT_SESSIONS = 5
if stats['active_sessions'] >= MAX_CONCURRENT_SESSIONS:
    # Cleanup oldest session before creating new
    oldest_id = stats['active_session_ids'][0]
    repl.cleanup(oldest_id)
```

---

## Error Handling

### Exception Hierarchy

```
EtherREPLError
├── SessionNotFoundError      # Session doesn't exist
├── ExecutionTimeoutError     # Code execution timed out
└── StatePersistenceError     # Pickle serialize/deserialize failed
```

### Error Response Format

```python
# Error responses include structured metadata
{
    "success": False,
    "error": "Session not found: nonexistent-session",
    "session_id": "nonexistent-session",
    "stderr": "",
    "stdout": "",
    "return_code": -1
}
```

---

## Usage Examples

### Basic REPL Session

```python
from gaia.agents.code.tools.ether_repl import create_ether_repl

repl = create_ether_repl()

# Create session
with repl.create_session("demo-001") as session:
    # Variable persists between calls
    session.eval("counter = 0")
    
    for i in range(5):
        result = session.eval("counter += 1; counter")
        print(f"Counter: {result.stdout.strip()}")
    
    # Counter: 1, 2, 3, 4, 5
```

### Data Analysis Session

```python
with repl.create_session("analysis-001", timeout=300) as session:
    # Load libraries
    session.eval("import pandas as pd")
    session.eval("import numpy as np")
    
    # Load data
    session.eval("df = pd.read_csv('sales_data.csv')")
    
    # Analysis
    result = session.eval("df.groupby('region')['revenue'].sum()")
    print(result.stdout)
    
    # Visualization
    session.eval("df.plot.bar(x='region', y='revenue')")
```

### Machine Learning Session

```python
with repl.create_session("ml-001", timeout=600) as session:
    # Training with state persistence
    session.eval("from sklearn.ensemble import RandomForestClassifier")
    session.eval("model = RandomForestClassifier(n_estimators=100)")
    session.eval("model.fit(X_train, y_train)")
    
    # Later evaluation uses persisted model
    result = session.eval("model.score(X_test, y_test)")
    print(f"Accuracy: {result.stdout.strip()}")
```

---

## Performance Considerations

### Subprocess Overhead

Each `eval()` call spawns a subprocess. For tight loops, consider:

```python
# BAD: High overhead
for i in range(100):
    session.eval(f"x += {i}")  # 100 subprocesses

# GOOD: Batch operations
session.eval("x += sum(range(100))")  # 1 subprocess
```

### State Serialization Cost

Pickle serialization adds ~1-5ms per eval for small state, ~100ms+ for large DataFrames.

```python
# For large state, consider:
# 1. Increase timeout
session = repl.create_session("big-data", timeout=300)

# 2. Persist to disk explicitly
session.eval("df.to_pickle('state_df.pkl')")
session.eval("df = pd.read_pickle('state_df.pkl')")
```

---

## Troubleshooting

### Session Not Found

```python
# ERROR: Session not created or already cleaned up
session = repl.get_session("nonexistent")  # Raises SessionNotFoundError

# FIX: Create session first
with repl.create_session("my-session") as session:
    session.eval("x = 1")
```

### Timeout Handling

```python
# Long-running code with timeout
result = session.eval("import time; time.sleep(100)", timeout=10)
if result.timed_out:
    print(f"Execution timed out after {result.duration_sec}s")
```

### State Corruption

```python
# If pickle fails, state resets
# FIX: Catch and reinitialize
try:
    session = repl.get_session("corrupted")
except StatePersistenceError:
    repl.cleanup("corrupted")
    with repl.create_session("corrupted") as session:
        session.eval("# Fresh state")
```

---

## Future Enhancements

| Enhancement | Priority | Description |
|-------------|----------|-------------|
| In-process eval option | Low | For trusted code, reduce subprocess overhead |
| Async eval support | Medium | Non-blocking execution for UI responsiveness |
| State diff/patch | Low | Only serialize changed variables |
| Resource limits | Medium | Memory/CPU limits per session |
| Session snapshots | Low | Save/restore session state to named checkpoints |

---

## References

- `src/gaia/agents/code/tools/testing.py` - Existing subprocess execution
- `src/gaia/pipeline/isolation.py` - PipelineIsolation context manager
- `src/gaia/security/workspace.py` - WorkspacePolicy path validation
- `src/gaia/utils/component_loader.py` - Component Framework template loading
