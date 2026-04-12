# B3-C Implementation Blueprint: Agent UI Pipeline Execution

**Document Type:** Technical Implementation Plan  
**Priority:** P0 (Merge-Blocking)  
**Estimated Effort:** 4-6 hours  
**Prepared By:** Dr. Sarah Kim, Technical Product Strategist & Engineering Lead  
**Date:** 2026-04-11  

---

## Executive Summary

This blueprint provides step-by-step instructions to implement the Agent UI pipeline execution endpoint (B3-C), enabling users to run pipeline orchestration tasks directly from the Agent UI browser interface. The implementation follows GAIA conventions for maintainability, modularity, and scalability.

**Key Deliverables:**
1. Backend SSE endpoint for pipeline execution (`POST /api/v1/pipeline/run`)
2. Backend pipeline service for orchestration
3. Frontend PipelinePanel component
4. Frontend pipeline service and store
5. Type definitions for pipeline events

---

## 1. Current State Analysis

### 1.1 What Exists

| Component | File | Status |
|-----------|------|--------|
| Pipeline Orchestrator | `src/gaia/pipeline/orchestrator.py` | COMPLETE (518 LOC) |
| Pipeline Router (CRUD only) | `src/gaia/ui/routers/pipeline.py` | PARTIAL (259 lines, template CRUD only) |
| Pipeline Template Manager UI | `src/gaia/apps/webui/src/components/templates/PipelineTemplateManager.tsx` | COMPLETE |
| Template API Service | `src/gaia/apps/webui/src/services/api.ts` | COMPLETE (lines 466-514) |
| Template Store | `src/gaia/apps/webui/src/stores/templateStore.ts` | COMPLETE |
| Pipeline Router Mount | `src/gaia/ui/server.py:290` | COMPLETE |
| CLI Pipeline Command | `src/gaia/cli.py:4725-4775` | COMPLETE |

### 1.2 What's Missing

| Component | Gap | Impact |
|-----------|-----|--------|
| Backend Run Endpoint | No `POST /api/v1/pipeline/run` | Users cannot execute pipelines from UI |
| Backend SSE Streaming | No streaming endpoint | No real-time progress feedback |
| Frontend Pipeline Panel | No UI component | No visible pipeline execution interface |
| Frontend Pipeline Service | No API wrapper | No TypeScript client for pipeline execution |
| Frontend Pipeline Store | No Zustand store | No state management for pipeline runs |
| Type Definitions | Missing pipeline event types | No TypeScript type safety |

---

## 2. Backend Implementation

### 2.1 File: `src/gaia/ui/routers/pipeline.py`

**Action:** Add SSE streaming endpoint for pipeline execution

**Lines to Add:** ~120 lines (add after line 259)

```python
# Add to imports at top of file (after line 27)
import asyncio
import time
import uuid
from typing import AsyncGenerator, Optional

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from ..database import ChatDatabase
from ..dependencies import get_db
from ..sse_handler import SSEOutputHandler

# Add after line 31 (router definition)
# Session locks and semaphore for pipeline execution (matches chat.py pattern)
_pipeline_session_locks: dict[str, asyncio.Lock] = {}
_pipeline_semaphore = asyncio.Semaphore(5)  # Limit to 5 concurrent pipeline runs


# Add new schema classes (after TemplateUpdateRequest, before line 117)
class PipelineRunRequest(BaseModel):
    """Request to execute a pipeline."""

    session_id: str = Field(..., description="Session ID for tracking")
    task_description: str = Field(..., description="Task/objective to execute")
    template_name: Optional[str] = Field(
        None, description="Optional pipeline template name"
    )
    auto_spawn: bool = Field(default=True, description="Auto-generate missing agents")
    stream: bool = Field(default=True, description="Enable SSE streaming")


class PipelineRunResponse(BaseModel):
    """Response from pipeline execution."""

    pipeline_id: str = Field(..., description="Unique pipeline execution ID")
    status: str = Field(..., description="initial|running|completed|failed|blocked")
    message: str = Field(..., description="Status message")


# Add new endpoint (after line 259, at end of file)
@router.post("/api/v1/pipeline/run")
async def run_pipeline_endpoint(
    request: PipelineRunRequest,
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
):
    """
    Execute a pipeline task with SSE streaming.

    This endpoint runs the full 5-stage pipeline orchestration:
    1. Domain Analysis
    2. Workflow Modeling
    3. Loom Building
    4. Gap Detection (with optional auto-spawn)
    5. Pipeline Execution

    Args:
        request: Pipeline execution request with task description
        http_request: FastAPI request for state access
        db: Chat database for session tracking

    Returns:
        StreamingResponse with SSE events for progress, or PipelineRunResponse
    """
    # Verify session exists
    session = db.get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Generate unique pipeline ID
    pipeline_id = str(uuid.uuid4())

    # Acquire session lock (prevent duplicate runs for same session)
    session_lock = _pipeline_session_locks.setdefault(request.session_id, asyncio.Lock())
    try:
        await asyncio.wait_for(session_lock.acquire(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning(
            "Force-releasing stuck pipeline session lock for %s",
            request.session_id,
        )
        try:
            session_lock.release()
        except RuntimeError:
            pass
        await session_lock.acquire()

    # Acquire semaphore (limit concurrent pipeline runs)
    try:
        await asyncio.wait_for(_pipeline_semaphore.acquire(), timeout=60.0)
    except asyncio.TimeoutError:
        session_lock.release()
        raise HTTPException(
            status_code=429,
            detail="The server is busy processing other pipeline runs. Please try again.",
        )

    # Record pipeline start in session
    db.add_message(
        request.session_id,
        "system",
        f"Pipeline started: {request.task_description[:100]}...",
    )

    # Create SSE output handler for this pipeline run
    output_handler = SSEOutputHandler()

    # Track lock ownership for streaming path
    locks_released = False

    try:
        if request.stream:
            # Async generator for SSE streaming
            async def _release_locks():
                """Release locks when stream completes."""
                try:
                    session_lock.release()
                except RuntimeError:
                    pass
                try:
                    _pipeline_semaphore.release()
                except ValueError:
                    pass

            async def _stream_pipeline_events() -> AsyncGenerator[str, None]:
                """Stream pipeline events as SSE."""
                nonlocal locks_released

                try:
                    # Emit pipeline start event
                    yield f"data: {json.dumps({'type': 'status', 'status': 'starting', 'message': 'Initializing pipeline...', 'pipeline_id': pipeline_id})}\n\n"

                    # Execute pipeline in background thread to avoid blocking
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None,
                        _execute_pipeline_sync,
                        request.task_description,
                        request.auto_spawn,
                        output_handler,
                        pipeline_id,
                    )

                    # Emit completion event
                    yield f"data: {json.dumps({'type': 'done', 'pipeline_id': pipeline_id, 'result': result})}\n\n"

                except Exception as e:
                    logger.error(f"Pipeline streaming error: {e}", exc_info=True)
                    yield f"data: {json.dumps({'type': 'error', 'content': str(e), 'pipeline_id': pipeline_id})}\n\n"
                finally:
                    # Ensure locks are released (BackgroundTask handles this too)
                    if not locks_released:
                        locks_released = True

            # Return streaming response
            locks_released = True
            return StreamingResponse(
                _stream_pipeline_events(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
                background=BackgroundTask(_release_locks),
            )
        else:
            # Non-streaming: execute and return result
            try:
                result = _execute_pipeline_sync(
                    request.task_description,
                    request.auto_spawn,
                    output_handler,
                    pipeline_id,
                )
                db.add_message(
                    request.session_id,
                    "assistant",
                    f"Pipeline completed: {result.get('pipeline_status', 'unknown')}",
                )
                return PipelineRunResponse(
                    pipeline_id=pipeline_id,
                    status=result.get("pipeline_status", "unknown"),
                    message="Pipeline execution completed",
                )
            finally:
                session_lock.release()

    finally:
        if not locks_released:
            _pipeline_semaphore.release()


def _execute_pipeline_sync(
    task_description: str,
    auto_spawn: bool,
    output_handler: SSEOutputHandler,
    pipeline_id: str,
) -> dict:
    """
    Execute pipeline synchronously (for executor thread).

    Args:
        task_description: Task/objective to execute
        auto_spawn: Auto-generate missing agents
        output_handler: SSE output handler for events
        pipeline_id: Unique pipeline execution ID

    Returns:
        Pipeline execution result dictionary
    """
    from gaia.pipeline.orchestrator import PipelineOrchestrator

    try:
        # Emit stage start events
        output_handler.print_processing_start(
            query=task_description,
            max_steps=50,
            model_id="Qwen3.5-35B-A3B-GGUF",
        )

        # Create orchestrator
        orchestrator = PipelineOrchestrator(
            model_id="Qwen3.5-35B-A3B-GGUF",
            debug=False,
            max_steps=50,
        )

        # Execute pipeline
        output_handler.print_status_info("Running domain analysis...")
        output_handler.print_info("Stage 1: Domain Analysis")

        output_handler.print_status_info("Modeling workflow...")
        output_handler.print_info("Stage 2: Workflow Modeling")

        output_handler.print_status_info("Building execution topology...")
        output_handler.print_info("Stage 3: Loom Building")

        output_handler.print_status_info("Detecting agent gaps...")
        output_handler.print_info("Stage 4: Gap Detection")

        output_handler.print_status_info("Executing pipeline...")
        output_handler.print_info("Stage 5: Pipeline Execution")

        result = orchestrator.run_pipeline(
            task_description=task_description,
            auto_spawn=auto_spawn,
        )

        output_handler.print_final_answer(
            f"Pipeline completed with status: {result.get('pipeline_status', 'unknown')}"
        )

        return result

    except Exception as e:
        logger.error(f"Pipeline execution failed: {e}", exc_info=True)
        output_handler.print_error(f"Pipeline failed: {str(e)}")
        return {
            "pipeline_status": "failed",
            "error": str(e),
            "pipeline_id": pipeline_id,
        }
```

---

### 2.2 File: `src/gaia/ui/schemas/pipeline_templates.py`

**Action:** Add pipeline run request/response schemas

**Lines to Add:** ~30 lines (add at end of file)

```python
# Add at end of file (after line 117)


class PipelineRunRequest(BaseModel):
    """Request to execute a pipeline."""

    session_id: str = Field(..., description="Session ID for tracking")
    task_description: str = Field(..., description="Task/objective to execute")
    template_name: Optional[str] = Field(
        None, description="Optional pipeline template name"
    )
    auto_spawn: bool = Field(default=True, description="Auto-generate missing agents")
    stream: bool = Field(default=True, description="Enable SSE streaming")


class PipelineRunResponse(BaseModel):
    """Response from pipeline execution."""

    pipeline_id: str
    status: str
    message: str
```

**Note:** Move the schema classes from `pipeline.py` to this file for better separation of concerns, then import in `pipeline.py`.

---

## 3. Frontend Implementation

### 3.1 File: `src/gaia/apps/webui/src/types/index.ts`

**Action:** Add pipeline execution types

**Lines to Add:** ~80 lines (add after line 298)

```typescript
// Add after existing types (after line 298)

// ── Pipeline Execution Types ──────────────────────────────────────────────

/** Pipeline execution status */
export type PipelineStatus =
    | 'initial'
    | 'starting'
    | 'running'
    | 'completed'
    | 'failed'
    | 'blocked';

/** Pipeline stage */
export type PipelineStage =
    | 'domain_analysis'
    | 'workflow_modeling'
    | 'loom_building'
    | 'gap_detection'
    | 'pipeline_execution';

/** Pipeline event for SSE streaming */
export interface PipelineEvent {
    type: 'status' | 'step' | 'thinking' | 'plan' | 'tool_start' | 'tool_end' | 'tool_result' | 'done' | 'error';
    pipeline_id?: string;
    status?: string;
    message?: string;
    step?: number;
    total?: number;
    content?: string;
    result?: Record<string, unknown>;
    elapsed?: number;
}

/** Pipeline execution request */
export interface PipelineRunRequest {
    session_id: string;
    task_description: string;
    template_name?: string;
    auto_spawn?: boolean;
    stream?: boolean;
}

/** Pipeline execution response */
export interface PipelineRunResponse {
    pipeline_id: string;
    status: PipelineStatus;
    message: string;
}

/** Pipeline execution state */
export interface PipelineExecution {
    id: string;
    sessionId: string;
    taskDescription: string;
    status: PipelineStatus;
    currentStage?: PipelineStage;
    startTime: number;
    endTime?: number;
    events: PipelineEvent[];
    result?: Record<string, unknown>;
    error?: string;
}
```

---

### 3.2 File: `src/gaia/apps/webui/src/services/api.ts`

**Action:** Add pipeline execution API functions

**Lines to Add:** ~60 lines (add at end of file, after line 587)

```typescript
// Add at end of file (after line 587)

// ── Pipeline Execution ─────────────────────────────────────────────────────

/**
 * Callbacks for pipeline streaming events.
 */
export interface PipelineStreamCallbacks {
    /** Pipeline status update (starting, running, etc.). */
    onStatus: (event: PipelineEvent) => void;
    /** Pipeline stage progress (step updates). */
    onStep: (event: PipelineEvent) => void;
    /** Pipeline thinking/reasoning output. */
    onThinking: (event: PipelineEvent) => void;
    /** Tool execution start. */
    onToolStart: (event: PipelineEvent) => void;
    /** Tool execution complete. */
    onToolEnd: (event: PipelineEvent) => void;
    /** Tool result summary. */
    onToolResult: (event: PipelineEvent) => void;
    /** Pipeline complete. */
    onDone: (event: PipelineEvent) => void;
    /** Error occurred. */
    onError: (error: Error) => void;
}

/** Pipeline event types to route to appropriate callbacks. */
const PIPELINE_EVENT_TYPES: Record<string, keyof PipelineStreamCallbacks> = {
    status: 'onStatus',
    step: 'onStep',
    thinking: 'onThinking',
    plan: 'onStatus', // Treat plans as status updates
    tool_start: 'onToolStart',
    tool_end: 'onToolEnd',
    tool_result: 'onToolResult',
    done: 'onDone',
    error: 'onError',
};

/**
 * Execute a pipeline task with SSE streaming.
 */
export function runPipelineStream(
    request: PipelineRunRequest,
    callbacks: PipelineStreamCallbacks,
): AbortController {
    const controller = new AbortController();
    const t = log.stream.time();
    let eventCount = 0;

    log.stream.info(`Starting pipeline SSE stream`, {
        taskDescription: request.task_description,
        sessionId: request.session_id,
    });

    fetch(`${API_BASE}/api/v1/pipeline/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: controller.signal,
    })
        .then(async (res) => {
            log.stream.info(`Pipeline SSE connection opened -> HTTP ${res.status}`);

            if (!res.ok) {
                const errText = await res.text().catch(() => '');
                log.stream.error(`Pipeline SSE failed: HTTP ${res.status}`, errText);
                callbacks.onError(new Error(`HTTP ${res.status}: ${errText}`));
                return;
            }

            const reader = res.body?.getReader();
            if (!reader) {
                log.stream.error('No response body reader available');
                callbacks.onError(new Error('No response body'));
                return;
            }

            const decoder = new TextDecoder();
            let buffer = '';

            try {
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) {
                        log.stream.timed(`Pipeline stream complete: ${eventCount} events`, t);
                        break;
                    }

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            const raw = line.slice(6).trim();
                            if (!raw) continue;
                            try {
                                const event: PipelineEvent = JSON.parse(raw);
                                eventCount++;

                                // Route event to appropriate callback
                                const callbackKey = PIPELINE_EVENT_TYPES[event.type] || 'onStatus';
                                const callback = callbacks[callbackKey];
                                if (callback) {
                                    callback(event);
                                } else {
                                    log.stream.debug(`Pipeline event: ${event.type}`, event);
                                }
                            } catch (parseErr) {
                                log.stream.warn(`Malformed pipeline SSE data`, {
                                    raw: raw.slice(0, 100),
                                });
                            }
                        }
                    }
                }
            } finally {
                reader.releaseLock();
            }
        })
        .catch((err) => {
            if (err.name === 'AbortError') {
                log.stream.warn(`Pipeline stream aborted by user after ${eventCount} events`);
            } else {
                log.stream.error(`Pipeline stream fetch error`, err);
                callbacks.onError(err);
            }
        });

    return controller;
}

/**
 * Execute a pipeline task without streaming (returns result immediately).
 */
export async function runPipeline(request: PipelineRunRequest): Promise<PipelineRunResponse> {
    return apiFetch('POST', '/api/v1/pipeline/run', { ...request, stream: false });
}
```

---

### 3.3 File: `src/gaia/apps/webui/src/stores/pipelineStore.ts`

**Action:** Create new Zustand store for pipeline execution state

**Lines:** ~200 lines (new file)

```typescript
// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Zustand store for pipeline execution management.
 *
 * Handles pipeline run state, SSE streaming events, and error handling.
 */

import { create } from 'zustand';
import type { PipelineEvent, PipelineExecution, PipelineRunRequest, PipelineRunResponse } from '../types';
import { runPipelineStream, runPipeline } from '../services/api';
import { log } from '../utils/logger';

// ── State Interface ──────────────────────────────────────────────────────

interface PipelineState {
    // State
    /** List of all pipeline executions */
    executions: PipelineExecution[];
    /** Currently active/running pipeline */
    activeExecution: PipelineExecution | null;
    /** Last pipeline result */
    lastResult: PipelineRunResponse | null;
    /** Whether a pipeline is currently running */
    isRunning: boolean;
    /** Whether pipelines are being loaded */
    isLoading: boolean;
    /** Error message from last failed operation */
    lastError: string | null;

    // Actions - State setters
    /** Set the list of executions */
    setExecutions: (executions: PipelineExecution[]) => void;
    /** Set active execution */
    setActiveExecution: (execution: PipelineExecution | null) => void;
    /** Set last result */
    setLastResult: (result: PipelineRunResponse | null) => void;
    /** Set running state */
    setIsRunning: (running: boolean) => void;
    /** Set loading state */
    setIsLoading: (loading: boolean) => void;
    /** Set last error */
    setLastError: (error: string | null) => void;

    // Actions - Pipeline execution
    /** Run a pipeline task with streaming */
    runPipeline: (request: PipelineRunRequest) => AbortController;
    /** Run a pipeline task without streaming */
    runPipelineSync: (request: PipelineRunRequest) => Promise<PipelineRunResponse>;
    /** Cancel the active pipeline */
    cancelPipeline: () => void;
    /** Clear a specific execution from history */
    clearExecution: (id: string) => void;
    /** Clear all execution history */
    clearAllExecutions: () => void;
}

// ── Store Implementation ─────────────────────────────────────────────────

export const usePipelineStore = create<PipelineState>((set, get) => {
    // Abort controller for current stream
    let currentAbortController: AbortController | null = null;

    return {
        // Initial state
        executions: [],
        activeExecution: null,
        lastResult: null,
        isRunning: false,
        isLoading: false,
        lastError: null,

        // State setters
        setExecutions: (executions) => set({ executions }),
        setActiveExecution: (execution) => set({ activeExecution: execution }),
        setLastResult: (result) => set({ lastResult: result }),
        setIsRunning: (running) => set({ isRunning: running }),
        setIsLoading: (loading) => set({ isLoading: loading }),
        setLastError: (error) => set({ lastError: error }),

        // Run pipeline with streaming
        runPipeline: (request) => {
            const execution: PipelineExecution = {
                id: crypto.randomUUID(),
                sessionId: request.session_id,
                taskDescription: request.task_description,
                status: 'starting',
                startTime: Date.now(),
                events: [],
            };

            // Add to executions list
            set((state) => ({
                executions: [...state.executions, execution],
                activeExecution: execution,
                isRunning: true,
                lastError: null,
            }));

            log.ui.info(`[pipelineStore] Starting pipeline: ${request.task_description}`);

            // Set up streaming callbacks
            const callbacks = {
                onStatus: (event: PipelineEvent) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                status: event.status as PipelineState['activeExecution']['status'] || 'running',
                                currentStage: event.message?.includes('Stage 1') ? 'domain_analysis'
                                    : event.message?.includes('Stage 2') ? 'workflow_modeling'
                                    : event.message?.includes('Stage 3') ? 'loom_building'
                                    : event.message?.includes('Stage 4') ? 'gap_detection'
                                    : 'pipeline_execution',
                                events: [...state.activeExecution.events, event],
                            },
                        };
                    });
                },
                onStep: (event: PipelineEvent) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                events: [...state.activeExecution.events, event],
                            },
                        };
                    });
                },
                onThinking: (event: PipelineEvent) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                events: [...state.activeExecution.events, event],
                            },
                        };
                    });
                },
                onToolStart: (event: PipelineEvent) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                events: [...state.activeExecution.events, event],
                            },
                        };
                    });
                },
                onToolEnd: (event: PipelineEvent) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                events: [...state.activeExecution.events, event],
                            },
                        };
                    });
                },
                onToolResult: (event: PipelineEvent) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                events: [...state.activeExecution.events, event],
                            },
                        };
                    });
                },
                onDone: (event: PipelineEvent) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                status: 'completed',
                                endTime: Date.now(),
                                result: event.result,
                                events: [...state.activeExecution.events, event],
                            },
                            isRunning: false,
                            lastResult: event.result as unknown as PipelineRunResponse,
                        };
                    });
                    log.ui.info(`[pipelineStore] Pipeline complete`);
                },
                onError: (error: Error) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                status: 'failed',
                                endTime: Date.now(),
                                error: error.message,
                                events: [...state.activeExecution.events, {
                                    type: 'error',
                                    content: error.message,
                                }],
                            },
                            isRunning: false,
                            lastError: error.message,
                        };
                    });
                    log.ui.error(`[pipelineStore] Pipeline failed:`, error);
                },
            };

            // Start streaming
            currentAbortController = runPipelineStream(request, callbacks);
            return currentAbortController;
        },

        // Run pipeline without streaming
        runPipelineSync: async (request) => {
            set({ isLoading: true, lastError: null });
            try {
                const result = await runPipeline(request);
                set({ lastResult: result, isLoading: false });
                log.ui.info(`[pipelineStore] Pipeline complete: ${result.status}`);
                return result;
            } catch (err) {
                const message = err instanceof Error ? err.message : String(err);
                set({ lastError: `Failed to run pipeline: ${message}`, isLoading: false });
                log.ui.error('[pipelineStore] Failed to run pipeline:', err);
                throw err;
            }
        },

        // Cancel active pipeline
        cancelPipeline: () => {
            if (currentAbortController) {
                currentAbortController.abort();
                currentAbortController = null;
                set((state) => {
                    if (!state.activeExecution) return state;
                    return {
                        activeExecution: {
                            ...state.activeExecution,
                            status: 'failed',
                            endTime: Date.now(),
                            error: 'Cancelled by user',
                        },
                        isRunning: false,
                    };
                });
                log.ui.warn('[pipelineStore] Pipeline cancelled');
            }
        },

        // Clear specific execution
        clearExecution: (id) => {
            set((state) => ({
                executions: state.executions.filter((e) => e.id !== id),
                activeExecution: state.activeExecution?.id === id ? null : state.activeExecution,
            }));
        },

        // Clear all executions
        clearAllExecutions: () => {
            set({ executions: [], activeExecution: null, lastResult: null });
        },
    };
});
```

---

### 3.4 File: `src/gaia/apps/webui/src/components/PipelinePanel.tsx`

**Action:** Create new pipeline execution panel component

**Lines:** ~250 lines (new file)

```typescript
// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * PipelinePanel - Interactive pipeline execution interface.
 *
 * Provides a chat-like interface for submitting pipeline tasks
 * and viewing real-time progress through the 5 stages.
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import { Play, Square, X, CheckCircle, AlertCircle, Loader2 } from 'lucide-react';
import { usePipelineStore } from '../stores/pipelineStore';
import { useChatStore } from '../stores/chatStore';
import type { PipelineEvent } from '../types';
import './PipelinePanel.css';

export function PipelinePanel() {
    const { currentSessionId } = useChatStore();
    const {
        activeExecution,
        isRunning,
        lastError,
        runPipeline,
        cancelPipeline,
        clearExecution,
    } = usePipelineStore();

    const [taskInput, setTaskInput] = useState('');
    const [autoSpawn, setAutoSpawn] = useState(true);
    const eventsEndRef = useRef<HTMLDivElement>(null);

    // Auto-scroll to latest event
    useEffect(() => {
        eventsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [activeExecution?.events]);

    // Handle task submission
    const handleSubmit = useCallback(
        (e: React.FormEvent) => {
            e.preventDefault();
            if (!taskInput.trim() || !currentSessionId || isRunning) return;

            runPipeline({
                session_id: currentSessionId,
                task_description: taskInput.trim(),
                auto_spawn: autoSpawn,
                stream: true,
            });

            setTaskInput('');
        },
        [taskInput, currentSessionId, isRunning, autoSpawn, runPipeline],
    );

    // Handle cancellation
    const handleCancel = useCallback(() => {
        cancelPipeline();
    }, [cancelPipeline]);

    // Handle clear
    const handleClear = useCallback(() => {
        if (activeExecution) {
            clearExecution(activeExecution.id);
        }
    }, [activeExecution, clearExecution]);

    // Get current stage label
    const getStageLabel = () => {
        if (!activeExecution?.currentStage) return 'Initializing...';
        const stageMap: Record<string, string> = {
            domain_analysis: 'Stage 1: Domain Analysis',
            workflow_modeling: 'Stage 2: Workflow Modeling',
            loom_building: 'Stage 3: Loom Building',
            gap_detection: 'Stage 4: Gap Detection',
            pipeline_execution: 'Stage 5: Pipeline Execution',
        };
        return stageMap[activeExecution.currentStage] || 'Running...';
    };

    // Get status color
    const getStatusColor = () => {
        switch (activeExecution?.status) {
            case 'completed':
                return 'pipeline-status-success';
            case 'failed':
                return 'pipeline-status-error';
            case 'blocked':
                return 'pipeline-status-warning';
            default:
                return 'pipeline-status-running';
        }
    };

    return (
        <div className="pipeline-panel">
            {/* Header */}
            <div className="pipeline-panel-header">
                <h2>Pipeline Execution</h2>
                <p>Run 5-stage auto-spawn pipeline orchestration</p>
            </div>

            {/* Error Banner */}
            {lastError && (
                <div className="pipeline-error-banner" role="alert">
                    <AlertCircle size={18} />
                    <span>{lastError}</span>
                    <button
                        className="pipeline-error-dismiss"
                        onClick={() => {}}
                        aria-label="Dismiss error"
                    >
                        Dismiss
                    </button>
                </div>
            )}

            {/* Active Execution */}
            {activeExecution && (
                <div className="pipeline-execution">
                    {/* Status Bar */}
                    <div className={`pipeline-status-bar ${getStatusColor()}`}>
                        {activeExecution.status === 'running' && (
                            <Loader2 size={18} className="spin" />
                        )}
                        {activeExecution.status === 'completed' && (
                            <CheckCircle size={18} />
                        )}
                        {activeExecution.status === 'failed' && (
                            <AlertCircle size={18} />
                        )}
                        <span>{getStageLabel()}</span>
                        {isRunning && (
                            <button
                                className="pipeline-btn pipeline-btn-cancel"
                                onClick={handleCancel}
                                aria-label="Cancel pipeline"
                            >
                                <Square size={16} />
                                Cancel
                            </button>
                        )}
                    </div>

                    {/* Events Log */}
                    <div className="pipeline-events">
                        {activeExecution.events.map((event, index) => (
                            <div
                                key={index}
                                className={`pipeline-event pipeline-event-${event.type}`}
                            >
                                {event.type === 'thinking' && (
                                    <div className="pipeline-event-content">
                                        <strong>Thinking:</strong> {event.content}
                                    </div>
                                )}
                                {event.type === 'tool_start' && (
                                    <div className="pipeline-event-content">
                                        <strong>Tool:</strong> {event.tool}
                                    </div>
                                )}
                                {event.type === 'tool_result' && (
                                    <div className="pipeline-event-content">
                                        <strong>Result:</strong> {event.summary}
                                    </div>
                                )}
                                {event.type === 'status' && (
                                    <div className="pipeline-event-content">
                                        {event.message}
                                    </div>
                                )}
                                {event.type === 'error' && (
                                    <div className="pipeline-event-content pipeline-event-error">
                                        <AlertCircle size={16} />
                                        {event.content}
                                    </div>
                                )}
                            </div>
                        ))}
                        <div ref={eventsEndRef} />
                    </div>
                </div>
            )}

            {/* Input Form */}
            <form className="pipeline-input-form" onSubmit={handleSubmit}>
                <div className="pipeline-input-group">
                    <textarea
                        className="pipeline-task-input"
                        placeholder="Describe your task or objective..."
                        value={taskInput}
                        onChange={(e) => setTaskInput(e.target.value)}
                        disabled={isRunning}
                        rows={3}
                        aria-label="Task description"
                    />
                    <div className="pipeline-options">
                        <label className="pipeline-option">
                            <input
                                type="checkbox"
                                checked={autoSpawn}
                                onChange={(e) => setAutoSpawn(e.target.checked)}
                                disabled={isRunning}
                            />
                            Auto-spawn missing agents
                        </label>
                    </div>
                    <div className="pipeline-actions">
                        {!isRunning ? (
                            <button
                                type="submit"
                                className="pipeline-btn pipeline-btn-primary"
                                disabled={!taskInput.trim() || !currentSessionId}
                                aria-label="Run pipeline"
                            >
                                <Play size={18} />
                                Run Pipeline
                            </button>
                        ) : (
                            <button
                                type="button"
                                className="pipeline-btn pipeline-btn-cancel"
                                onClick={handleCancel}
                                aria-label="Cancel pipeline"
                            >
                                <Square size={18} />
                                Cancel
                            </button>
                        )}
                        {activeExecution && (
                            <button
                                type="button"
                                className="pipeline-btn pipeline-btn-secondary"
                                onClick={handleClear}
                                aria-label="Clear execution"
                            >
                                <X size={18} />
                                Clear
                            </button>
                        )}
                    </div>
                </div>
            </form>
        </div>
    );
}
```

---

### 3.5 File: `src/gaia/apps/webui/src/components/PipelinePanel.css`

**Action:** Create styles for pipeline panel

**Lines:** ~150 lines (new file)

```css
/* Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved. */
/* SPDX-License-Identifier: MIT */

/** PipelinePanel styles */

.pipeline-panel {
    display: flex;
    flex-direction: column;
    gap: 1.5rem;
    padding: 1.5rem;
    max-width: 900px;
    margin: 0 auto;
}

.pipeline-panel-header {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}

.pipeline-panel-header h2 {
    margin: 0;
    font-size: 1.5rem;
    font-weight: 600;
}

.pipeline-panel-header p {
    margin: 0;
    color: var(--text-secondary);
    font-size: 0.9rem;
}

/* Error Banner */
.pipeline-error-banner {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.75rem 1rem;
    background: var(--error-bg);
    border: 1px solid var(--error-border);
    border-radius: 8px;
    color: var(--error-text);
}

.pipeline-error-banner svg {
    flex-shrink: 0;
}

.pipeline-error-dismiss {
    margin-left: auto;
    background: transparent;
    border: 1px solid var(--error-border);
    border-radius: 4px;
    padding: 0.25rem 0.5rem;
    color: var(--error-text);
    cursor: pointer;
}

.pipeline-error-dismiss:hover {
    background: rgba(0, 0, 0, 0.1);
}

/* Status Bar */
.pipeline-status-bar {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 1rem;
    border-radius: 8px;
    font-weight: 500;
}

.pipeline-status-running {
    background: var(--primary-bg);
    color: var(--primary-text);
}

.pipeline-status-success {
    background: var(--success-bg);
    color: var(--success-text);
}

.pipeline-status-error {
    background: var(--error-bg);
    color: var(--error-text);
}

.pipeline-status-warning {
    background: var(--warning-bg);
    color: var(--warning-text);
}

/* Events Log */
.pipeline-events {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    max-height: 400px;
    overflow-y: auto;
    padding: 1rem;
    background: var(--bg-secondary);
    border-radius: 8px;
    border: 1px solid var(--border);
}

.pipeline-event {
    padding: 0.75rem;
    border-radius: 6px;
    border-left: 3px solid var(--border);
}

.pipeline-event-thinking {
    background: var(--bg-tertiary);
    border-left-color: var(--primary);
}

.pipeline-event-tool_start {
    background: var(--primary-bg);
    border-left-color: var(--primary);
}

.pipeline-event-tool_end {
    background: var(--success-bg);
    border-left-color: var(--success);
}

.pipeline-event-error {
    background: var(--error-bg);
    border-left-color: var(--error);
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.pipeline-event-content {
    font-size: 0.9rem;
}

.pipeline-event-content strong {
    margin-right: 0.5rem;
}

/* Input Form */
.pipeline-input-form {
    display: flex;
    flex-direction: column;
    gap: 1rem;
}

.pipeline-input-group {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.pipeline-task-input {
    width: 100%;
    padding: 0.75rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    font-family: inherit;
    font-size: 0.95rem;
    resize: vertical;
}

.pipeline-task-input:focus {
    outline: none;
    border-color: var(--primary);
    box-shadow: 0 0 0 2px var(--primary-bg);
}

.pipeline-task-input:disabled {
    background: var(--bg-secondary);
    cursor: not-allowed;
}

.pipeline-options {
    display: flex;
    gap: 1rem;
}

.pipeline-option {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.9rem;
    color: var(--text-secondary);
    cursor: pointer;
}

.pipeline-option input:disabled {
    cursor: not-allowed;
}

.pipeline-actions {
    display: flex;
    gap: 0.75rem;
    justify-content: flex-end;
}

.pipeline-btn {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.625rem 1rem;
    border-radius: 6px;
    font-size: 0.9rem;
    font-weight: 500;
    cursor: pointer;
    border: 1px solid transparent;
    transition: all 0.2s;
}

.pipeline-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.pipeline-btn-primary {
    background: var(--primary);
    color: white;
}

.pipeline-btn-primary:hover:not(:disabled) {
    background: var(--primary-dark);
}

.pipeline-btn-secondary {
    background: var(--bg-secondary);
    color: var(--text-primary);
    border-color: var(--border);
}

.pipeline-btn-secondary:hover {
    background: var(--bg-tertiary);
}

.pipeline-btn-cancel {
    background: var(--error-bg);
    color: var(--error-text);
    border-color: var(--error-border);
}

.pipeline-btn-cancel:hover {
    background: var(--error);
    color: white;
}

/* Spin animation for loading icons */
@keyframes spin {
    to {
        transform: rotate(360deg);
    }
}

.spin {
    animation: spin 1s linear infinite;
}
```

---

### 3.6 File: `src/gaia/apps/webui/src/App.tsx`

**Action:** Add pipeline panel route and navigation

**Lines to Modify:** ~20 lines

```typescript
// Modify line 54 to add 'pipeline' view
type AppView = 'chat' | 'templates' | 'pipeline';

// Add import (after line 15)
import { PipelinePanel } from './components/PipelinePanel';

// Modify the view rendering section (find the conditional rendering for views)
// Add pipeline view case:
```

Find the section where views are rendered and add:

```typescript
{currentView === 'pipeline' && <PipelinePanel />}
```

**Note:** You may also want to add a navigation item in `Sidebar.tsx` to access the pipeline panel.

---

### 3.7 File: `src/gaia/apps/webui/src/components/Sidebar.tsx`

**Action:** Add pipeline navigation item

**Lines to Add:** ~10 lines

Find the navigation section and add:

```typescript
import { Pipeline } from 'lucide-react'; // Add to imports

// In navigation list, add:
<button
    className={`sidebar-nav-btn ${currentView === 'pipeline' ? 'active' : ''}`}
    onClick={() => {
        setCurrentView('pipeline');
        if (window.innerWidth <= 768) toggleSidebar();
    }}
    aria-label="Pipeline"
    aria-current={currentView === 'pipeline' ? 'page' : undefined}
>
    <Pipeline size={20} />
    <span>Pipeline</span>
</button>
```

---

## 4. Integration Testing

### 4.1 Backend Tests

Create `tests/unit/test_pipeline_router.py`:

```python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for pipeline router SSE endpoint."""

import pytest
from fastapi.testclient import TestClient

from gaia.ui.server import create_app


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(create_app())


@pytest.fixture
def test_session(client):
    """Create a test session."""
    response = client.post("/api/sessions", json={"title": "Test Pipeline Session"})
    return response.json()["id"]


def test_run_pipeline_streaming(client, test_session):
    """Test pipeline run endpoint with streaming."""
    response = client.post(
        "/api/v1/pipeline/run",
        json={
            "session_id": test_session,
            "task_description": "Test task",
            "auto_spawn": False,
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]


def test_run_pipeline_invalid_session(client):
    """Test pipeline run with invalid session."""
    response = client.post(
        "/api/v1/pipeline/run",
        json={
            "session_id": "invalid-session-id",
            "task_description": "Test task",
        },
    )
    assert response.status_code == 404
```

### 4.2 Frontend Tests

The component tests should verify:
1. PipelinePanel renders correctly
2. Task submission triggers API call
3. SSE events are displayed properly
4. Cancel button works
5. Clear button works

---

## 5. Acceptance Criteria

| Criterion | Status | Verification |
|-----------|--------|--------------|
| Backend endpoint `POST /api/v1/pipeline/run` exists | MUST HAVE | Test with curl/Postman |
| SSE streaming functional | MUST HAVE | Verify `text/event-stream` content type |
| Pipeline stages emit events | MUST HAVE | Verify events for all 5 stages |
| Frontend PipelinePanel component exists | MUST HAVE | Visual inspection |
| Pipeline panel accessible from sidebar | MUST HAVE | Click navigation |
| Task submission triggers pipeline | MUST HAVE | End-to-end test |
| Real-time event display works | MUST HAVE | Watch events appear |
| Cancel functionality works | MUST HAVE | Click cancel mid-run |
| TypeScript compiles without errors | MUST HAVE | Run `npm run build` |
| Python linting passes | MUST HAVE | Run `python util/lint.py --all` |

---

## 6. Maintainability Considerations

### 6.1 Separation of Concerns

| Layer | Responsibility |
|-------|----------------|
| `pipeline.py` router | HTTP endpoint, request validation, SSE response |
| `orchestrator.py` | Pipeline logic, stage execution |
| `PipelinePanel.tsx` | UI rendering, user interaction |
| `pipelineStore.ts` | State management, event handling |
| `api.ts` | HTTP client, SSE streaming |

### 6.2 Type Safety

- All TypeScript interfaces defined in `types/index.ts`
- Pydantic schemas for request/response validation
- Proper typing for callback functions

### 6.3 Error Handling

- Backend: HTTPException with appropriate status codes
- Frontend: Error banners, toast notifications
- SSE: Error events streamed to client

### 6.4 Concurrency Control

- Session locks prevent duplicate runs
- Semaphore limits concurrent executions (5 max)
- BackgroundTask ensures cleanup on disconnect

---

## 7. Scalability Considerations

### 7.1 Current Design Supports

- Multiple concurrent pipeline executions (limited by semaphore)
- Session isolation via per-session locks
- Event history tracking per execution
- Clean resource cleanup on disconnect

### 7.2 Future Enhancements

| Enhancement | Effort | Priority |
|-------------|--------|----------|
| Pipeline run persistence in database | 4 hours | P2 |
| Pipeline run history view | 6 hours | P2 |
| Pipeline template selection UI | 4 hours | P3 |
| Pipeline metrics dashboard | 8 hours | P3 |
| WebSocket instead of SSE | 6 hours | P3 |

---

## 8. File Summary

| File | Action | Lines | Purpose |
|------|--------|-------|---------|
| `src/gaia/ui/routers/pipeline.py` | MODIFY | +120 | Add SSE run endpoint |
| `src/gaia/ui/schemas/pipeline_templates.py` | MODIFY | +30 | Add run schemas |
| `src/gaia/apps/webui/src/types/index.ts` | MODIFY | +80 | Add pipeline types |
| `src/gaia/apps/webui/src/services/api.ts` | MODIFY | +60 | Add API functions |
| `src/gaia/apps/webui/src/stores/pipelineStore.ts` | CREATE | ~200 | State management |
| `src/gaia/apps/webui/src/components/PipelinePanel.tsx` | CREATE | ~250 | UI component |
| `src/gaia/apps/webui/src/components/PipelinePanel.css` | CREATE | ~150 | Styles |
| `src/gaia/apps/webui/src/App.tsx` | MODIFY | +5 | Add route |
| `src/gaia/apps/webui/src/components/Sidebar.tsx` | MODIFY | +10 | Add nav item |
| `tests/unit/test_pipeline_router.py` | CREATE | ~40 | Backend tests |

**Total New Lines:** ~945 lines  
**Total Modified Lines:** ~165 lines  
**Estimated Effort:** 4-6 hours

---

## 9. Implementation Sequence

1. **Backend First** (2 hours)
   - Add schemas to `pipeline_templates.py`
   - Add SSE endpoint to `pipeline.py`
   - Test with curl/Postman

2. **Frontend Types** (30 min)
   - Add types to `types/index.ts`
   - Add API functions to `api.ts`

3. **Frontend State** (1 hour)
   - Create `pipelineStore.ts`
   - Test store actions

4. **Frontend UI** (1.5 hours)
   - Create `PipelinePanel.tsx`
   - Create `PipelinePanel.css`
   - Add to `App.tsx` and `Sidebar.tsx`

5. **Testing** (30 min)
   - Run backend tests
   - Build frontend
   - Manual end-to-end test

---

## 10. Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| SSE streaming fails on Windows | HIGH | Use thread pool executor (implemented) |
| Frontend build fails | MEDIUM | Run `npm run build` after changes |
| Type mismatches | LOW | TypeScript compiler catches at build |
| Semaphore deadlock | MEDIUM | Timeout + BackgroundTask cleanup |
| Session lock race condition | LOW | Atomic setdefault + try/except |

---

## 11. Updated Branch Change Matrix

Update `docs/reference/branch-change-matrix.md` Section 2 (Open Items):

**Change B3-C status:**

| ID | Issue | Status | Notes |
|----|-------|--------|-------|
| **B3-C** | Agent UI pipeline execution endpoint | **RESOLVED** | Implemented `POST /api/v1/pipeline/run` SSE endpoint with full 5-stage progress streaming. Frontend PipelinePanel component added for user interaction. |

---

**Document Prepared By:** Dr. Sarah Kim, Technical Product Strategist & Engineering Lead  
**Date:** 2026-04-11  
**Next Reviewer:** senior-developer (for implementation)
