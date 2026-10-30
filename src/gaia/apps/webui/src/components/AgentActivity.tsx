// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useState, useCallback, useEffect, useRef, useMemo } from 'react';
import {
    ChevronRight,
    Wrench,
    ListChecks,
    AlertCircle,
    CheckCircle2,
    Loader2,
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
    Clock,
    Sparkles,
    Link,
    type LucideIcon,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { SAFE_DISALLOWED_ELEMENTS, safeUrlTransform } from '../utils/markdown';
import type { AgentStep, CommandOutput, RetrievalChunk } from '../types';
import * as api from '../services/api';
import { CodeBlock } from './PermissionPrompt';
import { log } from '../utils/logger';
import { StepResult } from './LongContent';
import { isMessageKey, ref, type MessageKey, type MessageRef } from '../i18n/registry';
import { tEn, useT, type Translate } from '../i18n/useT';
import './AgentActivity.css';

// ── Tool metadata: friendly names, icons, colors ──────────────────────────

interface ToolMeta {
    /** "Read file"; a message, so each language says it its own way. */
    label: MessageRef;
    /** "Reading file", also the stem of "Reading file failed". */
    activeLabel: MessageRef;
    icon: LucideIcon;
}

/** Each known tool's icon; its labels are `tool.<name>.done` / `.active` in src/i18n/locales. */
const TOOL_ICON: Record<string, LucideIcon> = {
    // File operations
    search_file: Search,
    search_files: Search,
    search_file_content: Search,
    search_directory: Search,
    read_file: FileText,
    write_file: FileEdit,
    get_file_info: FileText,
    browse_directory: FolderOpen,
    list_directory: FolderOpen,
    list_recent_files: FolderOpen,
    analyze_data_file: BarChart3,
    // Shell & code
    run_shell_command: Terminal,
    execute_code: Code2,
    // RAG & documents
    query_documents: BookOpen,
    query_specific_file: BookOpen,
    search_indexed_chunks: BookOpen,
    semantic_search: BookOpen,
    evaluate_retrieval: BookOpen,
    index_document: Database,
    index_documents: Database,
    index_directory: Database,
    index_file: Database,
    list_indexed_documents: Database,
    summarize_document: FileText,
    dump_document: FileText,
    rag_status: Database,
    add_watch_directory: FolderOpen,
    // Web
    web_search: Globe,
    search_web: Globe,
    fetch_page: Link,
    fetch_webpage: Link,
    analyze_data: BarChart3,
    // GAIA agent: files, memory, questions
    find_files: Search,
    edit_file: FileEdit,
    recall: Brain,
    remember: Brain,
    update_memory: Brain,
    forget: Brain,
    schedule_follow_up: Clock,
    list_follow_ups: Clock,
    cancel_follow_up: Clock,
    search_past_conversations: Brain,
    request_user_input: MessageCircleQuestionMark,
    // Skills
    load_skill: Sparkles,
    unload_skill: Sparkles,
    list_skills: Sparkles,
    search_skill_hub: Sparkles,
    install_skill: Sparkles,
    capture_skill: Sparkles,
    remove_skill: Sparkles,
    skill_status: Sparkles,
};

// A tool added here without its labels would show a raw key; say so at load instead.
for (const name of Object.keys(TOOL_ICON)) {
    if (!isMessageKey(`tool.${name}.done`) || !isMessageKey(`tool.${name}.active`)) {
        throw new Error(`AgentActivity: tool "${name}" has an icon but no tool.${name}.done/.active labels in src/i18n/locales/en.ts.`);
    }
}

/** Tools whose row marks the agent picking up a skill. */
const SKILL_USE_TOOLS = new Set(['load_skill']);

// Memory works in the background: its steps show only when they fail.
const BACKGROUND_TOOLS = new Set(['remember', 'recall', 'update_memory', 'forget']);

function isBackgroundStep(step: AgentStep): boolean {
    return step.type === 'tool' && step.success !== false && BACKGROUND_TOOLS.has(step.tool ?? '');
}

const DEFAULT_TOOL_META: ToolMeta = {
    label: ref('tool.default.done'), activeLabel: ref('tool.default.active'), icon: Wrench,
};

const named = (what: string, icon: LucideIcon): ToolMeta => ({
    label: ref('tool.named.done', { what }), activeLabel: ref('tool.named.active', { what }), icon,
});

const say = (t: Translate, m: MessageRef) => t(m.ref, m.params);

/** Which connector an MCP call went to, as the server names it. */
type McpIdentity = Pick<AgentStep, 'mcpServerLabel' | 'mcpTool'>;

function getToolMeta(toolName?: string, mcp?: McpIdentity): ToolMeta {
    if (!toolName) return DEFAULT_TOOL_META;
    if (toolName.startsWith('mcp_')) {
        const server = mcp?.mcpServerLabel;
        if (server) return named(mcp?.mcpTool ? `${server}: ${mcp.mcpTool}` : server, Globe);
        // Saved turns from before the server named the connector.
        return { label: ref('tool.mcp.done'), activeLabel: ref('tool.mcp.active'), icon: Globe };
    }
    if (Object.prototype.hasOwnProperty.call(TOOL_ICON, toolName)) {
        return {
            label: ref(`tool.${toolName}.done` as MessageKey),
            activeLabel: ref(`tool.${toolName}.active` as MessageKey),
            icon: TOOL_ICON[toolName],
        };
    }
    // A tool with no entry still says what it was, not a bare "Used tool".
    const words = toolName.replace(/[_-]+/g, ' ').trim();
    return words ? named(words, Wrench) : DEFAULT_TOOL_META;
}

/** The fallback line for a tool row that arrived without a narration (older saved turns). */
function toolFallbackLabel(step: AgentStep, t: Translate): string {
    const meta = getToolMeta(step.tool, step);
    // The done-label ("Wrote file") would claim a failed, denied or stopped call succeeded.
    const unfinished = step.success == null && !step.result;
    return step.active
        ? say(t, meta.activeLabel)
        : step.success === false ? t('tool.failed', { label: meta.activeLabel })
        : unfinished ? t('tool.unfinished', { label: meta.activeLabel }) : say(t, meta.label);
}

/** The one plain line a tool row shows by default, and the live line while it runs. */
export function toolRowText(step: AgentStep, t: Translate = tEn): string {
    // Narration can only humanize the prefixed tool id; the connector's own name says more.
    if (step.mcpServerLabel) return toolFallbackLabel(step, t);
    return step.narration ? compactNarration(step.narration) : toolFallbackLabel(step, t);
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
            result[result.length - 1] = { ...step, timestamp: prev.timestamp, detail: merged || prev.detail };
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
        if (step.type === 'status' && step.success !== false && prev && prev.type === 'thinking') {
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

/** One stretch of the turn: a note the agent wrote, or the steps between two notes. */
export type TimelineItem =
    | { kind: 'note'; row: ActivityRow }
    | { kind: 'steps'; id: number; rows: ActivityRow[] };

/**
 * Split display rows at the agent's notes, so the turn reads note, steps, note, steps.
 * A note the answer opens with is left out: text written beside a closing call leads the answer.
 */
export function toTimeline(rows: ActivityRow[], answer = ''): TimelineItem[] {
    const items: TimelineItem[] = [];
    for (const row of rows) {
        if (row.type === 'commentary') {
            const text = row.detail?.trim();
            if (text && !answer.trimStart().startsWith(text)) items.push({ kind: 'note', row });
            continue;
        }
        const last = items[items.length - 1];
        if (last?.kind === 'steps') last.rows.push(row);
        else items.push({ kind: 'steps', id: row.id, rows: [row] });
    }
    return items;
}

/** "Used meeting-notes skill" for a skill pickup whose narration names the skill. */
function skillUsed(row: ActivityRow): MessageRef | undefined {
    if (!SKILL_USE_TOOLS.has(row.tool ?? '')) return undefined;
    const name = /:\s*(.+)$/.exec(row.narration ?? '')?.[1]?.trim();
    return name ? ref('activity.usedSkill', { name }) : undefined;
}

/** Most kinds of step named in a folded run before "and N more". */
const SUMMARY_KINDS = 3;

/** The one line a folded run of steps shows: what it did, in order. */
export function stepsSummary(rows: ActivityRow[], t: Translate = tEn): string {
    const kinds: { label: MessageRef; id: string; count: number }[] = [];
    for (const row of rows) {
        let label: MessageRef;
        if (row.type === 'tool') label = skillUsed(row) ?? getToolMeta(row.tool, row).label;
        else if (row.type === 'policy_alert') label = ref('activity.blocked');
        else if (row.type === 'error') label = ref('activity.hitError');
        else if (row.type === 'plan') label = ref('activity.madePlan');
        else continue;
        const id = `${label.ref}:${JSON.stringify(label.params ?? {})}`;
        const seen = kinds.find((k) => k.id === id);
        if (seen) seen.count += row.count ?? 1;
        else kinds.push({ label, id, count: row.count ?? 1 });
    }
    // Written once per language, so a bracketed locale brackets the whole line, not each piece.
    return t.compose((sayIn) => {
        if (kinds.length === 0) return sayIn('activity.thoughtThrough');
        const parts = kinds.slice(0, SUMMARY_KINDS).map((k, i) => {
            const label = sayIn(k.label.ref, k.label.params);
            const text = i === 0 ? label : label.charAt(0).toLowerCase() + label.slice(1);
            return k.count > 1 ? `${text} ×${k.count}` : text;
        });
        const list = parts.join(sayIn('activity.summarySep'));
        const rest = kinds.length - parts.length;
        return rest > 0 ? sayIn('activity.summaryMore', { list, n: rest }) : list;
    });
}

/** A failed call the agent recovered from stays folded; a turn-level error never hides behind a click. */
function isTurnError(row: ActivityRow): boolean {
    return row.type === 'error' || row.type === 'policy_alert';
}

/** A warning the agent raised mid-turn ("Execution paused"); it carries no phase for the live line. */
function isWarning(row: ActivityRow): boolean {
    return row.type === 'status' && row.success === false;
}

/** Whether a row draws anything; the live line, not a row, says what the model is doing. */
function shownWhenDone(row: ActivityRow): boolean {
    return row.type !== 'status' && !(row.type === 'thinking' && !(row.detail || '').trim());
}

/**
 * How long each stretch of reasoning ran, keyed by every piece's id: from its first
 * piece until the next step began, or for the turn's last stretch, until the answer did.
 */
export function thoughtDurations(steps: AgentStep[]): Map<number, number> {
    const out = new Map<number, number>();
    let run: AgentStep[] = [];
    const close = (end?: number) => {
        const last = run[run.length - 1];
        const span = end != null ? end - run[0].timestamp : last?.latencyMs;
        if (span != null && span >= 0) for (const piece of run) out.set(piece.id, span);
        run = [];
    };
    for (const step of steps) {
        if (step.type === 'thinking' || (run.length > 0 && step.type === 'status')) {
            if (step.type === 'thinking') run.push(step);
            continue;
        }
        if (run.length > 0) close(step.timestamp);
    }
    if (run.length > 0) close();
    return out;
}

// ── Component ─────────────────────────────────────────────────────────────

interface AgentActivityProps {
    steps: AgentStep[];
    /** Whether a step is running right now. */
    isActive: boolean;
    /** Whether the turn is still in flight, between steps included. */
    running?: boolean;
    /** The turn is blocked on the user (an approval or a question), so nothing is running. */
    awaitingUser?: boolean;
    /** What the model is doing between steps; drawn at the end of the timeline while no row is live. */
    liveLine?: React.ReactNode;
    /** The turn's answer, so a note it repeats is not shown twice. */
    answer?: string;
}

/** The turn as a timeline: the agent's notes in full, the steps between them folded once done. */
export function AgentActivity({ steps, isActive, running = isActive, awaitingUser = false, liveLine, answer }: AgentActivityProps) {
    const t = useT();
    const [openGroups, setOpenGroups] = useState<Map<number, boolean>>(new Map());
    const [expandedTools, setExpandedTools] = useState<Set<number>>(new Set());
    const [toolNameFilter, setToolNameFilter] = useState('');
    const [statusFilter, setStatusFilter] = useState<'all' | 'success' | 'error'>('all');

    const displaySteps = useMemo(() => consolidateSteps(steps, running), [steps, running]);
    const timeline = useMemo(() => toTimeline(displaySteps, answer), [displaySteps, answer]);
    const durations = useMemo(() => thoughtDurations(steps), [steps]);
    const now = useNow(running);

    // While the turn runs, the latest plan stays in view as a live checklist.
    const livePlan = running ? [...displaySteps].reverse().find((s) => s.type === 'plan' && s.planSteps?.length) : undefined;
    const liveRow = running && displaySteps.some((s) => s.active && s.type !== 'status');
    const filtering = !!toolNameFilter || statusFilter !== 'all';

    const toggleTool = useCallback((id: number) => {
        setExpandedTools((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }, []);

    const keep = (row: ActivityRow): boolean => {
        if (row.type !== 'tool' || !filtering) return true;
        const needle = toolNameFilter.toLowerCase();
        if (needle && !row.tool?.toLowerCase().includes(needle)
            && !toolRowText(row, t).toLowerCase().includes(needle)) return false;
        if (statusFilter === 'success' && row.success !== true) return false;
        if (statusFilter === 'error' && row.success !== false) return false;
        return true;
    };

    const renderRow = (step: ActivityRow) => {
        if (step.type === 'thinking') {
            return <FlowThought key={step.id} step={step} live={running && !!step.active} durationMs={durations.get(step.id)} />;
        }
        if (step.type === 'tool') {
            return (
                <FlowToolCard
                    key={step.id}
                    step={step}
                    isExpanded={expandedTools.has(step.id)}
                    now={now}
                    awaitingUser={running && awaitingUser}
                    onToggle={() => toggleTool(step.id)}
                />
            );
        }
        if (step.type === 'plan') {
            // While the turn runs, the live checklist below stands in for it.
            return step === livePlan ? null : <PlanChecklist key={step.id} step={step} />;
        }
        if (step.type === 'status') return <FlowWarning key={step.id} step={step} />;
        if (step.type === 'error') return <FlowError key={step.id} step={step} />;
        if (step.type === 'policy_alert') return <FlowPolicyAlert key={step.id} step={step} />;
        return null;
    };

    if (displaySteps.length === 0) {
        return liveLine ? <div className="agent-activity active"><div className="flow-live-line">{liveLine}</div></div> : null;
    }

    const lastIndex = timeline.length - 1;
    const visibleRows = (rows: ActivityRow[], live: boolean) =>
        rows.filter((r) => (shownWhenDone(r) || (live && isWarning(r))) && !isPlanShownLive(r, livePlan));
    // Calls in the runs that are open; folded runs and lone rows have nothing to sift.
    const openCalls = timeline.reduce((n, item, i) => {
        if (item.kind !== 'steps') return n;
        const live = running && i === lastIndex;
        const visible = visibleRows(item.rows, live);
        if (visible.length < 2 && !live) return n;
        const open = openGroups.get(item.id) ?? (live || visible.some(isTurnError));
        return open ? n + visible.filter((r) => r.type === 'tool').length : n;
    }, 0);

    return (
        <div className={`agent-activity ${running ? 'active' : 'done'}`}>
            <div className="agent-timeline">
                {timeline.map((item, i) => {
                    if (item.kind === 'note') {
                        return <FlowNote key={item.row.id} text={item.row.detail ?? ''} />;
                    }
                    const live = running && i === lastIndex;
                    const visible = visibleRows(item.rows, live);
                    if (visible.length === 0) return null;
                    const failed = visible.some(isTurnError);
                    // A lone row is already one line; folding it would only add a click.
                    if (visible.length === 1 && !live) {
                        return keep(visible[0])
                            ? <div key={item.id} className="flow-steps single">{renderRow(visible[0])}</div>
                            : null;
                    }
                    const open = openGroups.get(item.id) ?? (live || failed);
                    const shown = visible.filter(keep);
                    return (
                        <div key={item.id} className={`flow-steps ${open ? 'open' : 'folded'} ${live ? 'live' : ''}`}>
                            {!live && (
                                <button
                                    className="flow-steps-summary"
                                    onClick={() => setOpenGroups((prev) => new Map(prev).set(item.id, !open))}
                                    aria-expanded={open}
                                >
                                    {failed
                                        ? <AlertCircle size={13} className="agent-icon-error" />
                                        : <CheckCircle2 size={13} className="flow-steps-done" />}
                                    <span className="flow-steps-text">{stepsSummary(visible, t)}</span>
                                    <ChevronRight size={12} className={`flow-tool-chevron ${open ? 'expanded' : ''}`} />
                                </button>
                            )}
                            {open && <div className="flow-steps-rows">{shown.map(renderRow)}</div>}
                        </div>
                    );
                })}
            </div>

            {livePlan && <PlanChecklist step={livePlan} />}
            {running && !liveRow && liveLine && <div className="flow-live-line">{liveLine}</div>}

            {/* Filter: after the steps, and only once there are enough open calls to sift */}
            {!running && openCalls >= FILTER_MIN_TOOL_ROWS && (
                <div className="activity-filter-bar">
                    <input
                        type="text"
                        className="activity-filter-input"
                        placeholder={t('activity.filterPlaceholder')}
                        aria-label={t('activity.filterByTool')}
                        value={toolNameFilter}
                        onChange={(e) => setToolNameFilter(e.target.value)}
                    />
                    <select
                        className="activity-filter-select"
                        aria-label={t('activity.filterByStatus')}
                        value={statusFilter}
                        onChange={(e) => setStatusFilter(e.target.value as 'all' | 'success' | 'error')}
                    >
                        <option value="all">{t('activity.filter.all')}</option>
                        <option value="success">{t('activity.filter.success')}</option>
                        <option value="error">{t('activity.filter.error')}</option>
                    </select>
                </div>
            )}
        </div>
    );
}

function isPlanShownLive(row: ActivityRow, livePlan?: ActivityRow): boolean {
    return row.type === 'plan' && row === livePlan;
}

// ── Flow: a warning raised while the turn runs ──────────────────────────────

function FlowWarning({ step }: { step: AgentStep }) {
    return (
        <div className="flow-warning" role="status">
            <AlertCircle size={13} />
            <span>{step.label}</span>
        </div>
    );
}

// ── Flow: a note the agent wrote between steps ────────────────────────────

function FlowNote({ text }: { text: string }) {
    return (
        <div className="flow-note md-content">
            <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                disallowedElements={[...SAFE_DISALLOWED_ELEMENTS]}
                urlTransform={safeUrlTransform}
            >
                {text}
            </ReactMarkdown>
        </div>
    );
}

// ── Flow: Reasoning (collapsed unless it is streaming right now) ──────────

/** "Thought for 8s" once reasoning is over and its length is known. */
export function thoughtLabel(live: boolean, durationMs?: number, t: Translate = tEn): string {
    if (live) return t('activity.thinking');
    if (durationMs == null || durationMs < 1000) return t('activity.thought');
    return t('activity.thoughtFor', { time: formatElapsed(durationMs / 1000) });
}

function FlowThought({ step, live, durationMs }: { step: AgentStep; live: boolean; durationMs?: number }) {
    const t = useT();
    const text = (step.detail || '').trim();
    const [open, setOpen] = useState(false);
    const containerRef = useRef<HTMLDivElement>(null);

    // Keep the newest reasoning in view as it streams.
    useEffect(() => {
        if (live && containerRef.current) {
            containerRef.current.scrollTop = containerRef.current.scrollHeight;
        }
    }, [text, live]);

    if (!text) return null;

    return (
        <div className={`flow-reasoning ${live ? 'live' : ''}`}>
            <button
                className="flow-reasoning-toggle"
                onClick={() => setOpen((o) => !o)}
                aria-expanded={open}
            >
                {live ? <Loader2 size={13} className="flow-tool-spinner" /> : <Brain size={13} className="flow-tool-icon" />}
                <span>{thoughtLabel(live, durationMs, t)}</span>
                <ChevronRight size={12} className={`flow-tool-chevron ${open ? 'expanded' : ''}`} />
            </button>
            {(open || live) && (
                <div ref={containerRef} className={`flow-thought-hacker ${open ? '' : 'peek'}`}>
                    <span>{text}</span>
                    {live && <span className="flow-thought-cursor" />}
                </div>
            )}
        </div>
    );
}

// ── Path Linkification (for tool results) ────────────────────────────────

/** Detect Windows absolute paths in text and make them clickable. */
export function linkifyPaths(text: string, t: Translate = tEn): React.ReactNode {
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
                title={t('activity.openPath', { path: filePath })}
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
    const t = useT();
    const [expanded, setExpanded] = useState(false);
    const PREVIEW_COUNT = 5;
    const displayFiles = expanded ? fileList.files : fileList.files.slice(0, PREVIEW_COUNT);
    const remaining = fileList.total - PREVIEW_COUNT;

    const getFileName = (f: Record<string, unknown>, i: number): string => {
        const name = f.name || f.filename || f.file_name;
        if (name) return String(name);
        if (f.file_path) return String(f.file_path).replace(/\\/g, '/').split('/').pop() || t('activity.files.file', { n: i + 1 });
        return t('activity.files.file', { n: i + 1 });
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
                    {t('activity.files.more', { n: remaining })}
                </button>
            )}
            {expanded && fileList.files.length < fileList.total && (
                <span className="file-list-truncated">{t('activity.files.showing', { shown: fileList.files.length, total: fileList.total })}</span>
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
    const t = useT();
    const Icon = getToolMeta(step.tool, step).icon;
    // A call held for the user's answer hasn't started, so it neither runs nor times.
    const waiting = !!step.active && awaitingUser;
    const line = waiting ? t('activity.waiting') : toolRowText(step, t);
    const count = step.count ?? 1;
    // A failed call's reason belongs on its row; a success's output stays one click deeper.
    const failure = step.success === false && step.result ? step.result : '';
    const timing = waiting
        ? ''
        : step.active
        ? formatElapsed((now - step.timestamp) / 1000)
        : step.latencyMs != null ? formatToolLatency(step.latencyMs) : '';

    return (
        <div className={`flow-tool ${step.active ? 'active' : ''} ${step.success === false ? 'error' : ''} ${SKILL_USE_TOOLS.has(step.tool ?? '') ? 'skill' : ''}`}>
            <button
                className="flow-tool-header"
                onClick={onToggle}
                aria-expanded={isExpanded}
                title={
                    waiting
                        ? toolRowText(step, t)
                        : step.narration && step.narration !== line ? step.narration : undefined
                }
            >
                <div className="flow-tool-left">
                    {/* What the step did, not whether it worked: only a running or failed step says otherwise. */}
                    {step.active && !waiting ? (
                        <Loader2 size={13} className="flow-tool-spinner" />
                    ) : step.success === false ? (
                        <AlertCircle size={13} style={{ color: 'var(--danger)' }} />
                    ) : (
                        <Icon size={13} className="flow-tool-icon" />
                    )}
                    <span className="flow-tool-label">{line}</span>
                    {count > 1 && (
                        <span className="flow-tool-count" aria-label={t('activity.identicalCalls', { n: count })}>{'×'}{count}</span>
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
                        <span className="detail-section-label">{t('activity.detail.tool')}</span>
                        <code className="flow-tool-badge">{step.tool || 'unknown'}</code>
                        {step.mcpServer && (
                            <span className="flow-tool-mcp-server">{t('activity.detail.via', { server: step.mcpServer })}</span>
                        )}
                    </div>
                    {/* Arguments (except for commands) */}
                    {step.detail && !step.commandOutput && (
                        <div className="step-detail-args">
                            <span className="detail-section-label">{t('activity.detail.arguments')}</span>
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
                            <span className="detail-section-label">{t('activity.detail.output')}</span>
                            <CodeBlock code={step.mcpOutput.text} previewLines={MCP_OUTPUT_PREVIEW_LINES} testId="mcp-output" />
                            {step.mcpOutput.truncated && (
                                <div className="cmd-truncated">{t('activity.truncated')}</div>
                            )}
                        </div>
                    )}

                    {/* Generic result */}
                    {step.result && !step.commandOutput && !step.mcpOutput && (
                        <div className={`step-detail-result ${step.success === false ? 'result-error' : 'result-success'}`}>
                            <span className="detail-section-label">
                                {step.success === false ? t('activity.detail.error') : t('activity.detail.result')}
                            </span>
                            <StepResult text={step.result} renderShort={(text) => linkifyPaths(text, t)} />
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

const PLAN_STATUS_LABEL: Record<PlanItemStatus, MessageKey> = {
    done: 'activity.planStatus.done',
    running: 'activity.planStatus.running',
    failed: 'activity.planStatus.failed',
    stopped: 'activity.planStatus.stopped',
    pending: 'activity.planStatus.pending',
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
    const t = useT();
    const items = step.planSteps ?? [];
    if (items.length === 0) return null;
    const statuses = step.planStatuses;
    const done = statuses?.filter((s) => s === 'done').length ?? 0;

    return (
        <div className="flow-plan">
            <div className="flow-plan-header">
                <ListChecks size={13} />
                <span>{t('activity.plan')}</span>
                {statuses && <span className="flow-plan-count">{t('activity.planDone', { done, total: items.length })}</span>}
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
                            aria-label={status ? t('activity.planItem', { status: ref(PLAN_STATUS_LABEL[status]), text }) : undefined}
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
    const t = useT();
    return (
        <div className="flow-error">
            <AlertCircle size={13} />
            <span>{step.detail || step.label || t('activity.errorOccurred')}</span>
        </div>
    );
}

function FlowPolicyAlert({ step }: { step: AgentStep }) {
    const t = useT();
    return (
        <div className="flow-policy-alert">
            <div className="flow-policy-alert-header">
                <ShieldAlert size={14} />
                <span className="flow-policy-alert-title">{t('activity.policy.title')}</span>
                <span className="flow-policy-alert-decision">{step.decision || 'BLOCK'}</span>
            </div>
            <div className="flow-policy-alert-body">
                <div className="flow-policy-alert-line">
                    <span>{t('activity.policy.tool')}</span>
                    <code>{step.tool || 'unknown tool'}</code>
                </div>
                <div className="flow-policy-alert-reason">{step.reason || step.detail}</div>
                {step.ruleIds && step.ruleIds.length > 0 && (
                    <div className="flow-policy-alert-line">
                        <span>{t('activity.policy.rules')}</span>
                        <code>{step.ruleIds.join(', ')}</code>
                    </div>
                )}
                {step.policyVersion && (
                    <div className="flow-policy-alert-line">
                        <span>{t('activity.policy.policy')}</span>
                        <code>{step.policyVersion}</code>
                    </div>
                )}
                <div className="flow-policy-alert-line">
                    <span>{t('activity.policy.receipt')}</span>
                    {step.receiptId ? <code>{step.receiptId}</code> : <em>{t('activity.policy.receiptUnavailable')}</em>}
                </div>
            </div>
        </div>
    );
}

// ── Retrieval Chunks View ──────────────────────────────────────────────

function ChunksView({ chunks }: { chunks: RetrievalChunk[] }) {
    const t = useT();
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
                {t('activity.chunks', { n: chunks.length })}
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
                                        <span className="chunk-page">{t('activity.chunkPage', { page: chunk.page })}</span>
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
    const t = useT();
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
                    <span className="cmd-header-title">{t('activity.terminal')}</span>
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
                            {t('activity.exit', { code: output.returnCode })}
                        </span>
                    )}
                    {hasOutput && (
                        <button
                            className={`cmd-copy ${copied ? 'copied' : ''}`}
                            onClick={handleCopy}
                            title={copied ? t('msg.copied') : t('activity.copyOutput')}
                            aria-label={copied ? t('msg.copiedAria') : t('activity.copyOutputAria')}
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
                <div className="cmd-truncated">{t('activity.truncated')}</div>
            )}
            {!hasOutput && <div className="cmd-empty">{t('activity.noOutput')}</div>}
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
