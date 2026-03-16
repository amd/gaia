# Performance Plan: Profiling, Speed & Native Acceleration

> **Status:** Draft
> **Date:** 2025-02-05
> **Branch:** `kalin/cli`

## Overview

This plan covers three phases:

1. **Profile** — Measure actual bottlenecks with real data before optimizing
2. **Optimize Python** — Fix the easy wins (lazy imports, connection pooling, library swaps)
3. **Native Acceleration** — Port proven bottlenecks to C++/Rust where profiling shows clear ROI

No optimization without measurement. Every change must be validated against profiling data.

---

## Phase 1: Profiling

### 1.1 CLI Startup Profiling

**Goal:** Identify what makes `gaia --help` and `gaia chat` slow before any code runs.

#### Import Time Analysis

```bash
# Measure import overhead for every module
python -X importtime -c "import gaia.cli" 2> import_times.txt

# Sort by cumulative time
sort -t'|' -k2 -n import_times.txt | tail -30
```

**What to look for:**
- Top-level imports that pull in heavy libraries (`transformers`, `torch`, `sentence_transformers`)
- Transitive imports from `gaia.__init__.py` (currently loads `Agent`, `DatabaseAgent`, `FileWatcher`)
- `load_dotenv()` calls in module-level code (`__init__.py:11`, `cli.py:41`)
- `LemonadeClient` import at `cli.py:16-24` (pulls in `openai`, `psutil`)

#### Startup Timing Script

```python
# util/profile_startup.py
"""Profile CLI startup time for each subcommand."""
import subprocess
import time
import json

commands = [
    ["gaia", "--help"],
    ["gaia", "chat", "--help"],
    ["gaia", "llm", "--help"],
    ["gaia", "cache", "status"],
    ["gaia", "prompt", "hello"],
    ["gaia", "chat"],  # interactive launch time
]

results = {}
for cmd in commands:
    times = []
    for _ in range(5):
        start = time.perf_counter()
        subprocess.run(cmd, capture_output=True, timeout=30)
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    results[" ".join(cmd)] = {
        "mean": sum(times) / len(times),
        "min": min(times),
        "max": max(times),
    }

for cmd, t in sorted(results.items(), key=lambda x: x[1]["mean"]):
    print(f"{t['mean']*1000:7.0f}ms  {cmd}")
```

**Target:** Establish baseline startup times for every command.

### 1.2 Agent Loop Profiling

**Goal:** Measure where wall-clock time goes during a typical agent session.

#### Instrumented Agent Run

```python
# util/profile_agent.py
"""Profile a single agent query with detailed timing."""
import cProfile
import pstats
import time

# Patch key functions with timing
from gaia.agents.base import agent as agent_module

original_parse = agent_module.Agent._parse_llm_response
original_truncate = agent_module.Agent._truncate_large_content
original_execute = agent_module.Agent._execute_tool
original_compose = agent_module.Agent._compose_system_prompt

def timed(name, fn):
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = fn(*args, **kwargs)
        elapsed = time.perf_counter() - start
        print(f"  [{name}] {elapsed*1000:.1f}ms")
        return result
    return wrapper

agent_module.Agent._parse_llm_response = timed("parse_response", original_parse)
agent_module.Agent._truncate_large_content = timed("truncate", original_truncate)
agent_module.Agent._execute_tool = timed("execute_tool", original_execute)
agent_module.Agent._compose_system_prompt = timed("compose_prompt", original_compose)
```

#### cProfile Full Run

```bash
# Full cProfile of a chat query
python -m cProfile -o agent_profile.prof -c "
from gaia.agents.chat.agent import ChatAgent
agent = ChatAgent()
agent.process_query('What is GAIA?')
"

# Analyze
python -c "
import pstats
p = pstats.Stats('agent_profile.prof')
p.sort_stats('cumulative')
p.print_stats(30)
"
```

#### Per-Step Breakdown

Instrument `process_query` (`agent.py:1315`) to log timing for each phase:

| Phase | What to Measure | Location |
|-------|----------------|----------|
| Prompt composition | Time to build messages array | `agent.py:1344-1371` |
| LLM inference | Network round-trip time | `agent.py:1814` (`chat.send_messages()`) |
| Response parsing | JSON extraction from LLM text | `agent.py:690-865` (`_parse_llm_response`) |
| Tool dispatch | Lookup + argument validation | `agent.py:967-1037` (`_execute_tool`) |
| Tool execution | Actual tool work (RAG search, shell, etc.) | Varies by tool |
| Result handling | Truncation, JSON serialization | `agent.py:1221-1310` |
| Console output | Rich rendering + terminal I/O | `console.py:381-625` |

**Expected finding:** LLM inference dominates (80-95% of wall time). The remaining 5-20% is where Python optimization matters.

### 1.3 JSON Profiling

**Goal:** Quantify how much time is spent in `json.loads`/`json.dumps` per step.

```python
# Monkey-patch json module to measure cumulative time
import json
import time
import functools

_json_stats = {"loads_ms": 0, "dumps_ms": 0, "loads_calls": 0, "dumps_calls": 0}

_original_loads = json.loads
_original_dumps = json.dumps

@functools.wraps(json.loads)
def _timed_loads(*args, **kwargs):
    start = time.perf_counter()
    result = _original_loads(*args, **kwargs)
    _json_stats["loads_ms"] += (time.perf_counter() - start) * 1000
    _json_stats["loads_calls"] += 1
    return result

@functools.wraps(json.dumps)
def _timed_dumps(*args, **kwargs):
    start = time.perf_counter()
    result = _original_dumps(*args, **kwargs)
    _json_stats["dumps_ms"] += (time.perf_counter() - start) * 1000
    _json_stats["dumps_calls"] += 1
    return result

json.loads = _timed_loads
json.dumps = _timed_dumps

# Run agent, then print stats
# ... agent.process_query(...) ...

print(f"json.loads: {_json_stats['loads_calls']} calls, {_json_stats['loads_ms']:.1f}ms total")
print(f"json.dumps: {_json_stats['dumps_calls']} calls, {_json_stats['dumps_ms']:.1f}ms total")
```

### 1.4 RAG Profiling

**Goal:** Measure embedding generation, FAISS search, and chunking separately.

| Operation | Location | How to Measure |
|-----------|----------|---------------|
| Embedding generation | `rag/sdk.py:329-406` | Time the `self.embedder.embeddings()` call |
| FAISS search | `rag/sdk.py:1972` | Time `self.index.search()` |
| Chunk retrieval | `rag/sdk.py:1902` | Time full `_retrieve_chunks()` |
| Document indexing | `rag/sdk.py:408-998` | Time per-document indexing |
| Text chunking | `rag/sdk.py:1000-1200` | Time `_split_text_into_chunks()` |

```python
# util/profile_rag.py
"""Profile RAG operations independently."""
import time

from gaia.rag.sdk import RAGSDK

rag = RAGSDK()

# Index a test document
start = time.perf_counter()
rag.index_document("test_doc.pdf")
index_time = time.perf_counter() - start
print(f"Indexing: {index_time*1000:.0f}ms")

# Query
start = time.perf_counter()
results = rag.query("What is the main topic?", top_k=5)
query_time = time.perf_counter() - start
print(f"Query (end-to-end): {query_time*1000:.0f}ms")
```

### 1.5 Memory Profiling

**Goal:** Understand memory footprint of agent sessions.

```bash
# Peak memory during agent init + first query
python -c "
import tracemalloc
tracemalloc.start()

from gaia.agents.chat.agent import ChatAgent
agent = ChatAgent()

snapshot = tracemalloc.take_snapshot()
stats = snapshot.statistics('lineno')
print('Top 10 memory allocations:')
for stat in stats[:10]:
    print(f'  {stat}')

current, peak = tracemalloc.get_traced_memory()
print(f'Current: {current/1024/1024:.1f}MB, Peak: {peak/1024/1024:.1f}MB')
"
```

### 1.6 Profiling Automation

Create a single script that runs all profiling and produces a report:

```bash
# util/profile_all.py — runs all profiling suites, outputs JSON + markdown report
python util/profile_all.py --output docs/plans/profile-results.md
```

Output format:

```markdown
# Profiling Results — YYYY-MM-DD

## CLI Startup
| Command | Mean (ms) | Min | Max |
|---------|-----------|-----|-----|
| gaia --help | 1,234 | ... | ... |
| gaia chat --help | ... | ... | ... |

## Agent Loop (5-step query)
| Phase | Total (ms) | % of Total | Calls |
|-------|-----------|-----------|-------|
| LLM inference | 8,500 | 89% | 5 |
| JSON parsing | 120 | 1.3% | 22 |
| ...

## JSON Operations
| Operation | Calls | Total (ms) | Avg (ms) |
|-----------|-------|-----------|----------|
| json.loads | 22 | 45 | 2.0 |
| json.dumps | 18 | 75 | 4.2 |

## RAG Operations
...

## Memory
...
```

This report becomes the baseline. Every optimization is measured against it.

---

## Phase 2: Python Optimizations

### 2.1 Lazy Imports (High Impact, Low Effort)

**Problem:** `cli.py:14-27` imports `create_client`, `LemonadeClient`, `AgentConsole` at module level. Every `gaia` invocation pays this cost.

**Fix:** Move all imports inside the subcommand handler functions.

```python
# Before (cli.py:14-27)
from gaia.agents.base.console import AgentConsole
from gaia.llm import create_client
from gaia.llm.lemonade_client import LemonadeClient
# ...

# After — inside each handler
def cmd_chat(args):
    from gaia.agents.chat.agent import ChatAgent
    from gaia.agents.base.console import AgentConsole
    # ...
```

Also fix `gaia/__init__.py:11-18` — remove `load_dotenv()` and class imports from package init.

**Expected gain:** `gaia --help` drops from ~1-2s to <200ms.

**Validation:** Re-run `python -X importtime` and startup profiling script.

### 2.2 JSON Library Swap (High Impact, Zero Code Change)

**Problem:** Python's `json` module is 10-40x slower than native alternatives.

**Fix:** Swap to `orjson` (Rust-backed) for dumps and `simdjson` for loads.

```python
# src/gaia/utils/json_utils.py
"""Fast JSON operations — drop-in replacements."""
try:
    import orjson

    def dumps(obj, **kwargs) -> str:
        # orjson returns bytes, decode to str for compatibility
        return orjson.dumps(obj, option=orjson.OPT_NON_STR_KEYS).decode()

    def loads(s):
        return orjson.loads(s)

except ImportError:
    # Fallback to stdlib
    import json
    dumps = json.dumps
    loads = json.loads
```

Then replace `json.loads`/`json.dumps` in hot paths:
- `agent.py:458, 494, 573, 743, 807, 824` (loads)
- `agent.py:1237, 1240, 1272, 1280, 1296` (dumps)
- `console.py:444-446` (dumps for display)

**Expected gain:** 5-10x faster JSON operations. Only matters if profiling confirms JSON takes >5% of non-LLM time.

**Validation:** Re-run JSON profiling with patched module.

### 2.3 Lazy Agent Initialization (High Impact, Medium Effort)

**Problem:** `ChatAgent.__init__` eagerly initializes RAG SDK (`chat/agent.py:127-143`) and auto-indexes documents (`chat/agent.py:174-186`).

**Fix:**

```python
# Lazy RAG — only initialize on first RAG tool call
@property
def rag(self):
    if self._rag is None:
        from gaia.rag.sdk import RAGSDK
        self._rag = RAGSDK(self.rag_config)
    return self._rag

# Lazy document indexing — index on first query, not on agent creation
def _ensure_documents_indexed(self):
    if not self._documents_indexed and self.rag_documents:
        self._index_documents(self.rag_documents)
        self._documents_indexed = True
```

Similarly for `ChatSDK` (`chat/sdk.py:79-101`) — create LLM client on first `send()`.

**Expected gain:** Agent creation drops from ~1-3s to <100ms. Model/RAG loading moves to first query.

**Validation:** Time `ChatAgent()` constructor before and after.

### 2.4 HTTP Connection Pooling (Medium Impact, Low Effort)

**Problem:** No persistent HTTP sessions visible in LLM client layer. Each API call may create a new TCP connection.

**Fix:** Use `httpx.Client` or `requests.Session` with keep-alive:

```python
# In LemonadeClient.__init__
self._session = requests.Session()
self._session.headers.update({"Connection": "keep-alive"})

# Reuse for all requests
response = self._session.post(url, json=payload, timeout=timeout)
```

**Expected gain:** 50-100ms saved per LLM call (TCP handshake + TLS negotiation eliminated after first request).

**Validation:** Profile consecutive LLM calls before/after.

### 2.5 Fast Health Check (Medium Impact, Low Effort)

**Problem:** Default request timeout is 900s (`lemonade_client.py:88`). Initial health check uses this timeout, making failure detection slow.

**Fix:** Separate health check timeout from generation timeout:

```python
HEALTH_CHECK_TIMEOUT = 3     # 3 seconds — server should respond instantly
GENERATION_TIMEOUT = 900     # 15 minutes — for long completions
```

**Expected gain:** Dead server detected in 3s instead of hanging.

### 2.6 Streaming by Default (High Perceived Impact)

**Problem:** `--stream` is opt-in (`cli.py:743`). Without it, users wait for the full response before seeing anything.

**Fix:** Make streaming the default for interactive commands. Add `--no-stream` for scripts/piping.

**Expected gain:** Time-to-first-token drops from "full response time" to <500ms.

### 2.7 Move Heavy Dependencies to Optional Groups (Installation Speed)

**Problem:** `transformers` and `accelerate` are in unconditional `install_requires` (`setup.py:81-82`).

**Fix:**

```python
# setup.py
install_requires = [
    "openai",
    "pydantic>=2.9.2",
    "python-dotenv",
    "aiohttp",
    "rich",
    "requests",
]

extras_require = {
    "local": [
        "transformers",
        "accelerate",
    ],
    "rag": [
        "sentence-transformers",
        "faiss-cpu",
    ],
    "audio": [
        "torch", "torchvision", "torchaudio",
        "openai-whisper",
        "kokoro>=0.3.4",
    ],
    "all": [...],  # everything
}
```

**Expected gain:** `pip install gaia` drops from ~2GB/60s to ~50MB/5s.

---

## Phase 3: Native Acceleration

Only pursue after Phase 1 profiling confirms these are actual bottlenecks.

### 3.1 Drop-in Library Swaps (No Custom Native Code)

These are "free" — just swap a Python library for a native-backed one:

| Operation | Current | Replacement | Speedup | Package |
|-----------|---------|-------------|---------|---------|
| JSON loads | `json.loads` | `orjson.loads` | 5-10x | [orjson](https://github.com/ijl/orjson) (Rust) |
| JSON dumps | `json.dumps` | `orjson.dumps` | 5-10x | [orjson](https://github.com/ijl/orjson) (Rust) |
| BM25 search | None (not implemented) | `tantivy-py` | N/A (new feature) | [tantivy-py](https://github.com/quickwit-oss/tantivy-py) (Rust) |
| Regex | `re` module | `regex` or `re2` | 2-5x | [google-re2](https://github.com/google/re2) (C++) |

### 3.2 Custom C++ Module: JSON Extraction (Conditional on Profiling)

**Only if** profiling shows the malformed-JSON fallback path (`agent.py:437-506`) takes >10ms per call and is hit frequently.

**Scope:** ~200 lines of C++ with nanobind bindings.

```cpp
// src/gaia/_native/json_extract.cpp
#include <string>
#include <nanobind/nanobind.h>

namespace nb = nanobind;

// Fast bracket-balanced JSON extraction from mixed text
std::string extract_json(const std::string& text) {
    // DFA-based scanner: find first '{' or '[', track nesting,
    // handle string literals (skip escaped quotes), return balanced substring
    // ...
}

NB_MODULE(_json_extract, m) {
    m.def("extract_json", &extract_json, "Extract JSON from mixed text");
}
```

**Build:** Add to `setup.py` as extension module or use `scikit-build-core` + `nanobind`.

**Expected gain:** 10x faster fallback parsing for malformed LLM output.

### 3.3 Custom Rust Module: Text Chunking (Conditional on Profiling)

**Only if** profiling shows `_split_text_into_chunks` (`rag/sdk.py:1000-1200`) takes >500ms for typical documents.

**Scope:** Rust module via PyO3 + maturin.

```rust
// src/gaia/_native/chunker/src/lib.rs
use pyo3::prelude::*;

#[pyfunction]
fn split_text_into_chunks(
    text: &str,
    chunk_size: usize,
    overlap: usize,
) -> Vec<String> {
    // Sentence boundary detection using Unicode segmentation
    // Semantic paragraph splitting
    // Token estimation
    // ...
}

#[pymodule]
fn _chunker(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(split_text_into_chunks, m)?)?;
    Ok(())
}
```

**Expected gain:** 10x faster document indexing. Only matters for large document sets.

### 3.4 Rust CLI Launcher (Conditional on Startup Requirements)

**Only if** Python interpreter startup (~100-200ms) is unacceptable after all lazy import optimizations.

**Scope:** Thin Rust binary that parses args, then calls Python for the specific subcommand.

```rust
// src/gaia-launcher/src/main.rs
fn main() {
    let args: Vec<String> = std::env::args().collect();

    // Handle --help, --version natively (instant)
    if args.contains(&"--help".to_string()) {
        print_help();  // static text, no Python
        return;
    }

    // For actual commands, invoke Python
    let status = std::process::Command::new("python")
        .args(&["-m", "gaia.cli"])
        .args(&args[1..])
        .status()
        .expect("Failed to launch Python");

    std::process::exit(status.code().unwrap_or(1));
}
```

**Expected gain:** `gaia --help` in <50ms. Marginal benefit if lazy imports already achieve <200ms.

### 3.5 Decision Framework for Native Porting

Before writing any native code, answer these questions:

```
1. Has profiling shown this is >5% of non-LLM wall time?
   No  → Don't port. Optimize Python first.
   Yes → Continue.

2. Is there an existing native-backed library (orjson, tantivy, re2)?
   Yes → Use it. Don't write custom code.
   No  → Continue.

3. Will the speedup be perceptible to users?
   No  → Don't port. Focus on perceived performance (streaming, UI).
   Yes → Continue.

4. Is the code stable (unlikely to change frequently)?
   No  → Keep in Python for iteration speed.
   Yes → Port to C++/Rust.

5. Choose binding technology:
   - Small, focused module → nanobind (C++) or PyO3 (Rust)
   - Complex data structures → PyO3 + maturin (Rust, safer memory)
   - Performance-critical hot loop → nanobind (lowest call overhead)
```

---

## Profiling Schedule

| Week | Activity | Output |
|------|----------|--------|
| 1 | Write profiling scripts (`util/profile_*.py`) | Profiling toolkit |
| 1 | Run baseline profiling on current code | `docs/plans/profile-results.md` |
| 2 | Implement Phase 2.1 (lazy imports) | Measured startup improvement |
| 2 | Implement Phase 2.2 (orjson swap) | Measured JSON improvement |
| 3 | Implement Phase 2.3 (lazy agent init) | Measured agent startup improvement |
| 3 | Re-run full profiling suite | Updated profile results |
| 4 | Evaluate Phase 3 necessity based on data | Go/no-go for native code |
| 4+ | Phase 3 work if justified by profiling | Native modules |

## Benchmarking Protocol

Every optimization must be validated:

1. **Before:** Run `util/profile_all.py`, save results
2. **Implement:** Make the change
3. **After:** Run `util/profile_all.py`, save results
4. **Compare:** Generate diff report showing improvement/regression
5. **Commit:** Include before/after numbers in commit message

```bash
# Example workflow
python util/profile_all.py --output before.json
# ... make changes ...
python util/profile_all.py --output after.json
python util/profile_compare.py before.json after.json
```

## Success Metrics

| Metric | Current (est.) | After Phase 2 | After Phase 3 |
|--------|---------------|---------------|---------------|
| `gaia --help` | ~1-2s | <200ms | <50ms (if Rust launcher) |
| `gaia chat` to first prompt | ~3-5s | <1s | <500ms |
| Time to first streamed token | N/A (buffered) | <500ms | <500ms |
| JSON ops per 10-step session | ~80 calls, ~500ms | ~80 calls, ~50ms | ~50ms |
| `pip install gaia` | ~60s / 2GB | ~5s / 50MB | ~5s / 50MB |
| Peak memory (chat agent) | TBD | TBD - 20% | TBD |
| RAG query latency | TBD | TBD | TBD |

*"TBD" values will be filled by Phase 1 profiling.*

## References

- [Python import profiling](https://docs.python.org/3/using/cmdline.html#cmdoption-X)
- [cProfile documentation](https://docs.python.org/3/library/profile.html)
- [tracemalloc documentation](https://docs.python.org/3/library/tracemalloc.html)
- [orjson — fast JSON for Python](https://github.com/ijl/orjson)
- [simdjson — SIMD JSON parsing](https://github.com/simdjson/simdjson)
- [nanobind — fast C++ bindings](https://github.com/wjakob/nanobind)
- [PyO3 — Rust Python bindings](https://github.com/PyO3/pyo3)
- [tantivy-py — Rust BM25 search](https://github.com/quickwit-oss/tantivy-py)
- [google-re2 — fast regex](https://github.com/google/re2)
