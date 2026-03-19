// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import {
    ChevronDown,
    ChevronRight,
    Wrench,
    ListChecks,
    AlertCircle,
    CheckCircle2,
    Loader2,
    Zap,
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
    Copy,
    Check,
    type LucideIcon,
} from 'lucide-react';
import type { AgentStep, CommandOutput, RetrievalChunk } from '../types';
import * as api from '../services/api';
import { log } from '../utils/logger';
import './AgentActivity.css';

// ── Tool metadata: friendly names, icons, colors ──────────────────────────

interface ToolMeta {
    label: string;
    activeLabel: string;
    icon: LucideIcon;
    color: string;
}

const TOOL_META: Record<string, ToolMeta> = {
    // File operations
    search_file:           { label: 'Searched files',     activeLabel: 'Searching files',     icon: Search,     color: '#3b82f6' },
    search_files:          { label: 'Searched files',     activeLabel: 'Searching files',     icon: Search,     color: '#3b82f6' },
    search_file_content:   { label: 'Searched content',   activeLabel: 'Searching content',   icon: Search,     color: '#3b82f6' },
    search_directory:      { label: 'Searched directory', activeLabel: 'Searching directory', icon: Search,     color: '#3b82f6' },
    read_file:             { label: 'Read file',          activeLabel: 'Reading file',        icon: FileText,   color: '#8b5cf6' },
    write_file:            { label: 'Wrote file',         activeLabel: 'Writing file',        icon: FileEdit,   color: '#f59e0b' },
    get_file_info:         { label: 'Got file info',      activeLabel: 'Getting file info',   icon: FileText,   color: '#8b5cf6' },
    browse_directory:      { label: 'Browsed directory',  activeLabel: 'Browsing directory',  icon: FolderOpen,  color: '#a78bfa' },
    list_directory:        { label: 'Listed directory',   activeLabel: 'Listing directory',   icon: FolderOpen,  color: '#a78bfa' },
    list_recent_files:     { label: 'Listed recent files', activeLabel: 'Listing recent files', icon: FolderOpen, color: '#a78bfa' },
    analyze_data_file:     { label: 'Analyzed data',      activeLabel: 'Analyzing data',      icon: BarChart3,   color: '#ec4899' },
    // Shell & code
    run_shell_command:     { label: 'Ran command',        activeLabel: 'Running command',     icon: Terminal,    color: '#22c55e' },
    execute_code:          { label: 'Executed code',      activeLabel: 'Executing code',      icon: Code2,       color: '#f59e0b' },
    // RAG & documents
    query_documents:       { label: 'Queried documents',  activeLabel: 'Querying documents',  icon: BookOpen,    color: '#06b6d4' },
    query_specific_file:   { label: 'Queried file',       activeLabel: 'Querying file',       icon: BookOpen,    color: '#06b6d4' },
    search_indexed_chunks: { label: 'Searched chunks',    activeLabel: 'Searching chunks',    icon: BookOpen,    color: '#06b6d4' },
    semantic_search:       { label: 'Searched documents', activeLabel: 'Searching documents', icon: BookOpen,    color: '#06b6d4' },
    evaluate_retrieval:    { label: 'Evaluated retrieval', activeLabel: 'Evaluating retrieval', icon: BookOpen,  color: '#06b6d4' },
    index_document:        { label: 'Indexed document',   activeLabel: 'Indexing document',   icon: Database,    color: '#f97316' },
    index_directory:       { label: 'Indexed directory',  activeLabel: 'Indexing directory',   icon: Database,    color: '#f97316' },
    index_file:            { label: 'Indexed file',       activeLabel: 'Indexing file',       icon: Database,    color: '#f97316' },
    list_indexed_documents: { label: 'Listed documents',  activeLabel: 'Listing documents',   icon: Database,    color: '#f97316' },
    summarize_document:    { label: 'Summarized',         activeLabel: 'Summarizing',         icon: FileText,    color: '#8b5cf6' },
    dump_document:         { label: 'Extracted text',     activeLabel: 'Extracting text',     icon: FileText,    color: '#8b5cf6' },
    rag_status:            { label: 'Checked RAG status', activeLabel: 'Checking RAG',        icon: Database,    color: '#f97316' },
    add_watch_directory:   { label: 'Added watch dir',    activeLabel: 'Adding watch dir',    icon: FolderOpen,  color: '#a78bfa' },
    // Web
    web_search:            { label: 'Searched web',       activeLabel: 'Searching web',       icon: Globe,       color: '#14b8a6' },
    analyze_data:          { label: 'Analyzed data',      activeLabel: 'Analyzing data',      icon: BarChart3,   color: '#ec4899' },
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

/** Displays agent activity as a single expandable "Thinking" panel with tool calls inline. */
export function AgentActivity({ steps, isActive, variant = 'inline' }: AgentActivityProps) {
    // Default to expanded so all activity is visible
    const [expanded, setExpanded] = useState(true);
    const [expandedTools, setExpandedTools] = useState<Set<number>>(new Set());
    const prevStepCountRef = useRef(0);
    const collapseTimersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());
    const wasActiveRef2 = useRef(isActive);

    // Auto-collapse when activity completes (thinking done → answer streaming)
    useEffect(() => {
        if (wasActiveRef2.current && !isActive) {
            // Small delay so erase animation can start before collapsing
            const timer = setTimeout(() => setExpanded(false), 300);
            return () => clearTimeout(timer);
        }
        wasActiveRef2.current = isActive;
    }, [isActive]);

    // Cleanup timers on unmount
    useEffect(() => {
        return () => {
            collapseTimersRef.current.forEach((timer) => clearTimeout(timer));
            collapseTimersRef.current.clear();
        };
    }, []);

    // ── Consolidate display steps ────────────────────────────────────
    // Merge consecutive thinking/status steps into one.
    const displaySteps = useMemo(() => {
        const result: AgentStep[] = [];
        for (const step of steps) {
            const prev = result[result.length - 1];
            // Merge consecutive thinking steps
            if (step.type === 'thinking' && prev && prev.type === 'thinking') {
                result[result.length - 1] = { ...step, detail: step.detail || prev.detail };
                continue;
            }
            // Merge consecutive status steps
            if (step.type === 'status' && prev && prev.type === 'status' && step.active !== false) {
                result[result.length - 1] = { ...step, label: step.label || prev.label };
                continue;
            }
            // Absorb thinking into adjacent status
            if (step.type === 'thinking' && prev && prev.type === 'status' && prev.active !== false) {
                result[result.length - 1] = { ...prev, detail: step.detail || prev.detail, active: step.active };
                continue;
            }
            // Absorb status into adjacent thinking
            if (step.type === 'status' && prev && prev.type === 'thinking') {
                result[result.length - 1] = { ...prev, label: step.label || prev.label, detail: step.detail || prev.detail, active: step.active ?? prev.active };
                continue;
            }
            result.push(step);
        }
        return result;
    }, [steps]);

    const toolSteps = displaySteps.filter((s) => s.type === 'tool');
    const errorSteps = displaySteps.filter((s) => s.type === 'error');
    const hasErrors = errorSteps.length > 0;

    // Keep all tools expanded — auto-collapse is disabled for now to
    // let users observe all activity. Will add adaptive collapse later.
    useEffect(() => {
        prevStepCountRef.current = displaySteps.length;
        const toolIds = displaySteps
            .filter((s) => s.type === 'tool')
            .map((s) => s.id);
        if (toolIds.length > 0) {
            setExpandedTools((prev) => {
                const next = new Set(prev);
                toolIds.forEach((id) => next.add(id));
                return next;
            });
        }
    }, [displaySteps]);

    const toggleTool = useCallback((id: number) => {
        // Clear any pending collapse timer when user manually toggles
        const timer = collapseTimersRef.current.get(id);
        if (timer) {
            clearTimeout(timer);
            collapseTimersRef.current.delete(id);
        }
        setExpandedTools((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }, []);

    // Don't render until there are real steps to show
    if (displaySteps.length === 0) return null;

    // Build summary content (can be string or JSX for animated dots)
    const activeStep = displaySteps.find((s) => s.active);
    let summaryText: string;
    let isThinking = false;

    if (isActive && activeStep) {
        if (activeStep.type === 'tool' && activeStep.tool) {
            summaryText = getToolMeta(activeStep.tool).activeLabel;
        } else if (activeStep.type === 'thinking') {
            summaryText = 'Thinking';
            isThinking = true;
        } else {
            summaryText = activeStep.label || 'Working...';
        }
    } else if (isActive) {
        summaryText = 'Thinking';
        isThinking = true;
    } else {
        const uniqueTools = [...new Set(toolSteps.map((s) => s.tool).filter(Boolean) as string[])];
        if (uniqueTools.length > 0) {
            const toolLabels = uniqueTools.slice(0, 3).map((t) => getToolMeta(t).label);
            summaryText = toolLabels.join(', ');
            if (uniqueTools.length > 3) summaryText += ` +${uniqueTools.length - 3} more`;
        } else {
            summaryText = `${displaySteps.length} step${displaySteps.length !== 1 ? 's' : ''}`;
        }
        if (toolSteps.length > 0) {
            summaryText += ` \u00b7 ${toolSteps.length} tool${toolSteps.length !== 1 ? 's' : ''}`;
        }
    }

    return (
        <div className={`agent-activity ${variant} ${isActive ? 'active' : 'done'} ${hasErrors ? 'has-errors' : ''}`}>
            {/* Summary bar */}
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
                    <span className="agent-summary-text">
                        {summaryText}
                        {isThinking && <span className="thinking-dots"><span>.</span><span>.</span><span>.</span></span>}
                    </span>
                </div>
                <div className="agent-summary-right">
                    {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </div>
            </button>

            {/* Flow content — thinking text + inline tool cards */}
            {expanded && displaySteps.length > 0 && (
                <div className="agent-flow">
                    {displaySteps.map((step) => {
                        if (step.type === 'thinking') {
                            return <FlowThought key={step.id} step={step} />;
                        }
                        if (step.type === 'status') {
                            return <FlowStatus key={step.id} step={step} />;
                        }
                        if (step.type === 'tool') {
                            return (
                                <FlowToolCard
                                    key={step.id}
                                    step={step}
                                    isExpanded={expandedTools.has(step.id)}
                                    onToggle={() => toggleTool(step.id)}
                                />
                            );
                        }
                        if (step.type === 'plan') {
                            return <FlowPlan key={step.id} step={step} />;
                        }
                        if (step.type === 'error') {
                            return <FlowError key={step.id} step={step} />;
                        }
                        return null;
                    })}
                </div>
            )}
        </div>
    );
}

// ── Flow: Thinking text (hacker-style with red cursor) ────────────────────

function FlowThought({ step }: { step: AgentStep }) {
    const text = step.detail || step.label || '';
    const containerRef = useRef<HTMLDivElement>(null);
    const cursorRef = useRef<HTMLSpanElement>(null);

    // Auto-scroll to keep cursor visible as text streams in
    useEffect(() => {
        if (step.active && containerRef.current) {
            containerRef.current.scrollTop = containerRef.current.scrollHeight;
        }
    }, [text, step.active]);

    if (!text) return null;

    return (
        <div ref={containerRef} className="flow-thought-hacker">
            <span>{text}</span>
            {step.active && <span ref={cursorRef} className="flow-thought-cursor" />}
        </div>
    );
}

// ── Flow: Status text (no cursor, simple text) ────────────────────────────

function FlowStatus({ step }: { step: AgentStep }) {
    const text = step.detail || step.label || '';
    if (!text) return null;
    return (
        <div className={`flow-thought ${step.active ? 'active' : ''}`}>
            <span className="flow-thought-text">{text}</span>
        </div>
    );
}

// ── Path Linkification (for tool results) ────────────────────────────────

/** Detect Windows absolute paths in text and make them clickable. */
function linkifyPaths(text: string): React.ReactNode {
    // Match Windows absolute paths: C:\...\file.ext or C:\...\folder\
    // Also match paths in parentheses: (C:\Users\...)
    const pathRe = /[A-Z]:[\\\/](?:[^\s*?"<>|,;)}\]]+[\\\/])*[^\s*?"<>|,;)}\]]*/gi;
    const parts: React.ReactNode[] = [];
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = pathRe.exec(text)) !== null) {
        if (match.index > lastIndex) {
            parts.push(text.slice(lastIndex, match.index));
        }
        const rawMatch = match[0];
        const filePath = rawMatch.replace(/[)}\]]+$/, ''); // trim trailing brackets
        const handleClick = () => {
            api.openFileOrFolder(filePath).catch((err) => log.ui.error('Failed to open path', err));
        };
        parts.push(
            <span
                key={match.index}
                className="tool-result-path"
                onClick={handleClick}
                title={`Open: ${filePath}`}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === 'Enter') handleClick(); }}
            >
                <FolderOpen size={10} style={{ flexShrink: 0, opacity: 0.7 }} />
                {filePath}
            </span>
        );
        // Advance past the full raw match; push trimmed trailing brackets as plain text
        if (filePath.length < rawMatch.length) {
            parts.push(rawMatch.slice(filePath.length));
        }
        lastIndex = match.index + rawMatch.length;
    }

    if (parts.length === 0) return text;
    if (lastIndex < text.length) parts.push(text.slice(lastIndex));
    return <>{parts}</>;
}

// ── Flow: Tool Card ──────────────────────────────────────────────────────

interface FlowToolCardProps {
    step: AgentStep;
    isExpanded: boolean;
    onToggle: () => void;
}

function FlowToolCard({ step, isExpanded, onToggle }: FlowToolCardProps) {
    const meta = getToolMeta(step.tool);
    const Icon = meta.icon;
    const color = meta.color;
    const friendlyLabel = step.active ? meta.activeLabel : meta.label;
    const hasDetail = !!(step.detail || step.result || step.commandOutput);

    return (
        <div className={`flow-tool ${step.active ? 'active' : ''} ${step.success === false ? 'error' : ''}`}>
            <button
                className="flow-tool-header"
                onClick={hasDetail ? onToggle : undefined}
                style={hasDetail ? undefined : { cursor: 'default' }}
            >
                <div className="flow-tool-left">
                    {step.active ? (
                        <Loader2 size={13} className="flow-tool-spinner" style={{ color }} />
                    ) : step.success === false ? (
                        <AlertCircle size={13} style={{ color: '#ef4444' }} />
                    ) : step.success === true ? (
                        <CheckCircle2 size={13} style={{ color: '#22c55e' }} />
                    ) : (
                        <Icon size={13} style={{ color }} />
                    )}
                    <span className="flow-tool-label">{friendlyLabel}</span>
                    <span className="flow-tool-badge" style={{ '--badge-color': color } as React.CSSProperties}>
                        {step.tool}
                    </span>
                </div>
                <div className="flow-tool-right">
                    {hasDetail && (
                        <span className={`flow-tool-chevron ${isExpanded ? 'expanded' : ''}`}>
                            <ChevronRight size={12} />
                        </span>
                    )}
                </div>
            </button>

            {isExpanded && hasDetail && (
                <div className="flow-tool-detail">
                    {/* Arguments (except for commands) */}
                    {step.detail && !step.commandOutput && (
                        <div className="step-detail-args">
                            <span className="detail-section-label">Arguments</span>
                            <div className="detail-args-content">
                                {formatArgsDisplay(step.detail)}
                            </div>
                        </div>
                    )}

                    {/* Command output - terminal style */}
                    {step.commandOutput && (
                        <CommandOutputView output={step.commandOutput} />
                    )}

                    {/* Generic result */}
                    {step.result && !step.commandOutput && (
                        <div className={`step-detail-result ${step.success === false ? 'result-error' : 'result-success'}`}>
                            <span className="detail-section-label">
                                {step.success === false ? 'Error' : 'Result'}
                            </span>
                            <div className="detail-result-content">{linkifyPaths(step.result)}</div>
                        </div>
                    )}
                    {/* Retrieved document chunks */}
                    {step.retrievalChunks && step.retrievalChunks.length > 0 && (
                        <ChunksView chunks={step.retrievalChunks} />
                    )}
                </div>
            )}
        </div>
    );
}

// ── Flow: Plan ───────────────────────────────────────────────────────────

function FlowPlan({ step }: { step: AgentStep }) {
    if (!step.planSteps || step.planSteps.length === 0) return null;

    return (
        <div className="flow-plan">
            <div className="flow-plan-header">
                <ListChecks size={12} />
                <span>Plan</span>
            </div>
            <ol className="flow-plan-list">
                {step.planSteps.map((ps, i) => (
                    <li key={i} className="flow-plan-item">{ps}</li>
                ))}
            </ol>
        </div>
    );
}

// ── Flow: Error ──────────────────────────────────────────────────────────

function FlowError({ step }: { step: AgentStep }) {
    return (
        <div className="flow-error">
            <AlertCircle size={13} />
            <span>{step.detail || step.label || 'An error occurred'}</span>
        </div>
    );
}

// ── Retrieval Chunks View ──────────────────────────────────────────────

function ChunksView({ chunks }: { chunks: RetrievalChunk[] }) {
    const [expandedChunks, setExpandedChunks] = useState<Set<number>>(new Set());

    const toggleChunk = (id: number) => {
        setExpandedChunks((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    return (
        <div className="chunks-view">
            <span className="detail-section-label">
                Retrieved Chunks ({chunks.length})
            </span>
            <div className="chunks-list">
                {chunks.map((chunk) => {
                    const isExpanded = expandedChunks.has(chunk.id);
                    return (
                        <div key={chunk.id} className={`chunk-card ${isExpanded ? 'expanded' : ''}`}>
                            <button
                                className="chunk-header"
                                onClick={() => toggleChunk(chunk.id)}
                            >
                                <div className="chunk-header-left">
                                    <BookOpen size={11} style={{ color: '#06b6d4', flexShrink: 0 }} />
                                    {chunk.source && (
                                        <span className="chunk-source">{chunk.source}</span>
                                    )}
                                    {chunk.page != null && (
                                        <span className="chunk-page">p.{chunk.page}</span>
                                    )}
                                    {chunk.score != null && chunk.score > 0 && (
                                        <span className="chunk-score">{chunk.score.toFixed(2)}</span>
                                    )}
                                </div>
                                <span className={`chunk-chevron ${isExpanded ? 'expanded' : ''}`}>
                                    <ChevronRight size={11} />
                                </span>
                            </button>
                            <div className={`chunk-body ${isExpanded ? 'show' : ''}`}>
                                {isExpanded ? (
                                    <pre className="chunk-content">{chunk.content}</pre>
                                ) : (
                                    <div className="chunk-preview">{chunk.preview}</div>
                                )}
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

// ── Command Output View (Terminal Style) ──────────────────────────────────

function CommandOutputView({ output }: { output: CommandOutput }) {
    const [copied, setCopied] = useState(false);
    const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        return () => {
            if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        };
    }, []);

    const handleCopy = useCallback(() => {
        const text = output.stdout || output.stderr || '';
        navigator.clipboard.writeText(text).catch(() => {});
        setCopied(true);
        if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
        copyTimerRef.current = setTimeout(() => setCopied(false), 2000);
    }, [output]);

    const hasOutput = !!(output.stdout || output.stderr);
    const isError = output.returnCode !== 0;

    return (
        <div className={`cmd-output ${isError ? 'cmd-error' : ''}`}>
            <div className="cmd-header">
                <div className="cmd-header-left">
                    <Terminal size={12} className="cmd-header-icon" />
                    <span className="cmd-header-title">Terminal</span>
                    {output.cwd && <span className="cmd-cwd">{output.cwd}</span>}
                </div>
                <div className="cmd-header-right">
                    {output.durationSeconds != null && (
                        <span className="cmd-duration">{output.durationSeconds.toFixed(1)}s</span>
                    )}
                    {output.returnCode !== 0 && (
                        <span className="cmd-exit-code">exit {output.returnCode}</span>
                    )}
                    {hasOutput && (
                        <button
                            className={`cmd-copy ${copied ? 'copied' : ''}`}
                            onClick={handleCopy}
                            title={copied ? 'Copied!' : 'Copy output'}
                        >
                            {copied ? <Check size={11} /> : <Copy size={11} />}
                        </button>
                    )}
                </div>
            </div>
            <div className="cmd-line">
                <span className="cmd-prompt">$</span>
                <span className="cmd-text">{output.command}</span>
            </div>
            {output.stdout && <pre className="cmd-stdout">{output.stdout}</pre>}
            {output.stderr && <pre className="cmd-stderr">{output.stderr}</pre>}
            {output.truncated && (
                <div className="cmd-truncated">Output was truncated (exceeded size limit)</div>
            )}
            {!hasOutput && <div className="cmd-empty">No output</div>}
        </div>
    );
}

// ── Helpers ───────────────────────────────────────────────────────────────

function formatArgsDisplay(detail: string): React.ReactNode {
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
