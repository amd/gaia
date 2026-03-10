// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useCallback } from 'react';
import {
    ChevronDown,
    ChevronRight,
    Wrench,
    Brain,
    ListChecks,
    AlertCircle,
    CheckCircle2,
    Loader2,
    Zap,
    Info,
    Search,
    FileText,
    Terminal,
    BookOpen,
    Database,
    FolderOpen,
    BarChart3,
    Globe,
    Code2,
    FileEdit,
    type LucideIcon,
} from 'lucide-react';
import type { AgentStep } from '../types';
import './AgentActivity.css';

// ── Tool metadata: friendly names, icons, colors ──────────────────────────

interface ToolMeta {
    label: string;
    activeLabel: string;
    icon: LucideIcon;
    color: string;
}

const TOOL_META: Record<string, ToolMeta> = {
    search_file:       { label: 'Searched files',     activeLabel: 'Searching files',     icon: Search,     color: '#3b82f6' },
    search_files:      { label: 'Searched files',     activeLabel: 'Searching files',     icon: Search,     color: '#3b82f6' },
    read_file:         { label: 'Read file',          activeLabel: 'Reading file',        icon: FileText,   color: '#8b5cf6' },
    write_file:        { label: 'Wrote file',         activeLabel: 'Writing file',        icon: FileEdit,   color: '#f59e0b' },
    run_shell_command: { label: 'Ran command',        activeLabel: 'Running command',     icon: Terminal,    color: '#22c55e' },
    semantic_search:   { label: 'Searched documents', activeLabel: 'Searching documents', icon: BookOpen,    color: '#06b6d4' },
    index_document:    { label: 'Indexed document',   activeLabel: 'Indexing document',   icon: Database,    color: '#f97316' },
    index_file:        { label: 'Indexed file',       activeLabel: 'Indexing file',       icon: Database,    color: '#f97316' },
    list_directory:    { label: 'Listed directory',   activeLabel: 'Listing directory',   icon: FolderOpen,  color: '#a78bfa' },
    analyze_data:      { label: 'Analyzed data',      activeLabel: 'Analyzing data',      icon: BarChart3,   color: '#ec4899' },
    web_search:        { label: 'Searched web',       activeLabel: 'Searching web',       icon: Globe,       color: '#14b8a6' },
    execute_code:      { label: 'Executed code',      activeLabel: 'Executing code',      icon: Code2,       color: '#f59e0b' },
};

const DEFAULT_TOOL_META: ToolMeta = {
    label: 'Used tool', activeLabel: 'Using tool', icon: Wrench, color: '#3b82f6',
};

function getToolMeta(toolName?: string): ToolMeta {
    if (!toolName) return DEFAULT_TOOL_META;
    return TOOL_META[toolName] || DEFAULT_TOOL_META;
}

// ── Component ─────────────────────────────────────────────────────────────

interface AgentActivityProps {
    steps: AgentStep[];
    /** Whether the agent is currently working. */
    isActive: boolean;
    /** Whether this is the inline version (during streaming) or the summary version (after). */
    variant?: 'inline' | 'summary';
}

/** Displays agent activity with collapsible step details. */
export function AgentActivity({ steps, isActive, variant = 'inline' }: AgentActivityProps) {
    const [expanded, setExpanded] = useState(variant === 'inline');
    const [expandedSteps, setExpandedSteps] = useState<Set<number>>(new Set());

    const toolSteps = steps.filter((s) => s.type === 'tool');
    const errorSteps = steps.filter((s) => s.type === 'error');
    const hasErrors = errorSteps.length > 0;

    const toggleStep = useCallback((id: number) => {
        setExpandedSteps((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }, []);

    // Don't render until there are real steps to show
    if (steps.length === 0) return null;

    // Build rich summary text
    const activeStep = steps.find((s) => s.active);
    let summaryText: string;
    let summaryTools: string[] = [];

    if (isActive && activeStep) {
        // Show active step's friendly label
        if (activeStep.type === 'tool' && activeStep.tool) {
            const meta = getToolMeta(activeStep.tool);
            summaryText = meta.activeLabel;
        } else {
            summaryText = activeStep.label || 'Working...';
        }
    } else if (isActive) {
        summaryText = `${steps.length} step${steps.length !== 1 ? 's' : ''}...`;
    } else {
        // Completed: show tool names used
        summaryTools = [...new Set(toolSteps.map((s) => s.tool).filter(Boolean) as string[])];
        if (summaryTools.length > 0) {
            const toolLabels = summaryTools.slice(0, 3).map((t) => getToolMeta(t).label);
            summaryText = toolLabels.join(', ');
            if (summaryTools.length > 3) summaryText += ` +${summaryTools.length - 3} more`;
        } else {
            summaryText = `${steps.length} step${steps.length !== 1 ? 's' : ''}`;
        }
        summaryText += ` \u00b7 ${toolSteps.length} tool${toolSteps.length !== 1 ? 's' : ''}`;
    }

    return (
        <div className={`agent-activity ${variant} ${isActive ? 'active' : 'done'} ${hasErrors ? 'has-errors' : ''}`}>
            {/* Summary bar - always visible */}
            <button
                className="agent-summary-bar"
                onClick={() => setExpanded(!expanded)}
                aria-expanded={expanded}
                aria-label={expanded ? 'Collapse agent activity' : 'Expand agent activity'}
            >
                <div className="agent-summary-left">
                    {isActive ? (
                        <div className="agent-spinner-wrap">
                            <Loader2 size={14} className="agent-spinner" />
                        </div>
                    ) : hasErrors ? (
                        <AlertCircle size={14} className="agent-icon-error" />
                    ) : (
                        <Zap size={14} className="agent-icon-done" />
                    )}
                    <span className="agent-summary-text">{summaryText}</span>
                </div>
                <div className="agent-summary-right">
                    {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </div>
            </button>

            {/* Expanded step list */}
            {expanded && steps.length > 0 && (
                <div className="agent-steps">
                    {steps.map((step, index) => (
                        <AgentStepRow
                            key={step.id}
                            step={step}
                            isExpanded={expandedSteps.has(step.id)}
                            onToggle={() => toggleStep(step.id)}
                            isLast={index === steps.length - 1}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}

// ── Step Row ──────────────────────────────────────────────────────────────

interface AgentStepRowProps {
    step: AgentStep;
    isExpanded: boolean;
    onToggle: () => void;
    isLast: boolean;
}

function AgentStepRow({ step, isExpanded, onToggle, isLast }: AgentStepRowProps) {
    const hasDetail = !!(step.detail || step.result || step.planSteps);
    const meta = step.type === 'tool' ? getToolMeta(step.tool) : null;
    const Icon = meta?.icon || stepIcon(step);
    const iconColor = meta?.color || stepIconColor(step);
    const friendlyLabel = step.type === 'tool'
        ? (step.active ? (meta?.activeLabel || step.label) : (meta?.label || step.label))
        : step.label;

    return (
        <div className={`agent-step-row ${step.active ? 'active' : ''} ${step.type} ${isLast ? 'last' : ''}`}>
            {/* Timeline connector */}
            <div className="step-timeline">
                <div
                    className={`step-dot ${step.active ? 'dot-active' : ''} ${step.success === false ? 'dot-error' : ''}`}
                    style={{ '--dot-color': iconColor } as React.CSSProperties}
                >
                    {step.active ? (
                        <Loader2 size={10} className="dot-spinner" />
                    ) : step.success === false ? (
                        <AlertCircle size={10} />
                    ) : step.success === true ? (
                        <CheckCircle2 size={10} />
                    ) : (
                        <Icon size={10} />
                    )}
                </div>
                {!isLast && <div className="step-line" />}
            </div>

            {/* Step content */}
            <div className="step-content">
                <button
                    className="step-header"
                    onClick={hasDetail ? onToggle : undefined}
                    aria-expanded={hasDetail ? isExpanded : undefined}
                    style={hasDetail ? undefined : { cursor: 'default' }}
                >
                    <div className="step-left">
                        <Icon size={14} className="step-icon" style={{ color: iconColor }} />
                        <span className="step-label">{friendlyLabel}</span>
                        {step.tool && (
                            <span className="step-tool-badge" style={{ '--badge-color': iconColor } as React.CSSProperties}>
                                {step.tool}
                            </span>
                        )}
                    </div>
                    <div className="step-right">
                        {step.success === true && !step.active && (
                            <CheckCircle2 size={13} className="step-check" />
                        )}
                        {step.success === false && (
                            <AlertCircle size={13} className="step-error-icon" />
                        )}
                        {hasDetail && (
                            <span className={`step-chevron-wrap ${isExpanded ? 'expanded' : ''}`}>
                                <ChevronRight size={13} className="step-chevron" />
                            </span>
                        )}
                    </div>
                </button>

                {/* Expandable detail */}
                {isExpanded && hasDetail && (
                    <div className="step-detail">
                        {step.detail && (
                            <div className="step-detail-args">
                                <span className="detail-section-label">Arguments</span>
                                <div className="detail-args-content">
                                    {formatArgsDisplay(step.detail)}
                                </div>
                            </div>
                        )}
                        {step.result && (
                            <div className={`step-detail-result ${step.success === false ? 'result-error' : 'result-success'}`}>
                                <span className="detail-section-label">
                                    {step.success === false ? 'Error' : 'Result'}
                                </span>
                                <div className="detail-result-content">{step.result}</div>
                            </div>
                        )}
                        {step.planSteps && step.planSteps.length > 0 && (
                            <div className="step-detail-plan">
                                <span className="detail-section-label">Plan</span>
                                <ol className="step-plan-list">
                                    {step.planSteps.map((ps, i) => (
                                        <li key={i} className="step-plan-item">{ps}</li>
                                    ))}
                                </ol>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
}

// ── Helpers ───────────────────────────────────────────────────────────────

function formatArgsDisplay(detail: string): React.ReactNode {
    // Split comma-separated or newline-separated args into key-value pairs
    const parts = detail.includes('\n')
        ? detail.split('\n').filter(Boolean)
        : detail.split(', ').filter(Boolean);

    if (parts.length <= 1) {
        return <span className="arg-value">{detail}</span>;
    }

    return (
        <div className="args-grid">
            {parts.map((part, i) => {
                const colonIdx = part.indexOf(':');
                if (colonIdx > 0 && colonIdx < 30) {
                    const key = part.slice(0, colonIdx).trim();
                    const val = part.slice(colonIdx + 1).trim();
                    return (
                        <div key={i} className="arg-row">
                            <span className="arg-key">{key}</span>
                            <span className="arg-value">{val}</span>
                        </div>
                    );
                }
                return <div key={i} className="arg-row"><span className="arg-value">{part}</span></div>;
            })}
        </div>
    );
}

function stepIcon(step: AgentStep): LucideIcon {
    switch (step.type) {
        case 'thinking': return Brain;
        case 'tool': return Wrench;
        case 'plan': return ListChecks;
        case 'error': return AlertCircle;
        case 'status': return Info;
        default: return Info;
    }
}

function stepIconColor(step: AgentStep): string {
    switch (step.type) {
        case 'thinking': return '#8b5cf6';
        case 'tool': return '#3b82f6';
        case 'plan': return '#f59e0b';
        case 'error': return '#ef4444';
        case 'status': return '#6e7681';
        default: return '#6e7681';
    }
}
