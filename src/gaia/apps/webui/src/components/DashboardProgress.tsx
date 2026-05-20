// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import React, { useEffect, useRef, useState, useCallback } from 'react';
import { X, Loader2, Trash2 } from 'lucide-react';
import { AgentActivity } from './AgentActivity';
import * as api from '../services/api';
import { log } from '../utils/logger';
import type { StreamEvent, AgentStep } from '../types';
import './ChatView.css';

interface DashboardProgressProps {
    sessionId: string;
    onClose?: () => void;
}

/**
 * Minimal dashboard progress strip that starts a streaming chat request
 * and renders agent activity + surfaced tool_result cards as they arrive.
 */
export default function DashboardProgress({ sessionId, onClose }: DashboardProgressProps) {
    const [running, setRunning] = useState(false);
    const [steps, setSteps] = useState<AgentStep[]>([]);
    const [surfaced, setSurfaced] = useState<Array<{ id: number; title?: string; summary?: string }>>([]);
    const stepIdRef = useRef(0);
    const abortRef = useRef<AbortController | null>(null);

    const pushStep = useCallback((step: AgentStep) => {
        setSteps((s) => [...s, step]);
    }, []);

    useEffect(() => {
        return () => {
            // Cleanup on unmount
            if (abortRef.current) {
                abortRef.current.abort();
                abortRef.current = null;
            }
        };
    }, []);

    const start = useCallback(() => {
        if (running) return;
        setRunning(true);
        setSteps([]);
        setSurfaced([]);

        const controller = api.sendMessageStream(sessionId, 'dashboard:refresh', {
            onChunk: (evt: StreamEvent) => {
                // ignore raw text chunks
            },
            onAgentEvent: (evt: StreamEvent) => {
                const id = ++stepIdRef.current;
                const ts = Date.now();
                if (evt.type === 'tool_start') {
                    pushStep({ id, type: 'tool', label: 'Using tool', tool: evt.tool, detail: evt.detail, active: true, timestamp: ts });
                } else if (evt.type === 'thinking') {
                    pushStep({ id, type: 'thinking', label: 'Thinking', detail: evt.content, active: true, timestamp: ts });
                } else if (evt.type === 'tool_result') {
                    // Mark previous tool step inactive and append result
                    pushStep({ id, type: 'tool', label: evt.title || 'Tool result', detail: evt.summary, active: false, success: evt.success, timestamp: ts });
                    // Add surfaced card item for animation
                    setSurfaced((s) => [...s, { id, title: evt.title, summary: evt.summary }]);
                } else if (evt.type === 'status') {
                    pushStep({ id, type: 'status', label: evt.message || 'Working', detail: evt.message, active: evt.status === 'working', timestamp: ts });
                }
            },
            onDone: (evt) => {
                log.stream.info('Dashboard stream done', evt);
                setRunning(false);
            },
            onError: (err) => {
                log.stream.error('Dashboard stream error', err);
                setRunning(false);
            }
        });

        abortRef.current = controller;
    }, [sessionId, running, pushStep]);

    const handleCancel = useCallback(async () => {
        try {
            await api.cancelStream(sessionId);
            setRunning(false);
            if (abortRef.current) {
                abortRef.current.abort();
                abortRef.current = null;
            }
        } catch (err) {
            log.stream.error('Cancel request failed', err);
        }
    }, [sessionId]);

    return (
        <div className="dashboard-progress">
            <div className="dashboard-progress-bar">
                <div className="dashboard-progress-left">
                    <Loader2 className={`spin ${running ? 'visible' : 'hidden'}`} />
                    <strong className="dashboard-title">Refresh progress</strong>
                </div>
                <div className="dashboard-controls">
                    <button className="btn btn-plain" onClick={() => start()} disabled={running} title="Start refresh">Start</button>
                    <button className="btn btn-plain" onClick={handleCancel} disabled={!running} title="Cancel refresh">Cancel</button>
                    <button className="btn btn-plain" onClick={() => { if (onClose) onClose(); }} title="Close">Close</button>
                </div>
            </div>

            <div className="dashboard-body">
                <div className="dashboard-steps">
                    <AgentActivity steps={steps} isActive={running} variant="inline" />
                </div>
                <div className="dashboard-surfaced">
                    <h4>Surfaced</h4>
                    <div className="surfaced-list">
                        {surfaced.map((c) => (
                            <div key={c.id} className="surfaced-card">
                                <div className="surfaced-card-body">
                                    <div className="surfaced-card-title">{c.title || 'Result'}</div>
                                    <div className="surfaced-card-summary">{c.summary}</div>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}
