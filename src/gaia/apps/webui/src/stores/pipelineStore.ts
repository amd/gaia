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

// -- State Interface ---------------------------------------------------------

interface PipelineState {
    // State
    executions: PipelineExecution[];
    activeExecution: PipelineExecution | null;
    lastResult: PipelineRunResponse | null;
    isRunning: boolean;
    isLoading: boolean;
    lastError: string | null;

    // State setters
    setExecutions: (executions: PipelineExecution[]) => void;
    setActiveExecution: (execution: PipelineExecution | null) => void;
    setLastResult: (result: PipelineRunResponse | null) => void;
    setIsRunning: (running: boolean) => void;
    setIsLoading: (loading: boolean) => void;
    setLastError: (error: string | null) => void;

    // Pipeline actions
    runPipeline: (request: PipelineRunRequest) => AbortController | null;
    runPipelineSync: (request: PipelineRunRequest) => Promise<PipelineRunResponse>;
    cancelPipeline: () => void;
    clearExecution: (id: string) => void;
    clearAllExecutions: () => void;
}

// -- Store Implementation ----------------------------------------------------

export const usePipelineStore = create<PipelineState>((set, get) => {
    let currentAbort: AbortController | null = null;

    const STAGE_LABELS: Record<string, PipelineExecution['currentStage']> = {
        'Stage 1': 'domain_analysis',
        'Stage 2': 'workflow_modeling',
        'Stage 3': 'loom_building',
        'Stage 4': 'gap_detection',
        'Stage 5': 'pipeline_execution',
    };

    function inferStage(message?: string): PipelineExecution['currentStage'] {
        if (!message) return undefined;
        for (const [key, stage] of Object.entries(STAGE_LABELS)) {
            if (message.includes(key)) return stage;
        }
        return 'pipeline_execution';
    }

    return {
        // Initial state
        executions: [],
        activeExecution: null,
        lastResult: null,
        isRunning: false,
        isLoading: false,
        lastError: null,

        // Setters
        setExecutions: (executions) => set({ executions }),
        setActiveExecution: (execution) => set({ activeExecution: execution }),
        setLastResult: (result) => set({ lastResult: result }),
        setIsRunning: (running) => set({ isRunning: running }),
        setIsLoading: (loading) => set({ isLoading: loading }),
        setLastError: (error) => set({ lastError: error }),

        // Run pipeline with SSE streaming
        runPipeline: (request) => {
            if (get().isRunning) {
                log.ui.warn('[pipelineStore] Pipeline already running');
                return null;
            }

            const execution: PipelineExecution = {
                id: crypto.randomUUID(),
                sessionId: request.session_id,
                taskDescription: request.task_description,
                status: 'starting',
                startTime: Date.now(),
                events: [],
            };

            set({
                executions: [...get().executions, execution],
                activeExecution: execution,
                isRunning: true,
                lastError: null,
            });

            log.ui.info(`[pipelineStore] Starting pipeline: ${request.task_description}`);

            currentAbort = runPipelineStream(request, {
                onStatus: (event) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                status: (event.status as PipelineExecution['status']) || 'running',
                                currentStage: inferStage(event.message),
                                events: [...state.activeExecution.events, event],
                            },
                        };
                    });
                },
                onStep: (event) => {
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
                onThinking: (event) => {
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
                onToolStart: (event) => {
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
                onToolEnd: (event) => {
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
                onToolResult: (event) => {
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
                onDone: (event) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        const updates: Partial<PipelineExecution> = {
                            status: 'completed',
                            endTime: Date.now(),
                            result: event.result,
                            events: [...state.activeExecution.events, event],
                        };
                        // Extract recursive pipeline metadata from done event
                        if ((event as any).loop_count !== undefined) {
                            updates.loopCount = (event as any).loop_count;
                        }
                        if ((event as any).quality_scores !== undefined) {
                            updates.qualityScores = (event as any).quality_scores;
                            const scores = updates.qualityScores as number[];
                            if (scores.length > 0) {
                                updates.latestQualityScore = scores[scores.length - 1];
                            }
                        }
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                ...updates,
                            },
                            isRunning: false,
                            lastResult: event.result as unknown as PipelineRunResponse,
                        };
                    });
                    log.ui.info('[pipelineStore] Pipeline complete');
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
                                events: [
                                    ...state.activeExecution.events,
                                    { type: 'agent_error', content: error.message },
                                ],
                            },
                            isRunning: false,
                            lastError: error.message,
                        };
                    });
                    log.ui.error('[pipelineStore] Pipeline failed:', error);
                },
                // Recursive pipeline event handlers
                onLoopBack: (event) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        const newIteration = (event as any).iteration ?? state.activeExecution.currentIteration ?? 1;
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                currentIteration: newIteration,
                                currentPhase: (event as any).target_phase ?? state.activeExecution.currentPhase,
                                events: [...state.activeExecution.events, event],
                            },
                        };
                    });
                    log.ui.info(`[pipelineStore] Loop back to ${(event as any).target_phase ?? 'prior phase'} (iteration ${(event as any).iteration ?? '?'})`);
                },
                onQualityScore: (event) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        const score = (event as any).quality_score;
                        const scores = state.activeExecution.qualityScores ?? [];
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                latestQualityScore: score,
                                qualityScores: [...scores, score],
                                events: [...state.activeExecution.events, event],
                            },
                        };
                    });
                },
                onPhaseJump: (event) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                currentPhase: (event as any).target_phase,
                                events: [...state.activeExecution.events, event],
                            },
                        };
                    });
                    log.ui.info(`[pipelineStore] Phase jump to ${(event as any).target_phase ?? '?'}`);
                },
                onIterationStart: (event) => {
                    set((state) => {
                        if (!state.activeExecution) return state;
                        const iter = (event as any).iteration ?? (state.activeExecution.currentIteration ?? 0) + 1;
                        return {
                            activeExecution: {
                                ...state.activeExecution,
                                currentIteration: iter,
                                events: [...state.activeExecution.events, event],
                            },
                        };
                    });
                },
                onIterationEnd: (event) => {
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
                onDefectFound: (event) => {
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
            });

            return currentAbort;
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
            if (currentAbort) {
                currentAbort.abort();
                currentAbort = null;
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
