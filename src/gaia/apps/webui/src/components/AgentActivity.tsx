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
    Folder,
    BarChart3,
    Globe,
    FileEdit,
    Copy,
    Check,
    File,
    ShieldAlert,
    Brain,
    MessageCircleQuestionMark,
    Circle,
    Sparkles,
    Link,
    AudioLines,
    Captions,
    Image as ImageIcon,
    Ban,
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
import { isMessageKey, ref, type MessageKey } from '../i18n/registry';
import {
    FILTER_MIN_TOOL_ROWS, matchesStepFilter, MCP_OUTPUT_PREVIEW_LINES, SKILL_USE_TOOLS, consolidateSteps, failureText, foldThoughts, thoughtLabel,
    formatElapsed, formatToolLatency, compactNarration, argRows, getToolLabels, isKept, isTurnError, isWarning, say, shownWhenDone, stepsSummary, thoughtDurations,
    toTimeline, toolRowText,
    type ActivityRow, type PlanItemStatus, type TimelineItem, type ToolLabels,
} from '../utils/agentActivity.core';
import { tEn, useT, type Translate } from '../i18n/useT';
import { localizeLiveStatus } from '../utils/liveStatus';
import { ensureModel } from '../stores/modelNeededStore';
import './AgentActivity.css';

// ── Tool metadata: friendly names, icons, colors ──────────────────────────

interface ToolMeta extends ToolLabels {
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
    run_python: Terminal,
    execute_python_file: Terminal,
    // Images
    analyze_image: ImageIcon,
    answer_question_about_image: ImageIcon,
    generate_image: ImageIcon,
    take_screenshot: ImageIcon,
    // RAG & documents
    query_documents: BookOpen,
    query_specific_file: BookOpen,
    search_indexed_chunks: BookOpen,
    evaluate_retrieval: BookOpen,
    index_document: Database,
    index_documents: Database,
    index_directory: Database,
    list_indexed_documents: Database,
    summarize_document: FileText,
    dump_document: FileText,
    rag_status: Database,
    add_watch_directory: FolderOpen,
    // Recordings
    transcribe_media: AudioLines,
    refine_transcript: FileText,
    analyze_transcript: ListChecks,
    export_transcript: Captions,
    // Web
    web_search: Globe,
    search_web: Globe,
    fetch_page: Link,
    fetch_webpage: Link,
    // GAIA agent: files, memory, questions
    find_files: Search,
    edit_file: FileEdit,
    recall: Brain,
    remember: Brain,
    update_memory: Brain,
    forget: Brain,
    forget_everything: Brain,
    search_past_conversations: Brain,
    request_user_input: MessageCircleQuestionMark,
    // Skills
    load_skill: Sparkles,
    use_skill: Sparkles,
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

function getToolMeta(toolName?: string, mcp?: Pick<AgentStep, 'mcpServerLabel' | 'mcpTool'>): ToolMeta {
    const icon = toolName?.startsWith('mcp_') ? Globe
        : toolName && Object.prototype.hasOwnProperty.call(TOOL_ICON, toolName) ? TOOL_ICON[toolName] : Wrench;
    return { ...getToolLabels(toolName, mcp), icon };
}

export {
    argRows, compactNarration, formatToolLatency, readableListValue, thoughtLabel, consolidateSteps, failureText, foldThoughts, formatElapsed, planStatuses, stepsSummary,
    thoughtDurations, toTimeline, toolRowText,
} from '../utils/agentActivity.core';
export type { ActivityRow, PlanItemStatus, TimelineItem } from '../utils/agentActivity.core';

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
    /** Whether a step is running right now. */
    isActive: boolean;
    /** Whether the turn is still in flight, between steps included. */
    running?: boolean;
    /** The turn is blocked on the user (an approval or a question), so nothing is running. */
    awaitingUser?: boolean;
    /** What the model is doing between steps; drawn at the end of the timeline while no row is live. */
    liveLine?: React.ReactNode;
    /** What the running call's own model calls are doing (page progress, a server restart); shown on its row. */
    toolStatus?: string | null;
    /** The turn's answer, so a note it repeats is not shown twice. */
    answer?: string;
}

const needsVision = (step: AgentStep) => step.type === 'tool' && step.needsModel === 'vision' && !step.active;

/** The download an image step needs, offered where the user is looking. */
function VisionOffer() {
    const t = useT();
    return (
        <div className="flow-tool-offer">
            <span>{t('activity.visionNeeded')}</span>
            <button type="button" className="btn-secondary" onClick={() => { void ensureModel('vision'); }}>
                {t('activity.getVisionModel')}
            </button>
        </div>
    );
}

/** The turn as a timeline: the agent's notes in full, the steps between them folded once done. */
export function AgentActivity({ steps, isActive, running = isActive, awaitingUser = false, liveLine, toolStatus = null, answer }: AgentActivityProps) {
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
    const statusRowId = running && toolStatus
        ? [...displaySteps].reverse().find((s) => s.type === 'tool' && s.active)?.id
        : undefined;

    const toggleTool = useCallback((id: number) => {
        setExpandedTools((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }, []);

    const keep = (row: ActivityRow): boolean => matchesStepFilter(row, toolNameFilter, statusFilter, t);

    const renderRow = (step: ActivityRow) => {
        if (step.type === 'thinking') {
            return <FlowThought key={step.id} step={step} live={running && !!step.active} durationMs={durations.get(step.id)} now={now} />;
        }
        if (step.type === 'tool') {
            return (
                <FlowToolCard
                    key={step.id}
                    step={step}
                    isExpanded={expandedTools.has(step.id)}
                    thoughtMs={step.thought ? durations.get(step.thought.id) : undefined}
                    now={now}
                    awaitingUser={running && awaitingUser}
                    status={step.id === statusRowId ? toolStatus : null}
                    onToggle={() => toggleTool(step.id)}
                />
            );
        }
        if (step.type === 'plan') {
            // While the turn runs, the live checklist below stands in for it.
            return step === livePlan ? null : <PlanChecklist key={step.id} step={step} />;
        }
        if (step.type === 'status') return step.kept ? <FlowDone key={step.id} step={step} /> : <FlowWarning key={step.id} step={step} />;
        if (step.type === 'error') return <FlowError key={step.id} step={step} />;
        if (step.type === 'policy_alert') return <FlowPolicyAlert key={step.id} step={step} />;
        return null;
    };

    if (displaySteps.length === 0) {
        return liveLine ? <div className="agent-activity active"><div className="flow-live-line">{liveLine}</div></div> : null;
    }

    const lastIndex = timeline.length - 1;
    const visibleRows = (rows: ActivityRow[]) =>
        foldThoughts(rows.filter((r) => (shownWhenDone(r) || isWarning(r) || isKept(r)) && !isPlanShownLive(r, livePlan)));
    // Calls in the runs that are open; folded runs and lone rows have nothing to sift.
    const openCalls = timeline.reduce((n, item, i) => {
        if (item.kind !== 'steps') return n;
        const live = running && i === lastIndex;
        const visible = visibleRows(item.rows);
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
                    const visible = visibleRows(item.rows);
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
                                        : visible.some((r) => r.type === 'tool' && (r.declined || r.autoDeclined))
                                            ? <Ban size={13} className="flow-steps-done" />
                                            : <CheckCircle2 size={13} className="flow-steps-done" />}
                                    <span className="flow-steps-text">{stepsSummary(visible, t)}</span>
                                    <ChevronRight size={12} className={`flow-tool-chevron ${open ? 'expanded' : ''}`} />
                                </button>
                            )}
                            {open && <div className="flow-steps-rows">{shown.map(renderRow)}</div>}
                            {/* A warning never hides behind the fold. */}
                            {!open && visible.filter(isWarning).map(renderRow)}
                            {/* Nor does the download an image step asked for. */}
                            {!open && !live && visible.some(needsVision) && <VisionOffer />}
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
                        <option value="all">{t.bare('activity.filter.all')}</option>
                        <option value="success">{t.bare('activity.filter.success')}</option>
                        <option value="error">{t.bare('activity.filter.error')}</option>
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

function FlowDone({ step }: { step: AgentStep }) {
    const t = useT();
    return (
        <div className="flow-done" role="status">
            <Check size={13} aria-hidden="true" />
            {/* Saved in English; translated when drawn so a reopened chat follows the language. */}
            <span>{localizeLiveStatus(t, step.label)}</span>
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

/** Reasoning stays one line, live or done, until the user opens it. */
function FlowThought({ step, live, durationMs, now }: { step: AgentStep; live: boolean; durationMs?: number; now: number }) {
    const t = useT();
    const text = (step.detail || '').trim();
    const [open, setOpen] = useState(false);

    if (!text) return null;

    return (
        <div className={`flow-reasoning ${live ? 'live' : ''}`}>
            <button
                className="flow-reasoning-toggle"
                onClick={() => setOpen((o) => !o)}
                aria-expanded={open}
            >
                {live ? <Loader2 size={13} className="flow-tool-spinner" /> : <Brain size={13} className="flow-tool-icon" />}
                <span>{thoughtLabel(live, live ? now - step.timestamp : durationMs, t)}</span>
                <ChevronRight size={12} className={`flow-tool-chevron ${open ? 'expanded' : ''}`} />
            </button>
            {open && <div className="flow-thought-hacker">{text}</div>}
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

// ── Flow: File list from file search and folder listing tools ────────────

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
                    {f.kind === 'folder'
                        ? <Folder size={11} className="file-list-icon" data-kind="folder" />
                        : <File size={11} className="file-list-icon" />}
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
    /** Live line from this running call's own model calls; replaces its label. */
    status?: string | null;
    /** How long the reasoning folded into this call ran. */
    thoughtMs?: number;
}

function FlowToolCard({ step, isExpanded, onToggle, now, awaitingUser = false, status = null, thoughtMs }: FlowToolCardProps) {
    const t = useT();
    const Icon = getToolMeta(step.tool, step).icon;
    // A call held for the user's answer hasn't started, so it neither runs nor times.
    const waiting = !!step.active && awaitingUser;
    const live = !waiting && !!step.active && status ? localizeLiveStatus(t, status) : '';
    const line = waiting ? t('activity.waiting') : live || toolRowText(step, t);
    const count = step.count ?? 1;
    // A failed call's reason belongs on its row; a success's output stays one click deeper.
    const notRun = (!!step.declined || !!step.timedOut || !!step.autoDeclined) && !step.active;
    // A missing vision model is said by the offer under the row, not by the tool's raw error.
    const failure = step.success === false && step.result && !notRun && !step.needsModel ? failureText(step, t) : '';
    const timing = waiting
        ? ''
        : step.active
        ? formatElapsed((now - step.timestamp) / 1000)
        : step.latencyMs != null ? formatToolLatency(step.latencyMs) : '';

    return (
        <div className={`flow-tool ${step.active ? 'active' : ''} ${step.success === false && !notRun ? 'error' : ''} ${notRun ? 'declined' : ''} ${SKILL_USE_TOOLS.has(step.tool ?? '') ? 'skill' : ''}`}>
            <button
                className="flow-tool-header"
                onClick={onToggle}
                aria-expanded={isExpanded}
                title={
                    waiting || live
                        ? toolRowText(step, t)
                        : step.narration && step.narration !== line ? step.narration : undefined
                }
            >
                <div className="flow-tool-left">
                    {/* What the step did, not whether it worked: only a running or failed step says otherwise. */}
                    {step.active && !waiting ? (
                        <Loader2 size={13} className="flow-tool-spinner" />
                    ) : notRun ? (
                        <Ban size={13} className="flow-tool-icon" />
                    ) : step.success === false ? (
                        <AlertCircle size={13} style={{ color: 'var(--danger)' }} />
                    ) : (
                        <Icon size={13} className="flow-tool-icon" />
                    )}
                    {/* The model's narration is English in every UI language; it truncates at its own end. */}
                    <span className="flow-tool-label" dir="auto">{line}</span>
                    {count > 1 && (
                        <span className="flow-tool-count" aria-label={t('activity.identicalCalls', { n: count })}>{'×'}{count}</span>
                    )}
                    {failure && <span className="flow-tool-failure" dir="auto">{failure}</span>}
                </div>
                <div className="flow-tool-right">
                    {step.thought && (
                        <span className="flow-tool-thought-mark" title={t('activity.thoughtFirst')} aria-hidden="true">
                            <Brain size={11} />
                        </span>
                    )}
                    {timing && <span className="flow-tool-latency">{timing}</span>}
                    <span className={`flow-tool-chevron ${isExpanded ? 'expanded' : ''}`}>
                        <ChevronRight size={12} />
                    </span>
                </div>
            </button>

            {step.needsModel === 'vision' && !step.active && <VisionOffer />}

            {isExpanded && (
                <div className="flow-tool-detail">
                    {step.thought?.detail && (
                        <div className="flow-tool-thought">
                            <span className="detail-section-label">{thoughtLabel(false, thoughtMs, t)}</span>
                            <div className="flow-thought-hacker">{step.thought.detail.trim()}</div>
                        </div>
                    )}
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
export function formatArgsDisplay(detail: string): React.ReactNode {
    const rows = argRows(detail);
    if (rows.length <= 1) {
        return <span className="arg-value">{detail}</span>;
    }

    return (
        <div className="args-grid">
            {rows.map((row, i) => (
                <div key={i} className="arg-row">
                    {row.key !== undefined && <span className="arg-key">{row.key}</span>}
                    <span className="arg-value">{row.value}</span>
                </div>
            ))}
        </div>
    );
}
