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
    File,
    ShieldAlert,
    Brain,
    MessageCircleQuestionMark,
    Circle,
    type LucideIcon,
} from 'lucide-react';
import type { AgentStep, CommandOutput, RetrievalChunk } from '../types';
import * as api from '../services/api';
import { CodeBlock } from './PermissionPrompt';
import { log } from '../utils/logger';
import { StepResult } from './LongContent';
import './AgentActivity.css';

// ── Tool metadata: friendly names, icons, colors ──────────────────────────

interface ToolMeta {
    label: string;
    activeLabel: string;
    icon: LucideIcon;
}

const TOOL_META: Record<string, ToolMeta> = {
    // File operations
    search_file:           { label: 'Searched files',     activeLabel: 'Searching files',     icon: Search },
    search_files:          { label: 'Searched files',     activeLabel: 'Searching files',     icon: Search },
    search_file_content:   { label: 'Searched content',   activeLabel: 'Searching content',   icon: Search },
    search_directory:      { label: 'Searched directory', activeLabel: 'Searching directory', icon: Search },
    read_file:             { label: 'Read file',          activeLabel: 'Reading file',        icon: FileText },
    write_file:            { label: 'Wrote file',         activeLabel: 'Writing file',        icon: FileEdit },
    get_file_info:         { label: 'Got file info',      activeLabel: 'Getting file info',   icon: FileText },
    browse_directory:      { label: 'Browsed directory',  activeLabel: 'Browsing directory',  icon: FolderOpen },
    list_directory:        { label: 'Listed directory',   activeLabel: 'Listing directory',   icon: FolderOpen },
    list_recent_files:     { label: 'Listed recent files', activeLabel: 'Listing recent files', icon: FolderOpen },
    analyze_data_file:     { label: 'Analyzed data',      activeLabel: 'Analyzing data',      icon: BarChart3 },
    // Shell & code
    run_shell_command:     { label: 'Ran command',        activeLabel: 'Running command',     icon: Terminal },
    execute_code:          { label: 'Executed code',      activeLabel: 'Executing code',      icon: Code2 },
    // RAG & documents
    query_documents:       { label: 'Queried documents',  activeLabel: 'Querying documents',  icon: BookOpen },
    query_specific_file:   { label: 'Queried file',       activeLabel: 'Querying file',       icon: BookOpen },
    search_indexed_chunks: { label: 'Searched chunks',    activeLabel: 'Searching chunks',    icon: BookOpen },
    semantic_search:       { label: 'Searched documents', activeLabel: 'Searching documents', icon: BookOpen },
    evaluate_retrieval:    { label: 'Evaluated retrieval', activeLabel: 'Evaluating retrieval', icon: BookOpen },
    index_document:        { label: 'Indexed document',   activeLabel: 'Indexing document',   icon: Database },
    index_documents:       { label: 'Indexed documents',  activeLabel: 'Indexing documents',  icon: Database },
    index_directory:       { label: 'Indexed directory',  activeLabel: 'Indexing directory',   icon: Database },
    index_file:            { label: 'Indexed file',       activeLabel: 'Indexing file',       icon: Database },
    list_indexed_documents: { label: 'Listed documents',  activeLabel: 'Listing documents',   icon: Database },
    summarize_document:    { label: 'Summarized',         activeLabel: 'Summarizing',         icon: FileText },
    dump_document:         { label: 'Extracted text',     activeLabel: 'Extracting text',     icon: FileText },
    rag_status:            { label: 'Checked RAG status', activeLabel: 'Checking RAG',        icon: Database },
    add_watch_directory:   { label: 'Added watch dir',    activeLabel: 'Adding watch dir',    icon: FolderOpen },
    // Web
    web_search:            { label: 'Searched web',       activeLabel: 'Searching web',       icon: Globe },
    search_web:            { label: 'Searched web',       activeLabel: 'Searching web',       icon: Globe },
    fetch_page:            { label: 'Read web page',      activeLabel: 'Reading web page',    icon: Globe },
    fetch_webpage:         { label: 'Read web page',      activeLabel: 'Reading web page',    icon: Globe },
    analyze_data:          { label: 'Analyzed data',      activeLabel: 'Analyzing data',      icon: BarChart3 },
    // GAIA agent: files, memory, questions
    find_files:            { label: 'Found files',        activeLabel: 'Finding files',       icon: Search },
    edit_file:             { label: 'Edited file',        activeLabel: 'Editing file',        icon: FileEdit },
    recall:                { label: 'Recalled memory',    activeLabel: 'Recalling memory',    icon: Brain },
    remember:              { label: 'Saved to memory',    activeLabel: 'Saving to memory',    icon: Brain },
    update_memory:         { label: 'Updated memory',     activeLabel: 'Updating memory',     icon: Brain },
    forget:                { label: 'Deleted from memory', activeLabel: 'Deleting from memory', icon: Brain },
    search_past_conversations: { label: 'Searched past chats', activeLabel: 'Searching past chats', icon: Brain },
    request_user_input:    { label: 'Asked you',          activeLabel: 'Asking you',          icon: MessageCircleQuestionMark },
};

// Memory works in the background: its steps show only when they fail.
const BACKGROUND_TOOLS = new Set(['remember', 'recall', 'update_memory', 'forget']);

function isBackgroundStep(step: AgentStep): boolean {
    return step.type === 'tool' && step.success !== false && BACKGROUND_TOOLS.has(step.tool ?? '');
}

const DEFAULT_TOOL_META: ToolMeta = {
    label: 'Used tool', activeLabel: 'Using tool', icon: Wrench,
};

/** Which connector an MCP call went to, as the server names it. */
type McpIdentity = Pick<AgentStep, 'mcpServerLabel' | 'mcpTool'>;

function getToolMeta(toolName?: string, mcp?: McpIdentity): ToolMeta {
    if (!toolName) return DEFAULT_TOOL_META;
    if (toolName.startsWith('mcp_')) {
        const server = mcp?.mcpServerLabel;
        if (server) {
            const what = mcp?.mcpTool ? `${server}: ${mcp.mcpTool}` : server;
            return { label: `Used ${what}`, activeLabel: `Using ${what}`, icon: Globe };
        }
        // Saved turns from before the server named the connector.
        return { label: 'MCP tool', activeLabel: 'Running MCP tool', icon: Globe };
    }
    if (TOOL_META[toolName]) return TOOL_META[toolName];
    // A tool with no entry still says what it was, not a bare "Used tool".
    const words = toolName.replace(/[_-]+/g, ' ').trim();
    return words
        ? { ...DEFAULT_TOOL_META, label: `Used ${words}`, activeLabel: `Using ${words}` }
        : DEFAULT_TOOL_META;
}

/** The fallback line for a tool row that arrived without a narration (older saved turns). */
function toolFallbackLabel(step: AgentStep): string {
    const meta = getToolMeta(step.tool, step);
    // The done-label ("Wrote file") would claim a failed, denied or stopped call succeeded.
    const unfinished = step.success == null && !step.result;
    return step.active
        ? meta.activeLabel
        : step.success === false ? `${meta.activeLabel} failed`
        : unfinished ? `${meta.activeLabel} didn't finish` : meta.label;
}

/** The one plain line a tool row shows by default, and the live line while it runs. */
export function toolRowText(step: AgentStep): string {
    // Narration can only humanize the prefixed tool id; the connector's own name says more.
    if (step.mcpServerLabel) return toolFallbackLabel(step);
    return step.narration ? compactNarration(step.narration) : toolFallbackLabel(step);
}

const COMPACT_DETAIL_CHARS = 48;

/** Lines of an MCP tool's output shown before "Show all". */
const MCP_OUTPUT_PREVIEW_LINES = 5;

/** Below this many call rows the list is short enough to scan without a filter. */
const FILTER_MIN_TOOL_ROWS = 6;

/**
 * Shorten the detail of "Reading file: <long path>" to the part that differs
 * between similar rows (a path's tail, a URL's host and page) instead of
 * letting the row cut off at a shared prefix.
 */
export function compactNarration(text: string): string {
    const split = /^([^:\n]{1,40}): (.+)$/.exec(text);
    if (!split) return text;
    const [, base, detail] = split;
    if (/^https?:\/\//i.test(detail)) {
        const bare = detail.replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, '');
        const segments = bare.split('/').filter(Boolean);
        const short = bare.length > COMPACT_DETAIL_CHARS && segments.length > 2
            ? `${segments[0]}/…/${segments[segments.length - 1]}`
            : bare;
        return `${base}: ${short}`;
    }
    if (detail.length > COMPACT_DETAIL_CHARS && /[\\/]/.test(detail) && !/\s/.test(detail)) {
        const sep = detail.includes('\\') ? '\\' : '/';
        const segments = detail.split(/[\\/]/).filter(Boolean);
        if (segments.length > 2) return `${base}: …${sep}${segments.slice(-2).join(sep)}`;
    }
    return text;
}

export type PlanItemStatus = 'done' | 'running' | 'failed' | 'stopped' | 'pending';

/** A display row: an agent step, plus how many identical calls it stands for. */
export interface ActivityRow extends AgentStep {
    /** Identical tool calls merged into this row (absent for a single call). */
    count?: number;
    /** Per-plan-step status, parallel to planSteps (plan rows with a known current step). */
    planStatuses?: PlanItemStatus[];
}

/** The agent's own bookkeeping lines while it walks a plan; the checklist already says this. */
const PLAN_BOOKKEEPING_RE = /^(?:Executing step \d+ of the plan\.?|Following the plan to [\s\S]*)$/;

function samePlan(a?: string[], b?: string[]): boolean {
    return !!a && !!b && a.length === b.length && a.every((s, i) => s === b[i]);
}

function sameCall(a: AgentStep, b: AgentStep): boolean {
    return a.tool === b.tool && (a.detail ?? '') === (b.detail ?? '') && a.mcpServer === b.mcpServer;
}

/** Status of each plan step, from the step being executed and the calls made since. */
export function planStatuses(
    stepCount: number,
    current: number | undefined,
    callsSince: AgentStep[],
    isActive: boolean,
): PlanItemStatus[] | undefined {
    if (current == null || current < 0) return undefined;
    const last = callsSince[callsSince.length - 1];
    let currentStatus: PlanItemStatus;
    if (last?.active) currentStatus = 'running';
    else if (last?.success === false) currentStatus = 'failed';
    else if (last && (last.success === true || last.result)) currentStatus = 'done';
    else if (isActive) currentStatus = 'running';
    // A call with no outcome after the turn ended was cut short (Stop, timeout).
    else currentStatus = last ? 'stopped' : 'pending';
    return Array.from({ length: stepCount }, (_, i) =>
        i < current ? 'done' : i === current ? currentStatus : 'pending');
}

/**
 * Fold the raw step stream into display rows: merge runs of thinking/status,
 * keep one row per plan (the agent re-sends it before every step), and merge
 * identical tool calls into one ×N row.
 */
export function consolidateSteps(steps: AgentStep[], isActive: boolean): ActivityRow[] {
    const result: ActivityRow[] = [];
    // Per plan row: raw index of its latest copy, and where the next different plan starts.
    const planSpan = new Map<number, { anchor: number; end: number }>();
    let lastPlan: ActivityRow | undefined;
    let seenPlan = false;

    steps.forEach((step, rawIdx) => {
        if (isBackgroundStep(step)) return;
        const prev = result[result.length - 1];

        if (seenPlan && (step.type === 'thinking' || step.type === 'status')) {
            if (PLAN_BOOKKEEPING_RE.test((step.detail || step.label || '').trim())) return;
        }

        if (step.type === 'plan') {
            seenPlan = true;
            // Only a re-send of the latest plan folds in; a replan gets its own checklist.
            if (lastPlan && samePlan(lastPlan.planSteps, step.planSteps)) {
                if (step.currentStep != null) lastPlan.currentStep = step.currentStep;
                if (step.planNarrations) lastPlan.planNarrations = step.planNarrations;
                planSpan.get(lastPlan.id)!.anchor = rawIdx;
                return;
            }
            if (lastPlan) planSpan.get(lastPlan.id)!.end = rawIdx;
            lastPlan = { ...step };
            result.push(lastPlan);
            planSpan.set(step.id, { anchor: rawIdx, end: steps.length });
            return;
        }

        if (step.type === 'tool') {
            // Look back past thinking/status only: a plan or error between two calls keeps them apart.
            let j = result.length - 1;
            while (j >= 0 && (result[j].type === 'thinking' || result[j].type === 'status')) j--;
            const prevTool = j >= 0 ? result[j] : undefined;
            if (prevTool && prevTool.type === 'tool' && !prevTool.active && sameCall(prevTool, step)
                && (step.active || prevTool.success === step.success)) {
                result[j] = { ...step, id: prevTool.id, count: (prevTool.count ?? 1) + 1 };
                return;
            }
            result.push({ ...step });
            return;
        }

        // Merge consecutive thinking steps; concatenate so accumulated text survives.
        if (step.type === 'thinking' && prev && prev.type === 'thinking') {
            const merged = (prev.detail || '') + (step.detail || '');
            result[result.length - 1] = { ...step, detail: merged || prev.detail };
            return;
        }
        // Merge consecutive status steps
        if (step.type === 'status' && prev && prev.type === 'status' && step.active !== false) {
            result[result.length - 1] = { ...step, label: step.label || prev.label };
            return;
        }
        // Absorb thinking into adjacent status — keep thinking detail
        if (step.type === 'thinking' && prev && prev.type === 'status' && prev.active !== false) {
            result[result.length - 1] = { ...prev, type: 'thinking', detail: step.detail || prev.detail, active: step.active };
            return;
        }
        // Absorb status into adjacent thinking — preserve thinking detail
        if (step.type === 'status' && prev && prev.type === 'thinking') {
            result[result.length - 1] = { ...prev, label: step.label || prev.label, active: step.active ?? prev.active };
            return;
        }
        result.push({ ...step });
    });

    for (const row of result) {
        const span = planSpan.get(row.id);
        if (row.type !== 'plan' || !row.planSteps || !span) continue;
        const callsSince = steps.slice(span.anchor + 1, span.end).filter((s) => s.type === 'tool');
        // A plan the agent replaced is no longer running, whatever the turn is doing.
        const running = isActive && row === lastPlan;
        row.planStatuses = planStatuses(row.planSteps.length, row.currentStep, callsSince, running);
    }
    return result;
}

/** What a turn blocked on the user says, in the message header and its held tool row. */
export const WAITING_ON_USER_LABEL = 'Waiting for you';

/** `8` -> "8s", `125` -> "2m 05s". */
export function formatElapsed(seconds: number): string {
    const s = Math.max(0, Math.floor(seconds));
    if (s < 60) return `${s}s`;
    return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
}

/** Wall-clock milliseconds, re-rendering once a second while `ticking`. */
function useNow(ticking: boolean): number {
    const [now, setNow] = useState(() => Date.now());
    useEffect(() => {
        if (!ticking) return;
        setNow(Date.now());
        const timer = setInterval(() => setNow(Date.now()), 1000);
        return () => clearInterval(timer);
    }, [ticking]);
    return now;
}

// ── Component ─────────────────────────────────────────────────────────────

interface AgentActivityProps {
    steps: AgentStep[];
    /** Whether the agent is currently working. */
    isActive: boolean;
    /** Whether this is the inline version (during streaming) or the summary version (after). */
    variant?: 'inline' | 'summary';
    /** The agent's own step count; rows (thinking, plans, tools) are not steps. */
    agentStepCount?: number;
    /** The turn is blocked on the user (an approval or a question), so nothing is running. */
    awaitingUser?: boolean;
}

/** Displays agent activity as a single expandable "Thinking" panel with tool calls inline. */
export function AgentActivity({ steps, isActive, variant = 'inline', agentStepCount, awaitingUser = false }: AgentActivityProps) {
    // Always start collapsed — thinking text is shown via ThinkingIndicator
    // in the message header. Users can expand to see details if they want.
    const [expanded, setExpanded] = useState(false);
    const [expandedTools, setExpandedTools] = useState<Set<number>>(new Set());
    const collapseTimersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map());
    const prevIsActiveRef = useRef(isActive);

    // Track isActive transitions (used by other effects if needed)
    useEffect(() => {
        prevIsActiveRef.current = isActive;
    }, [isActive]);

    // Cleanup timers on unmount
    useEffect(() => {
        return () => {
            collapseTimersRef.current.forEach((timer) => clearTimeout(timer));
            collapseTimersRef.current.clear();
        };
    }, []);

    const displaySteps = useMemo(() => consolidateSteps(steps, isActive), [steps, isActive]);
    const now = useNow(isActive);

    const toolSteps = displaySteps.filter((s) => s.type === 'tool');
    const toolCallCount = toolSteps.reduce((n, s) => n + (s.count ?? 1), 0);
    // While the turn runs, the latest plan stays in view as a live checklist.
    const livePlan = isActive ? [...displaySteps].reverse().find((s) => s.type === 'plan' && s.planSteps?.length) : undefined;
    const policySteps = displaySteps.filter((s) => s.type === 'policy_alert');
    const hasPolicyAlerts = policySteps.length > 0;

    // ── Filter state (for MCP tool visualization) ───────────────────
    const [toolNameFilter, setToolNameFilter] = useState('');
    const [statusFilter, setStatusFilter] = useState<'all' | 'success' | 'error'>('all');

    // filteredSteps drives rendering; toolSteps/errorSteps use unfiltered
    // displaySteps so counters and the filter-bar threshold stay stable.
    const filteredSteps = useMemo(() => {
        if (!toolNameFilter && statusFilter === 'all') return displaySteps;
        return displaySteps.filter(s => {
            if (s.type !== 'tool') return true;
            const needle = toolNameFilter.toLowerCase();
            if (needle && !s.tool?.toLowerCase().includes(needle)
                && !toolRowText(s).toLowerCase().includes(needle)) return false;
            if (statusFilter === 'success' && s.success !== true) return false;
            if (statusFilter === 'error' && s.success !== false) return false;
            return true;
        });
    }, [displaySteps, toolNameFilter, statusFilter]);

    const errorSteps = displaySteps.filter((s) => s.type === 'error');
    const hasErrors = errorSteps.length > 0;

    // A turn-level error is often the turn's only output; never hide it behind a click.
    useEffect(() => {
        if (hasPolicyAlerts || hasErrors) setExpanded(true);
    }, [hasPolicyAlerts, hasErrors]);

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

    // Build summary — always use stable step count so the bar doesn't
    // visually change when transitioning from thinking to answer streaming.
    // Messages saved before the agent's count was recorded only have their rows to go by.
    const rowCount = displaySteps
        .filter((s) => s.type !== 'status')
        .reduce((n, s) => n + (s.count ?? 1), 0);
    // The agent's own count includes the hidden memory steps, so it would disagree with the list.
    const stepCount = agentStepCount && agentStepCount > 0 && !steps.some(isBackgroundStep)
        ? agentStepCount
        : rowCount || displaySteps.length;
    const summaryText = hasPolicyAlerts
        ? `Blocked by policy \u00b7 ${policySteps.length} alert${policySteps.length !== 1 ? 's' : ''}`
        : `${stepCount} step${stepCount !== 1 ? 's' : ''}`
            + (toolCallCount > 0 ? ` \u00b7 ${toolCallCount} tool${toolCallCount !== 1 ? 's' : ''}` : '');

    return (
        <div className={`agent-activity ${variant} ${isActive ? 'active' : 'done'} ${hasErrors ? 'has-errors' : ''} ${hasPolicyAlerts ? 'has-policy-alerts' : ''}`}>
            {/* Summary bar */}
            <button
                className="agent-summary-bar"
                onClick={() => setExpanded(!expanded)}
                aria-expanded={expanded}
                aria-label={expanded ? 'Collapse agent activity' : 'Expand agent activity'}
            >
                <div className="agent-summary-left">
                    {hasErrors ? (
                        <AlertCircle size={14} className="agent-icon-error" />
                    ) : isActive ? (
                        <Loader2 size={14} className="flow-tool-spinner" />
                    ) : (
                        <Zap size={14} className="agent-icon-done" />
                    )}
                    <span className="agent-summary-text">{summaryText}</span>
                </div>
                <div className="agent-summary-right">
                    {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </div>
            </button>

            {livePlan && <PlanChecklist step={livePlan} />}

            {/* Flow content — thinking text + inline tool cards.
                Uses a wrapper div that's always mounted so CSS can animate
                the height transition on collapse/expand. */}
            {displaySteps.length > 0 && (
                // Inert while collapsed: invisible buttons must not be tabbable or announced.
                <div className={`agent-flow-wrap ${expanded ? 'flow-expanded' : 'flow-collapsed'}`} inert={!expanded}>
                    <div className="agent-flow">
                        {filteredSteps.map((step) => {
                            if (step.type === 'thinking') {
                                return <FlowThought key={step.id} step={step} live={isActive && !!step.active} />;
                            }
                            if (step.type === 'status') {
                                // Progress notes ("Sending to model…") mean nothing once the turn is over.
                                return isActive ? <FlowStatus key={step.id} step={step} /> : null;
                            }
                            if (step.type === 'tool') {
                                return (
                                    <FlowToolCard
                                        key={step.id}
                                        step={step}
                                        isExpanded={expandedTools.has(step.id)}
                                        now={now}
                                        awaitingUser={isActive && awaitingUser}
                                        onToggle={() => toggleTool(step.id)}
                                    />
                                );
                            }
                            if (step.type === 'plan') {
                                // While the turn runs, the live checklist above stands in for it.
                                return step === livePlan ? null : <PlanChecklist key={step.id} step={step} />;
                            }
                            if (step.type === 'error') {
                                return <FlowError key={step.id} step={step} />;
                            }
                            if (step.type === 'policy_alert') {
                                return <FlowPolicyAlert key={step.id} step={step} />;
                            }
                            return null;
                        })}
                    </div>
                    {/* Filter bar: after the steps, and only once there are enough calls to sift */}
                    {expanded && toolSteps.length >= FILTER_MIN_TOOL_ROWS && (
                        <div className="activity-filter-bar">
                            <input
                                type="text"
                                className="activity-filter-input"
                                placeholder="Filter steps…"
                                aria-label="Filter by tool name"
                                value={toolNameFilter}
                                onChange={(e) => setToolNameFilter(e.target.value)}
                            />
                            <select
                                className="activity-filter-select"
                                aria-label="Filter by status"
                                value={statusFilter}
                                onChange={(e) => setStatusFilter(e.target.value as 'all' | 'success' | 'error')}
                            >
                                <option value="all">All</option>
                                <option value="success">Success</option>
                                <option value="error">Error</option>
                            </select>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

// ── Flow: Reasoning (collapsed unless it is streaming right now) ──────────

function FlowThought({ step, live }: { step: AgentStep; live: boolean }) {
    const text = step.detail || step.label || '';
    const [open, setOpen] = useState(live);
    const containerRef = useRef<HTMLDivElement>(null);

    // Fold the reasoning away once the agent moves on or the answer arrives.
    useEffect(() => {
        if (!live) setOpen(false);
    }, [live]);

    // Auto-scroll to keep cursor visible as text streams in
    useEffect(() => {
        if (live && open && containerRef.current) {
            containerRef.current.scrollTop = containerRef.current.scrollHeight;
        }
    }, [text, live, open]);

    if (!text) return null;
    const words = text.trim().split(/\s+/).length;

    return (
        <div className="flow-reasoning">
            <button
                className="flow-reasoning-toggle"
                onClick={() => setOpen((o) => !o)}
                aria-expanded={open}
            >
                <Brain size={13} className="flow-tool-icon" />
                <span>{live ? 'Thinking' : 'Reasoning'}</span>
                <span className="flow-reasoning-count">{words} word{words !== 1 ? 's' : ''}</span>
                {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            </button>
            {open && (
                <div ref={containerRef} className="flow-thought-hacker">
                    <span>{text}</span>
                    {live && <span className="flow-thought-cursor" />}
                </div>
            )}
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
export function linkifyPaths(text: string): React.ReactNode {
    // A quoted path ('C:\My Docs\a.txt') runs to its closing quote, spaces
    // included, and the quotes stay outside the link. An unquoted one
    // (C:\...\file.ext, (C:\Users\...)) stops at whitespace or punctuation.
    // The lookbehind keeps the "s:/" in "https://" from reading as a drive.
    const pathRe = /(['"`])([A-Z]:[\\/][^'"`\n]*?)\1|(?<![\w/\\])[A-Z]:[\\/](?:[^\s*?"'`<>|,;)}\]]+[\\/])*[^\s*?"'`<>|,;)}\]]*/gi;
    const parts: React.ReactNode[] = [];
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = pathRe.exec(text)) !== null) {
        const quote = match[1] ?? '';
        if (match.index > lastIndex || quote) {
            parts.push(text.slice(lastIndex, match.index) + quote);
        }
        const rawMatch = quote ? match[2] : match[0];
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
        if (filePath.length < rawMatch.length || quote) {
            parts.push(rawMatch.slice(filePath.length) + quote);
        }
        lastIndex = match.index + match[0].length;
    }

    if (parts.length === 0) return text;
    if (lastIndex < text.length) parts.push(text.slice(lastIndex));
    return <>{parts}</>;
}

// ── Flow: File list from file search tools ───────────────────────────────

function FileListView({ fileList }: { fileList: { files: Array<Record<string, unknown>>; total: number } }) {
    const [expanded, setExpanded] = useState(false);
    const PREVIEW_COUNT = 5;
    const displayFiles = expanded ? fileList.files : fileList.files.slice(0, PREVIEW_COUNT);
    const remaining = fileList.total - PREVIEW_COUNT;

    const getFileName = (f: Record<string, unknown>, i: number): string => {
        const name = f.name || f.filename || f.file_name;
        if (name) return String(name);
        if (f.file_path) return String(f.file_path).replace(/\\/g, '/').split('/').pop() || `File ${i + 1}`;
        return `File ${i + 1}`;
    };

    const getDirectory = (f: Record<string, unknown>): string => {
        return f.directory ? String(f.directory) : '';
    };

    return (
        <div className="file-list-view">
            {displayFiles.map((f, i) => (
                <div key={i} className="file-list-item">
                    <File size={11} className="file-list-icon" />
                    <span className="file-list-name">{getFileName(f, i)}</span>
                    {getDirectory(f) && <span className="file-list-dir">{getDirectory(f)}</span>}
                </div>
            ))}
            {remaining > 0 && !expanded && (
                <button className="file-list-more" onClick={() => setExpanded(true)}>
                    +{remaining} more
                </button>
            )}
            {expanded && fileList.files.length < fileList.total && (
                <span className="file-list-truncated">Showing {fileList.files.length} of {fileList.total} files</span>
            )}
        </div>
    );
}

// ── Flow: Tool Card ──────────────────────────────────────────────────────

interface FlowToolCardProps {
    step: ActivityRow;
    isExpanded: boolean;
    onToggle: () => void;
    /** Wall-clock ms, for a running call's timer. */
    now: number;
    /** The turn is waiting on the user (an approval or a question). */
    awaitingUser?: boolean;
}

function FlowToolCard({ step, isExpanded, onToggle, now, awaitingUser = false }: FlowToolCardProps) {
    const Icon = getToolMeta(step.tool, step).icon;
    // A call held for the user's answer hasn't started, so it neither runs nor times.
    const waiting = !!step.active && awaitingUser;
    const line = waiting ? WAITING_ON_USER_LABEL : toolRowText(step);
    const count = step.count ?? 1;
    // A failed call's reason belongs on its row; a success's output stays one click deeper.
    const failure = step.success === false && step.result ? step.result : '';
    const timing = waiting
        ? ''
        : step.active
        ? formatElapsed((now - step.timestamp) / 1000)
        : step.latencyMs != null ? formatToolLatency(step.latencyMs) : '';

    return (
        <div className={`flow-tool ${step.active ? 'active' : ''} ${step.success === false ? 'error' : ''}`}>
            <button
                className="flow-tool-header"
                onClick={onToggle}
                aria-expanded={isExpanded}
                title={
                    waiting
                        ? toolRowText(step)
                        : step.narration && step.narration !== line ? step.narration : undefined
                }
            >
                <div className="flow-tool-left">
                    {waiting ? (
                        <Icon size={13} className="flow-tool-icon" />
                    ) : step.active ? (
                        <Loader2 size={13} className="flow-tool-spinner" />
                    ) : step.success === false ? (
                        <AlertCircle size={13} style={{ color: 'var(--danger)' }} />
                    ) : step.success === true ? (
                        <CheckCircle2 size={13} style={{ color: 'var(--accent-green)' }} />
                    ) : (
                        <Icon size={13} className="flow-tool-icon" />
                    )}
                    <span className="flow-tool-label">{line}</span>
                    {count > 1 && (
                        <span className="flow-tool-count" aria-label={`${count} identical calls`}>{'×'}{count}</span>
                    )}
                    {failure && <span className="flow-tool-failure">{failure}</span>}
                </div>
                <div className="flow-tool-right">
                    {timing && <span className="flow-tool-latency">{timing}</span>}
                    <span className={`flow-tool-chevron ${isExpanded ? 'expanded' : ''}`}>
                        <ChevronRight size={12} />
                    </span>
                </div>
            </button>

            {isExpanded && (
                <div className="flow-tool-detail">
                    <div className="flow-tool-raw">
                        <span className="detail-section-label">Tool</span>
                        <code className="flow-tool-badge">{step.tool || 'unknown'}</code>
                        {step.mcpServer && (
                            <span className="flow-tool-mcp-server">via {step.mcpServer}</span>
                        )}
                    </div>
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

                    {step.mcpOutput && (
                        <div className="step-detail-result result-success">
                            <span className="detail-section-label">Output</span>
                            <CodeBlock code={step.mcpOutput.text} previewLines={MCP_OUTPUT_PREVIEW_LINES} testId="mcp-output" />
                            {step.mcpOutput.truncated && (
                                <div className="cmd-truncated">Output was truncated (exceeded size limit)</div>
                            )}
                        </div>
                    )}

                    {/* Generic result */}
                    {step.result && !step.commandOutput && !step.mcpOutput && (
                        <div className={`step-detail-result ${step.success === false ? 'result-error' : 'result-success'}`}>
                            <span className="detail-section-label">
                                {step.success === false ? 'Error' : 'Result'}
                            </span>
                            <StepResult text={step.result} renderShort={linkifyPaths} />
                        </div>
                    )}
                    {/* Retrieved document chunks */}
                    {step.retrievalChunks && step.retrievalChunks.length > 0 && (
                        <ChunksView chunks={step.retrievalChunks} />
                    )}
                    {/* File list from file search tools */}
                    {step.fileList && step.fileList.files.length > 0 && (
                        <FileListView fileList={step.fileList} />
                    )}
                </div>
            )}
        </div>
    );
}

// ── Flow: Plan ───────────────────────────────────────────────────────────

const PLAN_STATUS_LABEL: Record<PlanItemStatus, string> = {
    done: 'Done', running: 'Running', failed: 'Failed', stopped: 'Stopped', pending: 'Not started',
};

function PlanStatusIcon({ status }: { status?: PlanItemStatus }) {
    if (status === 'done') return <CheckCircle2 size={13} className="plan-icon-done" />;
    if (status === 'running') return <Loader2 size={13} className="flow-tool-spinner" />;
    if (status === 'failed') return <AlertCircle size={13} className="plan-icon-failed" />;
    if (status === 'stopped') return <AlertCircle size={13} className="plan-icon-stopped" />;
    return <Circle size={13} className="plan-icon-pending" />;
}

/** The plan as a checklist: each step done, running, failed or not started. */
export function PlanChecklist({ step }: { step: ActivityRow }) {
    const items = step.planSteps ?? [];
    if (items.length === 0) return null;
    const statuses = step.planStatuses;
    const done = statuses?.filter((s) => s === 'done').length ?? 0;

    return (
        <div className="flow-plan">
            <div className="flow-plan-header">
                <ListChecks size={13} />
                <span>Plan</span>
                {statuses && <span className="flow-plan-count">{done} of {items.length} done</span>}
            </div>
            <ol className="flow-plan-list">
                {items.map((raw, i) => {
                    const status = statuses?.[i];
                    const text = compactNarration(step.planNarrations?.[i] || raw);
                    return (
                        <li
                            key={i}
                            className={`flow-plan-item ${status ?? ''}`}
                            title={raw}
                            aria-label={status ? `${PLAN_STATUS_LABEL[status]}: ${text}` : undefined}
                        >
                            <PlanStatusIcon status={status} />
                            <span className="flow-plan-text">{text}</span>
                        </li>
                    );
                })}
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

function FlowPolicyAlert({ step }: { step: AgentStep }) {
    return (
        <div className="flow-policy-alert">
            <div className="flow-policy-alert-header">
                <ShieldAlert size={14} />
                <span className="flow-policy-alert-title">Policy Shield</span>
                <span className="flow-policy-alert-decision">{step.decision || 'BLOCK'}</span>
            </div>
            <div className="flow-policy-alert-body">
                <div className="flow-policy-alert-line">
                    <span>Tool</span>
                    <code>{step.tool || 'unknown tool'}</code>
                </div>
                <div className="flow-policy-alert-reason">{step.reason || step.detail}</div>
                {step.ruleIds && step.ruleIds.length > 0 && (
                    <div className="flow-policy-alert-line">
                        <span>Rules</span>
                        <code>{step.ruleIds.join(', ')}</code>
                    </div>
                )}
                {step.policyVersion && (
                    <div className="flow-policy-alert-line">
                        <span>Policy</span>
                        <code>{step.policyVersion}</code>
                    </div>
                )}
                <div className="flow-policy-alert-line">
                    <span>Receipt</span>
                    {step.receiptId ? <code>{step.receiptId}</code> : <em>Receipt unavailable</em>}
                </div>
            </div>
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
                                    <BookOpen size={11} style={{ color: 'var(--accent-cyan)', flexShrink: 0 }} />
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
    const isError = output.returnCode !== 0 && !output.expectedExit;

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
                        <span
                            className={`cmd-exit-code${isError ? '' : ' cmd-exit-expected'}`}
                            title={output.expectedExit ?? undefined}
                        >
                            exit {output.returnCode}
                        </span>
                    )}
                    {hasOutput && (
                        <button
                            className={`cmd-copy ${copied ? 'copied' : ''}`}
                            onClick={handleCopy}
                            title={copied ? 'Copied!' : 'Copy output'}
                            aria-label={copied ? 'Copied to clipboard' : 'Copy command output'}
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

/** `47031` -> "47.0s", `312.4` -> "312ms", `0.3` -> "<1ms". */
export function formatToolLatency(ms: number): string {
    if (ms < 1) return '<1ms';
    if (ms < 1000) return `${Math.round(ms)}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
}

/** `['owner', 'task']` -> "owner, task"; anything else is returned as is. */
export function readableListValue(value: string): string {
    const list = /^\[(.*)\]$/s.exec(value);
    if (!list) return value;
    const items = list[1].split(/,\s*/).map((v) => v.trim().replace(/^(['"])(.*)\1$/s, '$2'));
    return items.filter(Boolean).join(', ');
}

/** `key: value`, where the colon is followed by a space — so `C:\x` is never read as key `C`. */
const ARG_ROW_RE = /^([A-Za-z_][\w.-]{0,29}):(?:\s+([\s\S]*))?$/;

export function formatArgsDisplay(detail: string): React.ReactNode {
    // Split on ", " only where a new `key: ` starts, so a list value stays on one row.
    const parts = detail.includes('\n')
        ? detail.split('\n').filter(Boolean)
        : detail.split(/, (?=[A-Za-z_][\w.-]{0,29}:(?:\s|$))/).filter(Boolean);

    if (parts.length <= 1) {
        return <span className="arg-value">{detail}</span>;
    }

    return (
        <div className="args-grid">
            {parts.map((part, i) => {
                const row = ARG_ROW_RE.exec(part.trim());
                if (row) {
                    const key = row[1];
                    const val = readableListValue((row[2] ?? '').trim());
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
