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
} from 'lucide-react';
import type { AgentStep } from '../types';
import './AgentActivity.css';

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

    // Summary bar text
    const activeStep = steps.find((s) => s.active);
    const summaryText = isActive
        ? (activeStep?.label || `${steps.length} step${steps.length !== 1 ? 's' : ''}...`)
        : `${steps.length} step${steps.length !== 1 ? 's' : ''}${toolSteps.length > 0 ? ` \u00b7 ${toolSteps.length} tool${toolSteps.length !== 1 ? 's' : ''}` : ''}`;

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
                        <Loader2 size={14} className="agent-spinner" />
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
                    {steps.map((step) => (
                        <AgentStepRow
                            key={step.id}
                            step={step}
                            isExpanded={expandedSteps.has(step.id)}
                            onToggle={() => toggleStep(step.id)}
                        />
                    ))}
                </div>
            )}

            {/* No empty placeholder - bar only renders when steps exist */}
        </div>
    );
}

// -- Step Row ------------------------------------------------------------------

interface AgentStepRowProps {
    step: AgentStep;
    isExpanded: boolean;
    onToggle: () => void;
}

function AgentStepRow({ step, isExpanded, onToggle }: AgentStepRowProps) {
    const hasDetail = !!(step.detail || step.result || step.planSteps);
    const Icon = stepIcon(step);

    return (
        <div className={`agent-step-row ${step.active ? 'active' : ''} ${step.type}`}>
            <button
                className="step-header"
                onClick={hasDetail ? onToggle : undefined}
                aria-expanded={hasDetail ? isExpanded : undefined}
                style={hasDetail ? undefined : { cursor: 'default' }}
            >
                <div className="step-left">
                    {step.active ? (
                        <Loader2 size={13} className="agent-spinner" />
                    ) : (
                        <Icon size={13} className={`step-icon step-icon-${step.type}`} />
                    )}
                    <span className="step-label">{step.label}</span>
                    {step.tool && <span className="step-tool-name">{step.tool}</span>}
                </div>
                <div className="step-right">
                    {step.success === true && !step.active && (
                        <CheckCircle2 size={12} className="step-check" />
                    )}
                    {step.success === false && (
                        <AlertCircle size={12} className="step-error-icon" />
                    )}
                    {hasDetail && (
                        isExpanded
                            ? <ChevronDown size={12} className="step-chevron" />
                            : <ChevronRight size={12} className="step-chevron" />
                    )}
                </div>
            </button>

            {/* Expandable detail */}
            {isExpanded && hasDetail && (
                <div className="step-detail">
                    {step.detail && (
                        <div className="step-detail-text">{step.detail}</div>
                    )}
                    {step.result && (
                        <div className="step-detail-result">
                            <span className="result-label">Result:</span> {step.result}
                        </div>
                    )}
                    {step.planSteps && step.planSteps.length > 0 && (
                        <ol className="step-plan-list">
                            {step.planSteps.map((ps, i) => (
                                <li key={i} className="step-plan-item">{ps}</li>
                            ))}
                        </ol>
                    )}
                </div>
            )}
        </div>
    );
}

// -- Helpers -------------------------------------------------------------------

function stepIcon(step: AgentStep) {
    switch (step.type) {
        case 'thinking': return Brain;
        case 'tool': return Wrench;
        case 'plan': return ListChecks;
        case 'error': return AlertCircle;
        case 'status': return Info;
        default: return Info;
    }
}
