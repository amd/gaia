# Implementation Plan: B3-C - Agent UI Pipeline Integration

**Priority:** P0 (Merge-Blocking) | **Effort:** 4-6 hours | **Owner:** senior-developer + frontend-developer

---

## Problem Statement

The `gaia pipeline` CLI is fully wired and functional, but the Agent UI (`gaia chat --ui`) has no route, widget, or chat-tool integration for pipeline execution. Users cannot invoke the pipeline via the browser chat interface.

---

## Current State Analysis

### Backend Status (COMPLETED)

1. **PipelineOrchestrator** (`src/gaia/pipeline/orchestrator.py`): Fully implemented with:
   - `run_pipeline(task_description, auto_spawn)` method (lines 512-528)
   - `execute_full_pipeline` tool (lines 80-256)
   - 5-stage execution: DomainAnalyzer → WorkflowModeler → LoomBuilder → GapDetector → PipelineExecutor
   - Auto-spawn capability for missing agents

2. **Pipeline Router** (`src/gaia/ui/routers/pipeline.py`): EXISTS but only handles templates:
   - `GET /api/v1/pipeline/templates` - List templates
   - `POST /api/v1/pipeline/templates` - Create template
   - `PUT /api/v1/pipeline/templates/{name}` - Update template
   - `DELETE /api/v1/pipeline/templates/{name}` - Delete template
   - `GET /api/v1/pipeline/templates/{name}/validate` - Validate template

3. **Pipeline Metrics Router** (`src/gaia/ui/routers/pipeline_metrics.py`): EXISTS for metrics:
   - `GET /api/v1/pipeline/metrics/{pipelineId}` - Get metrics
   - `GET /api/v1/pipeline/metrics/{pipelineId}/history` - Get history
   - `GET /api/v1/pipeline/metrics/aggregate` - Get aggregate metrics

4. **Server Mounting** (`src/gaia/ui/server.py`): ALREADY MOUNTED (lines 55-56):
   ```python
   from .routers import pipeline as pipeline_router_mod
   from .routers import pipeline_metrics as pipeline_metrics_router_mod
   app.include_router(pipeline_router_mod.router)
   app.include_router(pipeline_metrics_router_mod.router)
   ```

### Frontend Status (PARTIAL)

1. **Pipeline Template Manager** (`src/gaia/apps/webui/src/components/templates/PipelineTemplateManager.tsx`): EXISTS
2. **API Service** (`src/gaia/apps/webui/src/services/api.ts`): Has template/metrics functions (lines 465-587) but NO run endpoint
3. **Missing Components**:
   - No `PipelinePanel.tsx` for task input and execution
   - No `pipeline.ts` service for run API calls
   - No integration into ChatView or as separate tab

---

## Implementation Tasks

### Task 1: Backend - Add Pipeline Run Endpoint (1.5 hours)

**File:** `src/gaia/ui/routers/pipeline.py`

**Changes Required:**

1. Add new endpoints to the existing router (append after line 258):

```python
# Add these imports at the top of the file (after line 27)
import asyncio
import json
import logging
from typing import Any, Dict, Optional
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Add new schema classes (after line 27, before router definition)
class PipelineRunRequest(BaseModel):
    """Request schema for pipeline execution."""
    task: str
    model: Optional[str] = "Qwen3.5-35B-A3B-GGUF"
    auto_spawn: bool = True
    use_clear_thought: bool = True

class PipelineRunResponse(BaseModel):
    """Response schema for pipeline execution."""
    pipeline_id: str
    status: str  # "started", "success", "failed", "blocked"
    stage_results: Optional[Dict[str, Any]] = None
    gap_analysis: Optional[Dict[str, Any]] = None
    agents_spawned: Optional[list] = None
    error: Optional[str] = None

# Add new endpoints (append after existing endpoints, before final line)

@router.post("/api/v1/pipeline/run")
async def run_pipeline(
    request: PipelineRunRequest,
):
    """
    Execute a pipeline task with SSE streaming.
    
    This endpoint runs the PipelineOrchestrator with the given task
    and streams stage progress events via Server-Sent Events.
    
    Args:
        request: PipelineRunRequest with task, model, auto_spawn settings
        
    Returns:
        StreamingResponse with SSE events for each pipeline stage
    """
    import uuid
    from gaia.pipeline.orchestrator import PipelineOrchestrator
    
    pipeline_id = str(uuid.uuid4())[:8]
    logger.info(f"Starting pipeline execution {pipeline_id} for task: {request.task[:100]}...")
    
    async def generate_events():
        """Generate SSE events for pipeline stages."""
        try:
            # Send started event
            yield f"data: {json.dumps({'type': 'pipeline_started', 'pipeline_id': pipeline_id})}\n\n"
            
            # Run pipeline in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            
            def run_sync():
                orchestrator = PipelineOrchestrator(
                    model_id=request.model or "Qwen3.5-35B-A3B-GGUF",
                    debug=True,
                    max_steps=50
                )
                return orchestrator.run_pipeline(
                    task_description=request.task,
                    auto_spawn=request.auto_spawn
                )
            
            # Execute pipeline
            result = await loop.run_in_executor(None, run_sync)
            
            # Send stage progress events
            if result.get("stage_results"):
                stages = ["domain_analysis", "workflow_model", "loom_topology", "gap_analysis", "pipeline_execution"]
                for i, stage in enumerate(stages, 1):
                    if stage in result["stage_results"]:
                        yield f"data: {json.dumps({'type': 'pipeline_stage_complete', 'stage': i, 'stage_name': stage, 'result': result['stage_results'][stage]})}\n\n"
            
            # Send completion event
            yield f"data: {json.dumps({\n    'type': 'pipeline_complete',\n    'pipeline_id': pipeline_id,\n    'status': result.get('pipeline_status', 'unknown'),\n    'stage_results': result.get('stage_results'),\n    'gap_analysis': result.get('gap_analysis'),\n    'agents_spawned': result.get('agents_spawned', []),\n    'execution_result': result.get('execution_result'),\n})}\n\n"
            
        except Exception as e:
            logger.error(f"Pipeline execution failed: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'pipeline_error', 'pipeline_id': pipeline_id, 'error': str(e)})}\n\n"
    
    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@router.get("/api/v1/pipeline/status")
async def get_pipeline_status(pipeline_id: str):
    """
    Get status of a pipeline execution.
    
    Args:
        pipeline_id: Pipeline execution ID
        
    Returns:
        Pipeline status and results
    """
    # For now, return a placeholder - in future versions,
    # this could query a pipeline execution store
    return {
        "pipeline_id": pipeline_id,
        "status": "unknown",
        "message": "Pipeline status tracking not yet implemented"
    }
```

**Risk Assessment:**
- **Risk:** LOW - Orchestrator is well-tested
- **Mitigation:** Use thread pool to avoid blocking event loop
- **Testing:** Verify SSE events stream correctly with `curl -N`

---

### Task 2: Backend - Update Router Tags (15 min)

**File:** `src/gaia/ui/routers/pipeline.py`

**Change:** Update router tags (line 31) to include "pipeline-execution":

```python
router = APIRouter(tags=["pipeline", "pipeline-execution"])
```

---

### Task 3: Frontend - Add Pipeline API Service (45 min)

**File:** `src/gaia/apps/webui/src/services/pipeline.ts` (CREATE NEW)

```typescript
// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Pipeline execution API service for GAIA Agent UI. */

import { log } from '../utils/logger';

const API_BASE = '/api/v1/pipeline';

/** Pipeline execution request. */
export interface PipelineRunRequest {
    task: string;
    model?: string;
    auto_spawn?: boolean;
    use_clear_thought?: boolean;
}

/** Pipeline stage progress event. */
export interface PipelineStageEvent {
    type: 'pipeline_stage_complete';
    stage: number;
    stage_name: string;
    result: Record<string, any>;
}

/** Pipeline completion event. */
export interface PipelineCompleteEvent {
    type: 'pipeline_complete';
    pipeline_id: string;
    status: 'success' | 'failed' | 'blocked';
    stage_results?: Record<string, any>;
    gap_analysis?: Record<string, any>;
    agents_spawned?: string[];
    execution_result?: any;
}

/** Pipeline error event. */
export interface PipelineErrorEvent {
    type: 'pipeline_error';
    pipeline_id: string;
    error: string;
}

/** Pipeline started event. */
export interface PipelineStartedEvent {
    type: 'pipeline_started';
    pipeline_id: string;
}

/** Union of all pipeline SSE event types. */
export type PipelineEvent = 
    | PipelineStartedEvent
    | PipelineStageEvent
    | PipelineCompleteEvent
    | PipelineErrorEvent;

/** Callbacks for pipeline streaming events. */
export interface PipelineStreamCallbacks {
    /** Pipeline execution started. */
    onStart: (event: PipelineStartedEvent) => void;
    /** Pipeline stage completed. */
    onStageComplete: (event: PipelineStageEvent) => void;
    /** Pipeline execution completed. */
    onComplete: (event: PipelineCompleteEvent) => void;
    /** Error occurred during execution. */
    onError: (event: PipelineErrorEvent) => void;
}

/**
 * Execute a pipeline task with SSE streaming.
 * 
 * @param request - Pipeline execution request
 * @param callbacks - Streaming event callbacks
 * @returns AbortController for canceling the stream
 */
export function runPipeline(
    request: PipelineRunRequest,
    callbacks: PipelineStreamCallbacks,
): AbortController {
    const controller = new AbortController();
    const url = `${API_BASE}/run`;
    
    log.stream.info(`Starting pipeline execution for: ${request.task.slice(0, 50)}...`);
    
    fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: controller.signal,
    })
        .then(async (res) => {
            if (!res.ok) {
                const errText = await res.text().catch(() => 'Pipeline execution failed');
                log.stream.error(`Pipeline execution failed: HTTP ${res.status}`, errText);
                callbacks.onError({
                    type: 'pipeline_error',
                    pipeline_id: 'unknown',
                    error: `HTTP ${res.status}: ${errText}`,
                });
                return;
            }
            
            const reader = res.body?.getReader();
            if (!reader) {
                log.stream.error('No response body reader available');
                callbacks.onError({
                    type: 'pipeline_error',
                    pipeline_id: 'unknown',
                    error: 'No response body',
                });
                return;
            }
            
            const decoder = new TextDecoder();
            let buffer = '';
            
            try {
                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';
                    
                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            const raw = line.slice(6).trim();
                            if (!raw) continue;
                            
                            try {
                                const event: PipelineEvent = JSON.parse(raw);
                                log.stream.debug(`Pipeline event: ${event.type}`, event);
                                
                                switch (event.type) {
                                    case 'pipeline_started':
                                        callbacks.onStart(event);
                                        break;
                                    case 'pipeline_stage_complete':
                                        callbacks.onStageComplete(event);
                                        break;
                                    case 'pipeline_complete':
                                        callbacks.onComplete(event);
                                        break;
                                    case 'pipeline_error':
                                        callbacks.onError(event);
                                        break;
                                    default:
                                        log.stream.warn(`Unknown pipeline event type: ${event.type}`);
                                }
                            } catch (parseErr) {
                                log.stream.warn(`Malformed pipeline SSE data`, { raw: raw.slice(0, 100) });
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
                log.stream.warn('Pipeline execution aborted by user');
            } else {
                log.stream.error('Pipeline execution fetch error', err);
                callbacks.onError({
                    type: 'pipeline_error',
                    pipeline_id: 'unknown',
                    error: err.message,
                });
            }
        });
    
    return controller;
}

/**
 * Get pipeline execution status.
 * 
 * @param pipelineId - Pipeline execution ID
 * @returns Pipeline status
 */
export async function getPipelineStatus(pipelineId: string): Promise<{
    pipeline_id: string;
    status: string;
    message: string;
}> {
    return fetch(`${API_BASE}/status?pipeline_id=${encodeURIComponent(pipelineId)}`)
        .then((res) => {
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return res.json();
        });
}
```

---

### Task 4: Frontend - Add Pipeline Panel Component (2 hours)

**File:** `src/gaia/apps/webui/src/components/PipelinePanel.tsx` (CREATE NEW)

```tsx
// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** Pipeline execution panel for running GAIA pipeline tasks. */

import React, { useState, useCallback } from 'react';
import { runPipeline, PipelineRunRequest, PipelineEvent, PipelineStageEvent } from '../services/pipeline';
import './PipelinePanel.css';

export interface PipelinePanelProps {
    /** Callback when pipeline execution completes */
    onComplete?: (result: any) => void;
    /** Callback when pipeline execution fails */
    onError?: (error: string) => void;
}

interface StageProgress {
    stage: number;
    name: string;
    status: 'pending' | 'running' | 'complete' | 'error';
    result?: any;
}

const STAGE_NAMES = [
    'Domain Analysis',
    'Workflow Modeling',
    'Loom Building',
    'Gap Detection',
    'Pipeline Execution',
];

export const PipelinePanel: React.FC<PipelinePanelProps> = ({ onComplete, onError }) => {
    const [task, setTask] = useState('');
    const [isRunning, setIsRunning] = useState(false);
    const [stages, setStages] = useState<StageProgress[]>(
        STAGE_NAMES.map((name, i) => ({
            stage: i + 1,
            name,
            status: 'pending',
        }))
    );
    const [currentStage, setCurrentStage] = useState(0);
    const [result, setResult] = useState<any>(null);
    const [error, setError] = useState<string | null>(null);

    const handleRun = useCallback(() => {
        if (!task.trim()) return;

        setIsRunning(true);
        setError(null);
        setResult(null);
        setStages(STAGE_NAMES.map((name, i) => ({
            stage: i + 1,
            name,
            status: 'pending' as const,
        })));
        setCurrentStage(0);

        const request: PipelineRunRequest = {
            task: task.trim(),
            model: 'Qwen3.5-35B-A3B-GGUF',
            auto_spawn: true,
            use_clear_thought: true,
        };

        const controller = runPipeline(request, {
            onStart: () => {
                console.log('Pipeline execution started');
            },
            onStageComplete: (event: PipelineStageEvent) => {
                const stageIndex = event.stage - 1;
                setStages((prev) =>
                    prev.map((s, i) =>
                        i === stageIndex
                            ? { ...s, status: 'complete', result: event.result }
                            : i > stageIndex
                            ? s
                            : { ...s, status: 'running' }
                    )
                );
                setCurrentStage(event.stage);
            },
            onComplete: (event) => {
                setIsRunning(false);
                setResult(event);
                setStages((prev) =>
                    prev.map((s) =>
                        s.stage === STAGE_NAMES.length
                            ? { ...s, status: 'complete' }
                            : s.status === 'pending'
                            ? { ...s, status: 'complete' }
                            : s
                    )
                );
                onComplete?.(event);
            },
            onError: (event) => {
                setIsRunning(false);
                setError(event.error);
                onError?.(event.error);
            },
        });

        // Store controller for potential cancellation
        return () => controller.abort();
    }, [task, onComplete, onError]);

    const handleCancel = useCallback(() => {
        setIsRunning(false);
        setError('Pipeline execution cancelled by user');
    }, []);

    return (
        <div className="pipeline-panel">
            <h2 className="pipeline-panel__title">Pipeline Execution</h2>
            
            <div className="pipeline-panel__input-section">
                <textarea
                    className="pipeline-panel__task-input"
                    placeholder="Enter your task description..."
                    value={task}
                    onChange={(e) => setTask(e.target.value)}
                    disabled={isRunning}
                    rows={4}
                />
                
                <div className="pipeline-panel__controls">
                    <button
                        className="pipeline-panel__run-button button button--primary"
                        onClick={handleRun}
                        disabled={isRunning || !task.trim()}
                    >
                        {isRunning ? 'Running...' : 'Run Pipeline'}
                    </button>
                    
                    {isRunning && (
                        <button
                            className="pipeline-panel__cancel-button button button--secondary"
                            onClick={handleCancel}
                        >
                            Cancel
                        </button>
                    )}
                </div>
            </div>

            {isRunning && (
                <div className="pipeline-panel__progress">
                    <h3 className="pipeline-panel__progress-title">Execution Progress</h3>
                    <div className="pipeline-panel__stages">
                        {stages.map((stage, index) => (
                            <div
                                key={stage.stage}
                                className={`pipeline-panel__stage pipeline-panel__stage--${stage.status}`}
                            >
                                <div className="pipeline-panel__stage-number">{stage.stage}</div>
                                <div className="pipeline-panel__stage-name">{stage.name}</div>
                                <div className="pipeline-panel__stage-status">
                                    {stage.status === 'running' && 'Running...'}
                                    {stage.status === 'complete' && 'Complete'}
                                    {stage.status === 'error' && 'Error'}
                                    {stage.status === 'pending' && 'Pending'}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {result && (
                <div className="pipeline-panel__result">
                    <h3 className="pipeline-panel__result-title">Execution Result</h3>
                    <pre className="pipeline-panel__result-json">
                        {JSON.stringify(result, null, 2)}
                    </pre>
                </div>
            )}

            {error && (
                <div className="pipeline-panel__error">
                    <h3 className="pipeline-panel__error-title">Error</h3>
                    <p className="pipeline-panel__error-message">{error}</p>
                </div>
            )}
        </div>
    );
};
```

---

### Task 5: Frontend - Add Pipeline Panel CSS (30 min)

**File:** `src/gaia/apps/webui/src/components/PipelinePanel.css` (CREATE NEW)

```css
/* Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved. */
/* SPDX-License-Identifier: MIT */

/** Pipeline execution panel styles. */

.pipeline-panel {
    padding: 1.5rem;
    max-width: 900px;
    margin: 0 auto;
}

.pipeline-panel__title {
    font-size: 1.5rem;
    font-weight: 600;
    margin-bottom: 1.5rem;
    color: var(--text-primary);
}

.pipeline-panel__input-section {
    margin-bottom: 1.5rem;
}

.pipeline-panel__task-input {
    width: 100%;
    padding: 0.75rem;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    font-family: inherit;
    font-size: 0.95rem;
    resize: vertical;
    background-color: var(--bg-secondary);
    color: var(--text-primary);
}

.pipeline-panel__task-input:focus {
    outline: none;
    border-color: var(--accent-color);
    box-shadow: 0 0 0 2px var(--accent-color-light);
}

.pipeline-panel__task-input:disabled {
    opacity: 0.6;
    cursor: not-allowed;
}

.pipeline-panel__controls {
    display: flex;
    gap: 0.75rem;
    margin-top: 0.75rem;
}

.pipeline-panel__run-button,
.pipeline-panel__cancel-button {
    padding: 0.625rem 1.25rem;
    border-radius: 4px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
}

.pipeline-panel__run-button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.pipeline-panel__progress {
    margin-top: 2rem;
    padding: 1.25rem;
    background-color: var(--bg-secondary);
    border-radius: 8px;
    border: 1px solid var(--border-color);
}

.pipeline-panel__progress-title {
    font-size: 1.125rem;
    font-weight: 600;
    margin-bottom: 1rem;
    color: var(--text-primary);
}

.pipeline-panel__stages {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
}

.pipeline-panel__stage {
    display: flex;
    align-items: center;
    gap: 1rem;
    padding: 0.75rem 1rem;
    background-color: var(--bg-tertiary);
    border-radius: 6px;
    border-left: 4px solid var(--border-color);
    transition: all 0.2s ease;
}

.pipeline-panel__stage--pending {
    opacity: 0.6;
}

.pipeline-panel__stage--running {
    border-left-color: var(--accent-color);
    background-color: var(--accent-color-light);
    animation: pulse 1.5s ease-in-out infinite;
}

.pipeline-panel__stage--complete {
    border-left-color: var(--success-color);
    background-color: var(--success-color-light);
}

.pipeline-panel__stage--error {
    border-left-color: var(--error-color);
    background-color: var(--error-color-light);
}

.pipeline-panel__stage-number {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background-color: var(--bg-primary);
    font-size: 0.875rem;
    font-weight: 600;
    color: var(--text-secondary);
}

.pipeline-panel__stage--complete .pipeline-panel__stage-number {
    background-color: var(--success-color);
    color: white;
}

.pipeline-panel__stage--error .pipeline-panel__stage-number {
    background-color: var(--error-color);
    color: white;
}

.pipeline-panel__stage--running .pipeline-panel__stage-number {
    background-color: var(--accent-color);
    color: white;
}

.pipeline-panel__stage-name {
    flex: 1;
    font-weight: 500;
    color: var(--text-primary);
}

.pipeline-panel__stage-status {
    font-size: 0.875rem;
    color: var(--text-secondary);
}

.pipeline-panel__result,
.pipeline-panel__error {
    margin-top: 1.5rem;
    padding: 1.25rem;
    border-radius: 8px;
    border: 1px solid var(--border-color);
}

.pipeline-panel__result {
    background-color: var(--success-color-light);
    border-color: var(--success-color);
}

.pipeline-panel__error {
    background-color: var(--error-color-light);
    border-color: var(--error-color);
}

.pipeline-panel__result-title,
.pipeline-panel__error-title {
    font-size: 1.125rem;
    font-weight: 600;
    margin-bottom: 0.75rem;
}

.pipeline-panel__result-json {
    background-color: var(--bg-primary);
    padding: 1rem;
    border-radius: 4px;
    overflow-x: auto;
    font-size: 0.875rem;
    font-family: 'Consolas', 'Monaco', monospace;
    max-height: 400px;
    overflow-y: auto;
}

.pipeline-panel__error-message {
    color: var(--text-primary);
    line-height: 1.6;
}

@keyframes pulse {
    0%, 100% {
        opacity: 1;
    }
    50% {
        opacity: 0.7;
    }
}
```

---

### Task 6: Frontend - Integrate Pipeline Panel into App (45 min)

**File:** `src/gaia/apps/webui/src/App.tsx`

**Changes Required:**

1. Add import for PipelinePanel (after existing imports):
```tsx
import { PipelinePanel } from './components/PipelinePanel';
```

2. Add route/tab for PipelinePanel in the appropriate location (likely in the sidebar or as a new tab in ChatView)

**Integration Options:**

**Option A: Add as Sidebar Tab** (Recommended)
- Add "Pipeline" icon to Sidebar.tsx
- Show PipelinePanel when Pipeline tab is selected

**Option B: Add as ChatView Section**
- Add PipelinePanel as an expandable section in ChatView.tsx
- Users can toggle between chat and pipeline views

**Option C: Add as Separate Route**
- Add `/pipeline` route to the app router
- Render PipelinePanel on pipeline route

---

## Test Strategy

### Backend Tests

1. **Unit Test** - Test the new endpoint directly:
```python
# tests/unit/test_pipeline_router.py
def test_run_pipeline_endpoint(test_client):
    response = test_client.post("/api/v1/pipeline/run", json={
        "task": "Build a calculator app",
        "auto_spawn": False
    })
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream"
```

2. **Integration Test** - Test with real PipelineOrchestrator:
```python
def test_pipeline_execution_streams_events():
    # Verify SSE events are generated correctly
    # Verify stage progress events fire in order
    # Verify completion event contains expected data
```

### Frontend Tests

1. **Component Test** - Test PipelinePanel rendering:
```typescript
// src/gaia/apps/webui/src/components/__tests__/PipelinePanel.test.tsx
describe('PipelinePanel', () => {
    it('renders task input and run button', () => {
        render(<PipelinePanel />);
        expect(screen.getByPlaceholderText(/task/i)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /run pipeline/i })).toBeInTheDocument();
    });
    
    it('shows progress stages when running', async () => {
        // Mock runPipeline API
        // Trigger run and verify stages appear
    });
});
```

2. **Service Test** - Test pipeline.ts API service:
```typescript
// src/gaia/apps/webui/src/services/__tests__/pipeline.test.ts
describe('runPipeline', () => {
    it('calls SSE endpoint and handles events', () => {
        // Mock fetch
        // Verify callbacks are called with correct events
    });
});
```

---

## Risk Assessment

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| PipelineOrchestrator blocks event loop | HIGH | MEDIUM | Use thread pool executor (implemented) |
| SSE streaming fails on Windows | MEDIUM | LOW | Test with `--reload` flag disabled |
| Frontend build fails due to TypeScript errors | LOW | LOW | Run `npm run build` after changes |
| Large responses cause memory issues | MEDIUM | LOW | Add response size limits if needed |

---

## Acceptance Criteria

- [ ] `POST /api/v1/pipeline/run` endpoint responds with 200 OK and SSE stream
- [ ] SSE events stream pipeline stage progress (Stage 1-5)
- [ ] Pipeline panel visible in Agent UI
- [ ] Users can submit tasks and view results in browser
- [ ] Error handling for Lemonade unavailability
- [ ] TypeScript compiles without errors
- [ ] Frontend builds successfully with `npm run build`

---

## Files to Modify

| File | Action | Lines |
|------|--------|-------|
| `src/gaia/ui/routers/pipeline.py` | MODIFY | Add ~80 lines (endpoints) |
| `src/gaia/apps/webui/src/services/pipeline.ts` | CREATE | ~180 lines |
| `src/gaia/apps/webui/src/components/PipelinePanel.tsx` | CREATE | ~200 lines |
| `src/gaia/apps/webui/src/components/PipelinePanel.css` | CREATE | ~150 lines |
| `src/gaia/apps/webui/src/App.tsx` | MODIFY | Add imports/routes |

---

## Dependencies

- **None** - This task is unblocked and can be executed immediately
- **Blocks:** WIRE-2 (RoutingAgent update to route pipeline-capable requests)

---

**Document Version:** 1.0  
**Prepared By:** Jordan Lee, Senior Software Developer  
**Date:** 2026-04-11
