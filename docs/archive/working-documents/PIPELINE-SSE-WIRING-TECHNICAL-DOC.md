# PipelineEngine-to-SSE Wiring: Technical Architecture & Implementation Plan

**Date:** 2026-04-25
**Status:** Implementation Complete -- drain() fix, SSE hooks, canvas config forwarding, Phase 5 pending
**Branch:** `feature/pipeline-orchestration-v1`
**Author:** Technical Documentation Specialist

---

## 1. Overview

This document describes the Server-Sent Events (SSE) wiring architecture that connects the **PipelineEngine** -- a 4-phase recursive orchestrator (PLANNING, DEVELOPMENT, QUALITY, DECISION) -- to the GAIA Agent UI frontend. The PipelineEngine executes recursive iterative loops with quality gates and decision routing. Currently, events are emitted to `_PipelineSSEHandler` (a `queue.Queue`-based handler) and collected only after pipeline execution completes. This document defines the event flow, documents a critical bug, provides exact JSON payload schemas, and lays out an implementation plan for true real-time streaming.

---

## 2. Architecture

### 2.1 Event Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  PIPELINE ENGINE (async, recursive 4-phase)                                │
│                                                                             │
│  PipelineEngine.start()                                                    │
│    ├── PLANNING  → Domain analysis, task decomposition                      │
│    ├── DEVELOPMENT → Agent selection, tool execution                        │
│    ├── QUALITY   → Quality scoring, defect detection                        │
│    └── DECISION  → Continue / Loop-back / Complete / Fail                   │
│         │                                                                    │
│         └── (loop back if quality < threshold, iteration < max)             │
└───────────────────────────┬────────────────────────────────────────────────┘
                            │ sse_handler.emit(event_type, data)
                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  _PipelineSSEHandler (queue.Queue)                                          │
│  Location: src/gaia/ui/routers/pipeline.py:1301                            │
│                                                                             │
│  - emit(type, data) → puts JSON-serializable dict into queue.Queue          │
│  - drain(q) → generator that yields SSE-formatted strings                   │
│  - Thread-safe: events enqueued from ThreadPoolExecutor thread,             │
│    consumed from async generator in main event loop                         │
└───────────────────────────┬────────────────────────────────────────────────┘
                            │ queue.Queue.put() from executor thread
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  FastAPI SSE Router                                                         │
│  Location: src/gaia/ui/routers/pipeline.py:1101                            │
│                                                                             │
│  _stream_pipeline_events() -> AsyncGenerator[str, None]                     │
│    1. Yield "status/starting" event                                         │
│    2. Run _execute_and_record() in ThreadPoolExecutor (blocking call)       │
│    3. Drain buffered events AFTER executor completes (BUG: not yielded)     │
│    4. Yield "done" event with pipeline metadata                             │
│                                                                             │
│  Returns StreamingResponse with media_type="text/event-stream"              │
└───────────────────────────┬────────────────────────────────────────────────┘
                            │ yield f"data: {json.dumps(event)}\n\n"
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Frontend SSE Client                                                        │
│  Location: src/gaia/apps/webui/src/services/api.ts                         │
│                                                                             │
│  runPipelineStream() -- Fetch-based SSE parser                              │
│    - Reads response.body as ReadableStream                                  │
│    - Parses "data: {...}\n\n" frames                                       │
│    - Routes events via PIPELINE_EVENT_MAP to typed callbacks               │
└───────────────────────────┬────────────────────────────────────────────────┘
                            │ PipelineStreamCallbacks
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  Zustand Pipeline Store                                                     │
│  Location: src/gaia/apps/webui/src/stores/pipelineStore.ts                │
│                                                                             │
│  - onLoopBack → updates currentIteration, currentPhase                      │
│  - onQualityScore → appends to qualityScores array                          │
│  - onPhaseJump → updates currentPhase                                       │
│  - onIterationStart → increments currentIteration                           │
│  - onIterationEnd → no state change (logging only)                          │
│  - onDefectFound → appends event to event log                               │
│                                                                             │
│  State drives: PipelineRunner.tsx event log, stage progress, iteration badge│
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Component Reference Table

| Component | File Location | Role |
|-----------|---------------|------|
| `PipelineEngine` | `src/gaia/pipeline/engine.py` | 4-phase recursive orchestrator core |
| `PipelinePhase` | `src/gaia/pipeline/engine.py:63` | Phase constants: PLANNING, DEVELOPMENT, QUALITY, DECISION |
| `_PipelineSSEHandler` | `src/gaia/ui/routers/pipeline.py:1301` | Queue-based event buffer |
| `_execute_recursive_pipeline()` | `src/gaia/pipeline/orchestrator.py:562` | Bridges sync call to async engine, emits iteration_start |
| `_emit_sse()` | `src/gaia/pipeline/orchestrator.py:680` | Helper: puts events into handler's queue |
| `_stream_pipeline_events()` | `src/gaia/ui/routers/pipeline.py:1101` | FastAPI async generator for SSE |
| `runPipelineStream()` | `src/gaia/apps/webui/src/services/api.ts:699` | Frontend SSE client with callback routing |
| `PIPELINE_EVENT_MAP` | `src/gaia/apps/webui/src/services/api.ts:674` | Maps event type strings to callback keys |
| `usePipelineStore` | `src/gaia/apps/webui/src/stores/pipelineStore.ts` | Zustand store consuming SSE events |
| `PipelineRunner` | `src/gaia/apps/webui/src/components/pipeline/PipelineRunner.tsx` | React UI rendering events and progress |
| `StreamEventType` | `src/gaia/apps/webui/src/types/index.ts:242` | TypeScript union of all SSE event types |

---

## 3. Critical Bug: `drain()` Called Without Iteration

### 3.1 Bug Description

**Severity:** CRITICAL
**Location:** `src/gaia/ui/routers/pipeline.py`, line 1133

**The Bug:** The `drain()` generator is called but its result is never consumed. In Python, calling a generator function without iterating returns a generator object that is immediately garbage-collected because it is not assigned to any variable. The result is that **all buffered SSE events are silently discarded.**

**Buggy Code (line 1133):**
```python
# Stream any buffered output handler events
output_handler.drain(output_handler.event_queue)
```

This line evaluates to a generator object but does not iterate it. The `drain()` method (lines 1315-1322) yields SSE-formatted strings, but nothing consumes those yields.

### 3.2 The Fix

```python
# Stream any buffered output handler events
yield from output_handler.drain(output_handler.event_queue)
```

Using `yield from` delegates iteration to the enclosing async generator, ensuring each buffered event is emitted as an SSE data frame.

### 3.3 Impact

Without the fix:
- All `loop_back`, `quality_score`, `phase_jump`, `iteration_start`, `iteration_end`, and `defect_found` events emitted during pipeline execution are lost.
- The frontend receives only the `status/starting` event and the `done` event.
- The event log in PipelineRunner.tsx appears empty during execution.
- Quality score history, iteration counts, and loop-back tracking never update in the UI.
- The visual stage progress indicator never advances beyond "starting."

### 3.4 Verification

After applying the fix, the SSE stream for a 3-iteration pipeline should look like:

```
data: {"type":"status","status":"starting","message":"Initializing pipeline...","pipeline_id":"..."}

data: {"type":"iteration_start","message":"Pipeline starting (max 10 iterations)","iteration":1}

data: {"type":"loop_back","message":"Quality below threshold, looping back","target_phase":"DEVELOPMENT","iteration":2}

data: {"type":"quality_score","score":0.82,"threshold":0.90,"phase":"QUALITY"}

data: {"type":"iteration_end","iteration":1}

data: {"type":"iteration_start","message":"Starting iteration 2","iteration":2}

... (more loop cycles) ...

data: {"type":"done","pipeline_id":"...","status":"success","loop_count":3,"quality_scores":[0.82,0.88,0.92],"decisions":[...]}
```

---

## 4. Event Payload Schemas

### 4.1 Common Fields

All SSE events share these baseline fields:

```json
{
  "type": "<event_type>",
  "pipeline_id": "<uuid>",
  "timestamp": "<ISO 8601 datetime>"
}
```

### 4.2 Event Type: `iteration_start`

Emitted when a new pipeline iteration begins.

```json
{
  "type": "iteration_start",
  "pipeline_id": "recursive-a1b2c3d4e5f6",
  "timestamp": "2026-04-25T14:30:00.000Z",
  "message": "Pipeline starting (max 10 iterations)",
  "iteration": 1,
  "max_iterations": 10
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `string` | yes | Literal: `"iteration_start"` |
| `pipeline_id` | `string` | yes | UUID of the pipeline run |
| `timestamp` | `string` | yes | ISO 8601 UTC datetime |
| `message` | `string` | yes | Human-readable description |
| `iteration` | `integer` | yes | 1-based iteration number |
| `max_iterations` | `integer` | no | Maximum iterations configured |

### 4.3 Event Type: `iteration_end`

Emitted when an iteration completes (regardless of loop-back or exit).

```json
{
  "type": "iteration_end",
  "pipeline_id": "recursive-a1b2c3d4e5f6",
  "timestamp": "2026-04-25T14:31:00.000Z",
  "message": "Iteration 1 complete",
  "iteration": 1,
  "phase": "DECISION"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `string` | yes | Literal: `"iteration_end"` |
| `iteration` | `integer` | yes | The iteration that completed |
| `phase` | `string` | no | Phase where iteration ended (e.g., `"DECISION"`) |

### 4.4 Event Type: `phase_enter`

Emitted when the engine enters a new pipeline phase.

```json
{
  "type": "phase_enter",
  "pipeline_id": "recursive-a1b2c3d4e5f6",
  "timestamp": "2026-04-25T14:30:05.000Z",
  "message": "Entering PLANNING phase",
  "phase": "PLANNING",
  "iteration": 1
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `string` | yes | Literal: `"phase_enter"` |
| `phase` | `string` | yes | One of: `PLANNING`, `DEVELOPMENT`, `QUALITY`, `DECISION` |
| `iteration` | `integer` | yes | Current iteration number |

### 4.5 Event Type: `phase_exit`

Emitted when the engine exits a pipeline phase.

```json
{
  "type": "phase_exit",
  "pipeline_id": "recursive-a1b2c3d4e5f6",
  "timestamp": "2026-04-25T14:30:30.000Z",
  "message": "Completed PLANNING phase",
  "phase": "PLANNING",
  "iteration": 1,
  "duration_ms": 25000
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `string` | yes | Literal: `"phase_exit"` |
| `phase` | `string` | yes | The phase that completed |
| `iteration` | `integer` | yes | Current iteration number |
| `duration_ms` | `integer` | no | Phase duration in milliseconds |

### 4.6 Event Type: `quality_evaluated`

Emitted when the QualityScorer completes evaluation.

```json
{
  "type": "quality_evaluated",
  "pipeline_id": "recursive-a1b2c3d4e5f6",
  "timestamp": "2026-04-25T14:30:35.000Z",
  "message": "Quality evaluation complete",
  "phase": "QUALITY",
  "iteration": 1,
  "score": 0.82,
  "threshold": 0.90,
  "passed": false,
  "dimensions": {
    "completeness": 0.85,
    "correctness": 0.78,
    "coherence": 0.83
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `string` | yes | Literal: `"quality_evaluated"` |
| `phase` | `string` | yes | Always `"QUALITY"` |
| `iteration` | `integer` | yes | Current iteration |
| `score` | `number` | yes | Quality score in range [0.0, 1.0] |
| `threshold` | `number` | yes | Configured quality threshold |
| `passed` | `boolean` | yes | Whether score >= threshold |
| `dimensions` | `object` | no | Per-dimension scores |

### 4.7 Event Type: `quality_score`

Legacy/alias event (currently used in frontend). Maps to `StreamEventType` in `types/index.ts:261`.

```json
{
  "type": "quality_score",
  "pipeline_id": "recursive-a1b2c3d4e5f6",
  "timestamp": "2026-04-25T14:30:35.000Z",
  "message": "Quality score: 0.82/0.90",
  "quality_score": 0.82,
  "threshold": 0.90,
  "iteration": 1
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `string` | yes | Literal: `"quality_score"` |
| `quality_score` | `number` | yes | Score in [0.0, 1.0] |
| `threshold` | `number` | yes | Configured threshold |
| `iteration` | `integer` | yes | Current iteration |

### 4.8 Event Type: `defect_discovered` (maps to frontend `defect_found`)

Emitted when the quality phase detects a defect.

```json
{
  "type": "defect_discovered",
  "pipeline_id": "recursive-a1b2c3d4e5f6",
  "timestamp": "2026-04-25T14:30:36.000Z",
  "message": "Defect: missing error handling in module X",
  "phase": "QUALITY",
  "iteration": 1,
  "defects": [
    {
      "type": "missing_coverage",
      "severity": "high",
      "description": "No unit tests for error handling in module X"
    },
    {
      "type": "code_smell",
      "severity": "medium",
      "description": "Long function (>100 lines) in processing pipeline"
    }
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `string` | yes | Literal: `"defect_discovered"` (backend), `"defect_found"` (frontend type) |
| `phase` | `string` | yes | Always `"QUALITY"` |
| `iteration` | `integer` | yes | Current iteration |
| `defects` | `array` | yes | Array of defect objects |
| `defects[].type` | `string` | yes | Defect category (e.g., `missing_coverage`, `code_smell`) |
| `defects[].severity` | `string` | yes | `critical`, `high`, `medium`, `low` |
| `defects[].description` | `string` | yes | Human-readable defect description |

### 4.9 Event Type: `decision_made`

Emitted when the DecisionEngine makes a routing decision.

```json
{
  "type": "decision_made",
  "pipeline_id": "recursive-a1b2c3d4e5f6",
  "timestamp": "2026-04-25T14:30:37.000Z",
  "message": "Decision: LOOP_BACK to DEVELOPMENT",
  "phase": "DECISION",
  "iteration": 1,
  "decision": {
    "type": "LOOP_BACK",
    "reason": "Quality score 0.82 below threshold 0.90",
    "target_phase": "DEVELOPMENT"
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `string` | yes | Literal: `"decision_made"` |
| `phase` | `string` | yes | Always `"DECISION"` |
| `iteration` | `integer` | yes | Current iteration |
| `decision` | `object` | yes | Decision details |
| `decision.type` | `string` | yes | One of: `CONTINUE`, `LOOP_BACK`, `PAUSE`, `COMPLETE`, `FAIL` |
| `decision.reason` | `string` | yes | Human-readable reasoning |
| `decision.target_phase` | `string` | no | Target phase (for `LOOP_BACK`) |

### 4.10 Event Type: `loop_back`

Emitted when the pipeline loops back to a prior phase.

```json
{
  "type": "loop_back",
  "pipeline_id": "recursive-a1b2c3d4e5f6",
  "timestamp": "2026-04-25T14:30:38.000Z",
  "message": "Looping back to DEVELOPMENT phase",
  "target_phase": "DEVELOPMENT",
  "source_phase": "DECISION",
  "iteration": 2,
  "loop_count": 1,
  "reason": "Quality below threshold"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `string` | yes | Literal: `"loop_back"` |
| `target_phase` | `string` | yes | Phase to loop back to |
| `source_phase` | `string` | no | Phase where loop was triggered |
| `iteration` | `integer` | yes | The NEW iteration number (incremented) |
| `loop_count` | `integer` | yes | Total loop count so far |
| `reason` | `string` | no | Reason for loop-back |

### 4.11 Event Type: `phase_jump`

Emitted when the pipeline skips ahead to a non-sequential phase.

```json
{
  "type": "phase_jump",
  "pipeline_id": "recursive-a1b2c3d4e5f6",
  "timestamp": "2026-04-25T14:30:40.000Z",
  "message": "Jumping to QUALITY phase",
  "target_phase": "QUALITY",
  "source_phase": "DEVELOPMENT",
  "iteration": 1,
  "reason": "Fast-track: development artifacts already validated"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `string` | yes | Literal: `"phase_jump"` |
| `target_phase` | `string` | yes | Phase being jumped to |
| `source_phase` | `string` | no | Phase where jump originated |
| `iteration` | `integer` | yes | Current iteration |
| `reason` | `string` | no | Reason for the jump |

### 4.12 Event Type: `status`

Standard lifecycle status events.

```json
{
  "type": "status",
  "pipeline_id": "recursive-a1b2c3d4e5f6",
  "timestamp": "2026-04-25T14:30:00.000Z",
  "status": "starting",
  "message": "Initializing pipeline..."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `string` | yes | Literal: `"status"` |
| `status` | `string` | yes | One of: `starting`, `running`, `completed`, `failed`, `blocked` |
| `message` | `string` | yes | Human-readable status message |

### 4.13 Event Type: `done`

Final completion event with aggregate metadata.

```json
{
  "type": "done",
  "pipeline_id": "recursive-a1b2c3d4e5f6",
  "timestamp": "2026-04-25T14:35:00.000Z",
  "status": "success",
  "loop_count": 3,
  "quality_scores": [0.82, 0.88, 0.92],
  "decisions": [
    {"type": "LOOP_BACK", "reason": "Quality below threshold", "target_phase": "DEVELOPMENT"},
    {"type": "LOOP_BACK", "reason": "Quality below threshold", "target_phase": "DEVELOPMENT"},
    {"type": "COMPLETE", "reason": "Quality threshold met"}
  ],
  "result": {
    "pipeline_status": "success",
    "stage_results": {...}
  }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `string` | yes | Literal: `"done"` |
| `status` | `string` | yes | `success` or `failed` |
| `loop_count` | `integer` | yes | Total number of loop iterations |
| `quality_scores` | `array` | yes | Array of quality scores from each iteration |
| `decisions` | `array` | no | Array of decision objects |
| `result` | `object` | no | Full pipeline result dict |

### 4.14 Event Type: `error`

Emitted on pipeline-level failures.

```json
{
  "type": "error",
  "pipeline_id": "recursive-a1b2c3d4e5f6",
  "timestamp": "2026-04-25T14:32:00.000Z",
  "content": "Lemonade server connection refused",
  "phase": "DEVELOPMENT",
  "iteration": 2
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | `string` | yes | Literal: `"error"` |
| `content` | `string` | yes | Error message |
| `phase` | `string` | no | Phase where error occurred |
| `iteration` | `integer` | no | Iteration where error occurred |

### 4.15 Frontend Event Type Mapping

The frontend (`StreamEventType` in `types/index.ts:242`) uses slightly different names for some events. The backend-to-frontend mapping:

| Backend Event Type | Frontend StreamEventType | Handler Callback |
|--------------------|-------------------------|------------------|
| `iteration_start` | `iteration_start` | `onIterationStart` |
| `iteration_end` | `iteration_end` | `onIterationEnd` |
| `loop_back` | `loop_back` | `onLoopBack` |
| `quality_score` | `quality_score` | `onQualityScore` |
| `phase_jump` | `phase_jump` | `onPhaseJump` |
| `defect_discovered` | `defect_found` | `onDefectFound` |
| `decision_made` | (no dedicated handler) | Falls through to generic handling |
| `phase_enter` | (no dedicated handler) | Falls through to generic handling |
| `phase_exit` | (no dedicated handler) | Falls through to generic handling |
| `status` | `status` | `onStatus` |
| `done` | `done` | `onDone` |
| `error` | `error` | `onError` |

---

## 5. Implementation Plan

### Phase 1: Critical Bug Fix (Priority: Immediate)

**Step 1.1 -- Fix drain() bug**

File: `src/gaia/ui/routers/pipeline.py`, line 1133

```python
# Before (BUG):
output_handler.drain(output_handler.event_queue)

# After (FIX):
yield from output_handler.drain(output_handler.event_queue)
```

**Step 1.2 -- Add pipeline_id to all emitted events**

Ensure every `_emit_sse()` call in `_execute_recursive_pipeline()` includes `pipeline_id`:

```python
_emit_sse(sse_handler, "iteration_start", {
    "pipeline_id": pipeline_id,
    "message": f"Pipeline starting (max {max_iterations} iterations)",
    "iteration": 1,
})
```

Currently, `pipeline_id` is only included in the `status` and `done` events. All recursive events (loop_back, quality_score, etc.) should carry it for frontend correlation.

**Step 1.3 -- Add timestamp to all events**

Add ISO 8601 timestamps at emission time:

```python
from datetime import datetime, timezone

def _emit_sse(handler, event_type: str, data: Dict[str, Any]) -> None:
    """Emit an SSE event through the handler's event queue."""
    try:
        event = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        handler.event_queue.put(event)
    except Exception as e:
        logger.warning(f"Failed to emit SSE event {event_type}: {e}")
```

### Phase 2: Canvas Config Forwarding (Priority: HIGH)

**Step 2.1 -- Add canvas config fields to PipelineEngine initialization**

The PipelineEngine must receive canvas-defined loops, supervisors, and gates. Add forwarding at 5 points:

1. `_execute_recursive_pipeline()` -- pass `canvas_loops`, `canvas_supervisors`, `canvas_gates` from template to `PipelineConfig`
2. `PipelineConfig` dataclass -- add fields:
   ```python
   canvas_loops: List[Dict] = None
   canvas_supervisors: List[Dict] = None
   canvas_gates: List[Dict] = None
   ```
3. `PipelineEngine.initialize()` -- forward canvas config to LoopManager and DecisionEngine
4. `LoopManager` -- accept canvas-defined loop configurations
5. `DecisionEngine` -- accept canvas-defined supervisor and gate configurations

**Step 2.2 -- Update template schema**

The `PipelineTemplate` in `types/index.ts:353` already includes `canvas_loops` and `canvas_supervisors`. Verify that the backend template service forwards these fields through to `_execute_recursive_pipeline()`.

### Phase 3: Phase Name Mapping (Priority: MEDIUM)

See Section 7 for the complete mapping. Implement a translation layer:

```python
# In src/gaia/ui/routers/pipeline.py or a new mapping module

PHASE_TO_STAGE_MAP = {
    "PLANNING": "domain_analysis",       # Partial mapping -- see Section 7
    "DEVELOPMENT": "pipeline_execution",  # Partial mapping
    "QUALITY": "gap_detection",          # Partial mapping
    "DECISION": "pipeline_execution",    # Partial mapping
}
```

Emit both `phase` (engine-native) and `stage` (UI-native) in events to allow the frontend to render the correct stage progress.

### Phase 4: Real-time Streaming (Priority: MEDIUM)

See Section 7 for analysis. The recommended approach is the dual-channel strategy.

### Phase 5: Testing & Validation (Priority: HIGH)

See Section 8 for the testing strategy.

---

## 6. Real-time Streaming Considerations

### 6.1 Current Architecture: Buffered Delivery

The current implementation executes the pipeline in a `ThreadPoolExecutor` thread and buffers all events in a `queue.Queue`. After the executor completes, the `drain()` generator yields all buffered events in a burst.

**Problem:** This is not true real-time streaming. Events accumulate during the multi-second/minute pipeline execution and are delivered all at once after completion. The user sees a long pause followed by a burst of events.

**Root cause:** The async generator `_stream_pipeline_events()` calls `loop.run_in_executor()` which blocks until the entire pipeline completes. The `drain()` call happens AFTER the executor returns. There is no mechanism for the async generator to yield events while the executor is still running.

### 6.2 Approach A: Thread-Safe Async Queue (Recommended)

Replace `queue.Queue` with `asyncio.Queue` and use a bridge between the thread and the async event loop.

**Architecture:**
```
ThreadPoolExecutor thread                     Main async event loop
      │                                              │
      │ _emit_sse()                                  │
      │   └→ thread_safe_queue.put(event)            │
      │        (threading-safe wrapper)              │
      │                                              │
      │                                   async for event in poll_queue():
      │                                        yield f"data: {json.dumps(event)}"
      │                                        (yields immediately as events arrive)
```

**Implementation sketch:**
```python
class _AsyncSSEBridge:
    """Bridge between synchronous executor thread and async SSE generator."""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False

    def emit(self, event_type: str, data: dict):
        """Call from ANY thread (thread-safe via run_coroutine_threadsafe)."""
        event = {"type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), **data}
        try:
            loop = asyncio.get_event_loop()
            asyncio.run_coroutine_threadsafe(self._queue.put(event), loop)
        except Exception:
            pass  # Silent drop if event loop unavailable

    async def drain(self):
        """Async generator -- yields events as they arrive."""
        while not self._closed or not self._queue.empty():
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.TimeoutError:
                continue  # Check _closed flag
        # Final drain of any remaining events
        while not self._queue.empty():
            try:
                event = self._queue.get_nowait()
                yield f"data: {json.dumps(event)}\n\n"
            except asyncio.QueueEmpty:
                break

    def close(self):
        """Signal that no more events will be emitted."""
        self._closed = True
```

**Usage in `_stream_pipeline_events()`:**
```python
async def _stream_pipeline_events() -> AsyncGenerator[str, None]:
    bridge = _AsyncSSEBridge()

    # Start executor in background
    executor_future = loop.run_in_executor(
        None,
        _execute_and_record,
        pipeline_id, ..., bridge,  # pass bridge instead of output_handler
    )

    # Yield events as they arrive from executor thread
    async for sse_frame in bridge.drain():
        yield sse_frame

    # Wait for executor to complete
    result = await executor_future
    bridge.close()

    # Yield final done event
    yield f"data: {json.dumps(done_payload)}\n\n"
```

### 6.3 Approach B: Multiprocessing Queue (Alternative)

If the `run_in_executor` uses a process pool instead of a thread pool, use `multiprocessing.Queue` or a named pipe. This is more complex and only necessary if the pipeline execution requires process isolation.

### 6.4 Approach C: Keep Buffered (Fallback)

If the real-time streaming infrastructure cannot be built immediately, the buffered approach is acceptable for MVP. The drain() bug fix is the minimum viable change. Users will see events after pipeline completion rather than during, which is consistent with many CI/CD systems that show build logs only after completion.

### 6.5 Recommendation

| Approach | Effort | User Experience | Thread Safety | Recommendation |
|----------|--------|----------------|---------------|----------------|
| A: Async Queue Bridge | Medium | True real-time | Requires careful implementation | **Recommended** |
| B: Multiprocessing | High | True real-time | Complex | Only if process isolation needed |
| C: Buffered (current) | Low (fix drain() only) | Post-execution burst | Already safe | **MVP minimum** |

---

## 7. Phase Name Mapping

### 7.1 The Mismatch

| System | Phases/Stages | Names |
|--------|--------------|-------|
| **PipelineEngine** (backend) | 4 phases | `PLANNING`, `DEVELOPMENT`, `QUALITY`, `DECISION` |
| **PipelineRunner UI** (frontend) | 5 stages | `domain_analysis`, `workflow_modeling`, `loom_building`, `gap_detection`, `pipeline_execution` |

The PipelineEngine's 4-phase recursive model does not have a 1:1 mapping with the UI's 5-stage pipeline view. The UI stages come from the original `PipelineOrchestrator` class (`src/gaia/pipeline/orchestrator.py:40`), which implements a 5-stage linear flow. The `PipelineEngine` implements a 4-phase recursive loop.

### 7.2 Proposed Mapping

| Engine Phase | Primary UI Stage | Secondary UI Stage | Rationale |
|--------------|-----------------|-------------------|-----------|
| `PLANNING` | `domain_analysis` | `workflow_modeling` | Planning encompasses domain analysis and workflow modeling |
| `DEVELOPMENT` | `loom_building` | `pipeline_execution` | Development covers topology building and agent execution |
| `QUALITY` | `gap_detection` | (n/a) | Quality evaluation identifies gaps between current and target state |
| `DECISION` | `pipeline_execution` | (n/a) | Decision routing determines execution continuation |

### 7.3 Implementation

Add the mapping to the backend SSE handler and include `stage` in all events:

```python
PHASE_TO_STAGE_MAP = {
    "PLANNING": ["domain_analysis", "workflow_modeling"],
    "DEVELOPMENT": ["loom_building", "pipeline_execution"],
    "QUALITY": ["gap_detection"],
    "DECISION": ["pipeline_execution"],
}

def _emit_sse_with_stage(handler, event_type, data, phase):
    stages = PHASE_TO_STAGE_MAP.get(phase, ["pipeline_execution"])
    data["stage"] = stages[0]        # Primary stage for progress indicator
    data["stages"] = stages          # All mapped stages
    _emit_sse(handler, event_type, data)
```

Update the frontend `inferStage()` in `pipelineStore.ts:55` to also check for a `stage` field in events:

```typescript
function inferStage(event: PipelineEvent): PipelineExecution['currentStage'] {
    // Priority 1: use explicit stage field from event
    if ((event as any).stage) return (event as any).stage;
    // Priority 2: fallback to message-based inference
    if (!event.message) return undefined;
    for (const [key, stage] of Object.entries(STAGE_LABELS)) {
        if (event.message.includes(key)) return stage;
    }
    return 'pipeline_execution';
}
```

### 7.4 Long-term Resolution

The 5-stage UI model represents a different abstraction than the 4-phase engine. The long-term fix is one of:

1. **Unify to 4 phases everywhere** -- Rename UI stages to PLANNING/DEVELOPMENT/QUALITY/DECISION and remove the 5-stage concept.
2. **Extend engine to 5 phases** -- Split the engine to match the UI's 5-stage model (requires engine refactoring).
3. **Maintain mapping layer** -- Keep the translation as a persistent adapter, documenting the conceptual gap.

Recommendation: Option 3 (mapping layer) for the current sprint, with a future spike to evaluate options 1 or 2.

---

## 8. Testing Strategy

### 8.1 End-to-End SSE Event Flow Verification

**Test Objective:** Verify that every event emitted by the PipelineEngine is delivered to the frontend SSE client and correctly rendered in the PipelineRunner UI.

**Test Environment:**
- Backend: `gaia ui server` running on `localhost:4200`
- Frontend: `npm run dev` on `localhost:5173`
- Browser: Chrome with DevTools Network tab open

### 8.2 Test Cases

**TC-1: Drain Bug Fix Verification**

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Start pipeline with a task that triggers 2+ iterations | Pipeline runs successfully |
| 2 | Open Chrome DevTools Network tab, filter by `event-stream` | SSE connection visible |
| 3 | Observe SSE event stream | Multiple `data:` frames visible, not just `starting` and `done` |
| 4 | Check for `iteration_start` events | At least 1 `iteration_start` event |
| 5 | Check for `loop_back` events (if quality < threshold) | `loop_back` events present with correct `target_phase` |
| 6 | Check for `quality_score` events | At least 1 `quality_score` event with numeric score |

**TC-2: Event Payload Schema Validation**

For each event type in the stream, validate JSON structure:

```python
# Test: Validate SSE event payloads
import json
import re

def parse_sse_stream(stream_text: str) -> list[dict]:
    """Parse raw SSE stream text into list of event dicts."""
    events = []
    for line in stream_text.split('\n'):
        if line.startswith('data: '):
            payload = json.loads(line[6:])
            events.append(payload)
    return events

def test_iteration_start_schema():
    events = parse_sse_stream(get_test_stream())
    iter_starts = [e for e in events if e['type'] == 'iteration_start']
    assert len(iter_starts) >= 1
    event = iter_starts[0]
    assert 'type' in event
    assert 'pipeline_id' in event
    assert 'iteration' in event
    assert isinstance(event['iteration'], int)
    assert event['iteration'] >= 1
    assert 'timestamp' in event
    # Validate ISO 8601 format
    assert re.match(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', event['timestamp'])
```

**TC-3: Quality Score Progression**

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Run pipeline with task that loops 3 times | 3 quality scores emitted |
| 2 | Inspect `quality_score` events | Scores are monotonically increasing (typical) |
| 3 | Verify last score >= threshold | Final iteration passes quality gate |
| 4 | Check `done` event contains `quality_scores` array | Array matches individual event scores |

**TC-4: Frontend State Consistency**

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Run pipeline and watch PipelineRunner UI | Event log populates with events |
| 2 | Check iteration badge | Badge shows `Iteration 2` during second loop |
| 3 | Check quality score display | Score shown as percentage (e.g., `82%`) |
| 4 | Check stage progress indicator | Dots fill as pipeline progresses through stages |
| 5 | After completion | All events visible, loop count matches |

**TC-5: Error Handling**

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Trigger pipeline with invalid configuration | Pipeline fails gracefully |
| 2 | Check SSE stream for `error` event | `error` event with descriptive `content` field |
| 3 | Check frontend error display | PipelineRunner shows error state |
| 4 | Verify cleanup | Session lock released, semaphore released |

**TC-6: Concurrency**

| Step | Action | Expected Result |
|------|--------|-----------------|
| 1 | Start pipeline run A | Runs normally |
| 2 | Start pipeline run B (different session) | Runs concurrently, events don't mix |
| 3 | Start pipeline run C (same session as A) | Blocked or queued (session lock prevents duplicate) |
| 4 | Verify event isolation | Each SSE stream contains only its own pipeline's events |

### 8.3 Unit Tests for SSE Handler

```python
# tests/pipeline/test_sse_handler.py

import json
import queue
import pytest
from gaia.ui.routers.pipeline import _PipelineSSEHandler

class TestPipelineSSEHandler:
    def test_emit_and_drain(self):
        handler = _PipelineSSEHandler()
        handler.emit("iteration_start", {"iteration": 1, "message": "Starting"})

        events = list(handler.drain(handler.event_queue))
        assert len(events) == 1
        payload = json.loads(events[0].replace("data: ", "").strip())
        assert payload["type"] == "iteration_start"
        assert payload["iteration"] == 1

    def test_drain_empty_queue(self):
        handler = _PipelineSSEHandler()
        events = list(handler.drain(handler.event_queue))
        assert len(events) == 0

    def test_multiple_events_drain_order(self):
        handler = _PipelineSSEHandler()
        handler.emit("iteration_start", {"iteration": 1})
        handler.emit("quality_score", {"quality_score": 0.82})
        handler.emit("loop_back", {"target_phase": "DEVELOPMENT", "iteration": 2})

        events = list(handler.drain(handler.event_queue))
        assert len(events) == 3
        payloads = [json.loads(e.replace("data: ", "").strip()) for e in events]
        assert payloads[0]["type"] == "iteration_start"
        assert payloads[1]["type"] == "quality_score"
        assert payloads[2]["type"] == "loop_back"

    def test_sse_format(self):
        handler = _PipelineSSEHandler()
        handler.emit("status", {"status": "starting"})

        events = list(handler.drain(handler.event_queue))
        assert events[0].startswith("data: ")
        assert events[0].endswith("\n\n")
```

### 8.4 Integration Test: SSE Stream Parser

```python
# tests/pipeline/test_sse_stream_integration.py

import json
import pytest
from unittest.mock import patch, MagicMock

@pytest.mark.asyncio
async def test_sse_stream_contains_recursive_events():
    """Verify that _stream_pipeline_events yields recursive events after drain fix."""
    from gaia.ui.routers.pipeline import _PipelineSSEHandler

    handler = _PipelineSSEHandler()

    # Simulate events that would be emitted during pipeline execution
    handler.emit("iteration_start", {"iteration": 1, "message": "Starting pipeline"})
    handler.emit("quality_score", {"quality_score": 0.82, "threshold": 0.90})
    handler.emit("loop_back", {"target_phase": "DEVELOPMENT", "iteration": 2})

    # Verify drain yields all events (the fix)
    drained = list(handler.drain(handler.event_queue))
    assert len(drained) == 3, "drain() must yield all buffered events"

    # Verify each is valid SSE format
    for frame in drained:
        assert frame.startswith("data: ")
        assert frame.endswith("\n\n")
        payload_str = frame[6:-2]  # Strip "data: " and "\n\n"
        payload = json.loads(payload_str)
        assert "type" in payload
```

---

## 9. Resilience Patterns Reference

The following resilience patterns (implemented in `src/gaia/resilience/`) are relevant to the SSE wiring:

| Pattern | File | Public Methods | Use Case for SSE |
|---------|------|---------------|-----------------|
| `CircuitBreaker` | `src/gaia/resilience/circuit_breaker.py` | `call()`, `get_statistics()`, `record_success()`, `record_failure()`, `isolate()` (static decorator) | Protect SSE connection against repeated emitter failures |
| `Bulkhead` | `src/gaia/resilience/bulkhead.py` | `execute()`, `get_statistics()`, `isolate()` (static decorator) | Isolate SSE event emission from pipeline execution resource contention |
| `Retry` / `retry` | `src/gaia/resilience/retry.py` | `get_statistics()`, `with_backoff()` (static decorator), `@retry` decorator | Retry failed SSE queue operations with exponential backoff |

**Recommended usage in SSE wiring:**
```python
from gaia.resilience import CircuitBreaker, Retry

# Circuit breaker for SSE queue operations
_sse_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=10)

def _emit_sse(handler, event_type, data):
    @_sse_breaker.call
    def _do_emit():
        event = {"type": event_type, **data}
        handler.event_queue.put(event)
    _do_emit()

# Retry for drain operations
@Retry.with_backoff(max_retries=3, base_delay=0.1, max_delay=2.0)
def _drain_with_retry(bridge):
    async for frame in bridge.drain():
        yield frame
```

---

## 10. File Index

| File | Absolute Path | Role |
|------|--------------|------|
| Pipeline Router | `C:/Users/antmi/gaia/src/gaia/ui/routers/pipeline.py` | SSE endpoint, `_PipelineSSEHandler`, drain bug |
| Pipeline Engine | `C:/Users/antmi/gaia/src/gaia/pipeline/engine.py` | 4-phase `PipelineEngine`, `PipelinePhase` |
| Orchestrator | `C:/Users/antmi/gaia/src/gaia/pipeline/orchestrator.py` | `_execute_recursive_pipeline()`, `_emit_sse()` |
| Frontend API Client | `C:/Users/antmi/gaia/src/gaia/apps/webui/src/services/api.ts` | `runPipelineStream()`, `PIPELINE_EVENT_MAP` |
| Pipeline Store | `C:/Users/antmi/gaia/src/gaia/apps/webui/src/stores/pipelineStore.ts` | Zustand store, SSE event handlers |
| Pipeline Runner UI | `C:/Users/antmi/gaia/src/gaia/apps/webui/src/components/pipeline/PipelineRunner.tsx` | Event log rendering, stage progress |
| Frontend Types | `C:/Users/antmi/gaia/src/gaia/apps/webui/src/types/index.ts` | `StreamEventType`, `PipelineEvent`, `PipelineExecution` |
| Circuit Breaker | `C:/Users/antmi/gaia/src/gaia/resilience/circuit_breaker.py` | Circuit breaker with statistics |
| Bulkhead | `C:/Users/antmi/gaia/src/gaia/resilience/bulkhead.py` | Concurrency isolation |
| Retry | `C:/Users/antmi/gaia/src/gaia/resilience/retry.py` | Exponential backoff retry |
| Resilience Exports | `C:/Users/antmi/gaia/src/gaia/resilience/__init__.py` | Module exports |
| NexusService | `C:/Users/antmi/gaia/src/gaia/state/nexus.py` | Unified state management (potential future event bus) |

---

## Appendix A: SSE Wire Format Reference

Server-Sent Events use plain-text frames over HTTP with `Content-Type: text/event-stream`. Each event is a `data:` line followed by a blank line:

```
data: {"type":"iteration_start","iteration":1,"message":"Starting pipeline"}

data: {"type":"quality_score","quality_score":0.82,"threshold":0.90}

data: {"type":"loop_back","target_phase":"DEVELOPMENT","iteration":2}
```

Required HTTP headers for SSE:
```
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no        # Disable nginx buffering
```

These headers are already set in `pipeline.py:1181-1184`.

---

## Appendix B: Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-04-25 | Initial document creation | Technical Documentation Specialist |
| 2026-04-26 | Implementation complete: Phases 1-4 implemented. 76 new tests passing (16 drain + 32 hooks + 28 regression). canvas config try/except defensive wrapping added. Phase 5 (real-time async bridge) deferred. | Senior Developer, Quality Reviewer, Testing Specialist |

---

## 11. Implementation Status

### Completed (2026-04-26)

| Phase | Status | Tests |
|-------|--------|-------|
| Phase 1: drain() bug fix | Done | 16 tests passing |
| Phase 2: SSE hooks | Done | 32 tests passing |
| Phase 3: Canvas config forwarding | Done | Verified 7-link chain |
| Phase 4: Frontend types | Done | TypeScript compiles clean |
| Phase 5: Real-time async bridge | Deferred | - |

### Files Modified

| File | Change |
|------|--------|
| `src/gaia/ui/routers/pipeline.py` | drain() bug fix: `for event_str in output_handler.drain(...): yield event_str` |
| `src/gaia/pipeline/sse_hooks.py` | New file: 5 SSE hook classes + factory |
| `src/gaia/pipeline/engine.py` | SSE hook registration in initialize(), canvas config try/except wrapping |
| `src/gaia/pipeline/orchestrator.py` | canvas_loops/canvas_supervisors passed through _execute_recursive_pipeline |
| `src/gaia/ui/schemas/pipeline_templates.py` | canvas_loops/canvas_supervisors added to PipelineRunRequest |
| `src/gaia/apps/webui/src/types/index.ts` | canvas_loops/canvas_supervisors added to PipelineRunRequest interface |
| `src/gaia/apps/webui/src/components/pipeline/PipelineRunner.tsx` | handleRun collects canvas config and passes in request |
| `tests/pipeline/test_sse_drain_fix.py` | New file: 16 drain() tests |
| `tests/pipeline/test_sse_hooks.py` | New file: 32 SSE hooks tests |

### Known Issues

1. **Pre-existing**: `test_capability_migration.py::test_yaml_structure_preserved` fails (54 capability YAML files missing id/name/capabilities fields) - unrelated to SSE wiring
2. **Deferred**: Phase 5 real-time async bridge - current buffered delivery is acceptable for MVP; true real-time streaming requires async queue bridge between executor thread and async event loop
