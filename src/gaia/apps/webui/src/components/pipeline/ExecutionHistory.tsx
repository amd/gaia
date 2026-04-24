// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * ExecutionHistory - Panel showing past pipeline executions.
 *
 * Displays a list of past runs with status, duration, quality scores, and loop count.
 * Supports replay (re-run with same config) and deletion.
 */

import { memo, useState, useEffect, useCallback } from 'react';
import { History, Play, Trash2, ChevronDown, ChevronRight, RotateCcw, Clock, CheckCircle, AlertCircle, Loader2 } from 'lucide-react';

interface ExecutionEntry {
    pipeline_id: string;
    session_id: string;
    task_description: string;
    status: string;
    start_time: number;
    end_time: number;
    duration_seconds: number;
    quality_scores: number[];
    loop_count: number;
    decisions: Array<{ condition: string; decision: string }>;
    agents_used: string[];
    avg_quality: number | null;
}

interface ExecutionHistoryProps {
    onReplay?: (taskDescription: string, agentsUsed: string[]) => void;
}

const STATUS_ICONS: Record<string, React.ReactNode> = {
    completed: <CheckCircle size={14} />,
    failed: <AlertCircle size={14} />,
    running: <Loader2 size={14} className="spin" />,
    unknown: <Clock size={14} />,
};

const STATUS_COLORS: Record<string, string> = {
    completed: '#10b981',
    failed: '#ef4444',
    running: '#3b82f6',
    unknown: '#6b7280',
};

function ExecutionHistoryInner({ onReplay }: ExecutionHistoryProps) {
    const [executions, setExecutions] = useState<ExecutionEntry[]>([]);
    const [loading, setLoading] = useState(false);
    const [expandedId, setExpandedId] = useState<string | null>(null);

    const fetchExecutions = useCallback(async () => {
        setLoading(true);
        try {
            const res = await fetch('/api/v1/pipeline/executions?limit=50');
            const data = await res.json();
            setExecutions(data.executions || []);
        } catch (err) {
            console.error('Failed to fetch executions:', err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchExecutions();
    }, [fetchExecutions]);

    const handleReplay = async (pipelineId: string) => {
        try {
            const res = await fetch(`/api/v1/pipeline/executions/${pipelineId}/replay`, {
                method: 'POST',
            });
            const data = await res.json();
            onReplay?.(data.task_description, data.agents_used || []);
        } catch (err) {
            console.error('Failed to replay:', err);
        }
    };

    const handleDelete = async (pipelineId: string) => {
        try {
            await fetch(`/api/v1/pipeline/executions/${pipelineId}`, { method: 'DELETE' });
            setExecutions((prev) => prev.filter((e) => e.pipeline_id !== pipelineId));
        } catch (err) {
            console.error('Failed to delete:', err);
        }
    };

    const formatDuration = (seconds: number) => {
        if (seconds < 60) return `${seconds.toFixed(1)}s`;
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}m ${secs}s`;
    };

    const formatDate = (timestamp: number) => {
        return new Date(timestamp * 1000).toLocaleString();
    };

    if (loading && executions.length === 0) {
        return (
            <div className="pc-execution-history pc-execution-loading">
                <Loader2 size={20} className="spin" />
                <span>Loading execution history...</span>
            </div>
        );
    }

    return (
        <div className="pc-execution-history">
            <div className="pc-execution-history-header">
                <History size={16} />
                <span>Execution History</span>
                <span className="pc-execution-count">{executions.length} runs</span>
                <button
                    className="pc-btn pc-btn-secondary pc-refresh-btn"
                    onClick={fetchExecutions}
                    title="Refresh"
                >
                    <RotateCcw size={12} />
                </button>
            </div>

            {executions.length === 0 ? (
                <div className="pc-execution-empty">
                    <History size={24} strokeWidth={1} />
                    <span>No executions yet</span>
                    <span className="pc-execution-empty-hint">Run a pipeline to see history</span>
                </div>
            ) : (
                <div className="pc-execution-list">
                    {executions.map((exec) => {
                        const isExpanded = expandedId === exec.pipeline_id;
                        const statusColor = STATUS_COLORS[exec.status] || STATUS_COLORS.unknown;

                        return (
                            <div key={exec.pipeline_id} className="pc-execution-item">
                                <div
                                    className="pc-execution-item-header"
                                    onClick={() => setExpandedId(isExpanded ? null : exec.pipeline_id)}
                                    role="button"
                                    tabIndex={0}
                                >
                                    <div className="pc-exec-status" style={{ color: statusColor }}>
                                        {STATUS_ICONS[exec.status] || STATUS_ICONS.unknown}
                                    </div>
                                    <div className="pc-exec-info">
                                        <span className="pc-exec-task">
                                            {exec.task_description.slice(0, 60)}
                                            {exec.task_description.length > 60 ? '...' : ''}
                                        </span>
                                        <span className="pc-exec-meta">
                                            {formatDate(exec.start_time)} &middot; {formatDuration(exec.duration_seconds)}
                                        </span>
                                    </div>
                                    <div className="pc-exec-stats">
                                        {exec.avg_quality !== null && (
                                            <span className="pc-exec-quality">
                                                {(exec.avg_quality * 100).toFixed(0)}%
                                            </span>
                                        )}
                                        {exec.loop_count > 0 && (
                                            <span className="pc-exec-loops">
                                                {exec.loop_count} loop{exec.loop_count !== 1 ? 's' : ''}
                                            </span>
                                        )}
                                    </div>
                                    <div className="pc-exec-actions">
                                        {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                                    </div>
                                </div>

                                {isExpanded && (
                                    <div className="pc-execution-details">
                                        <div className="pc-exec-detail-row">
                                            <label>Pipeline ID</label>
                                            <span className="pc-exec-id">{exec.pipeline_id.slice(0, 12)}</span>
                                        </div>
                                        <div className="pc-exec-detail-row">
                                            <label>Session</label>
                                            <span>{exec.session_id.slice(0, 12)}</span>
                                        </div>
                                        {exec.quality_scores.length > 0 && (
                                            <div className="pc-exec-detail-row">
                                                <label>Quality Scores</label>
                                                <span className="pc-exec-quality-scores">
                                                    {exec.quality_scores.map((s, i) => (
                                                        <span key={i} className={`pc-quality-dot ${s >= 0.9 ? 'pass' : 'fail'}`} />
                                                    ))}
                                                    {' '}
                                                    {exec.quality_scores.map((s, i) => (s * 100).toFixed(0)).join(', ')}%
                                                </span>
                                            </div>
                                        )}
                                        {exec.agents_used.length > 0 && (
                                            <div className="pc-exec-detail-row">
                                                <label>Agents Used</label>
                                                <span>{exec.agents_used.length} agents</span>
                                            </div>
                                        )}
                                        <div className="pc-exec-detail-actions">
                                            <button
                                                className="pc-btn pc-btn-secondary pc-replay-btn"
                                                onClick={() => handleReplay(exec.pipeline_id)}
                                                title="Replay this execution"
                                            >
                                                <Play size={12} />
                                                Replay
                                            </button>
                                            <button
                                                className="pc-btn pc-btn-secondary pc-delete-btn"
                                                onClick={() => handleDelete(exec.pipeline_id)}
                                                title="Delete from history"
                                            >
                                                <Trash2 size={12} />
                                            </button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

export const ExecutionHistory = memo(ExecutionHistoryInner);
