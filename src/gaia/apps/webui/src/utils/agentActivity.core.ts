// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The agent activity timeline's pure logic, shared by the Agent UI and the phone app. */

import { isMessageKey, ref, type MessageKey, type MessageRef } from '../i18n/registry';
import { tEn, type Translate } from '../i18n/translator';
import type { AgentStep } from '../types';
import { markStrayDirection, visibleName } from './calendarApproval.core';

export const SKILL_USE_TOOLS = new Set(['load_skill', 'use_skill']);

// Memory works in the background: its steps show only when they fail.
const BACKGROUND_TOOLS = new Set(['remember', 'recall', 'update_memory', 'forget']);

function isBackgroundStep(step: AgentStep): boolean {
    return step.type === 'tool' && step.success !== false && BACKGROUND_TOOLS.has(step.tool ?? '');
}

export const say = (t: Translate, m: MessageRef) => t(m.ref, m.params);

/** Which connector an MCP call went to, as the server names it. */
type McpIdentity = Pick<AgentStep, 'mcpServerLabel' | 'mcpTool'>;

export interface ToolLabels {
    /** "Read file"; a message, so each language says it its own way. */
    label: MessageRef;
    /** "Reading file", also the stem of "Reading file failed". */
    activeLabel: MessageRef;
}

const DEFAULT_TOOL_LABELS: ToolLabels = { label: ref('tool.default.done'), activeLabel: ref('tool.default.active') };

const named = (what: string): ToolLabels => ({
    label: ref('tool.named.done', { what }), activeLabel: ref('tool.named.active', { what }),
});

export function getToolLabels(toolName?: string, mcp?: McpIdentity): ToolLabels {
    if (!toolName) return DEFAULT_TOOL_LABELS;
    if (toolName.startsWith('mcp_')) {
        const server = mcp?.mcpServerLabel;
        if (server) return named(mcp?.mcpTool ? `${server}: ${mcp.mcpTool}` : server);
        // Saved turns from before the server named the connector.
        return { label: ref('tool.mcp.done'), activeLabel: ref('tool.mcp.active') };
    }
    if (isMessageKey(`tool.${toolName}.done`) && isMessageKey(`tool.${toolName}.active`)) {
        return {
            label: ref(`tool.${toolName}.done` as MessageKey),
            activeLabel: ref(`tool.${toolName}.active` as MessageKey),
        };
    }
    // A tool with no entry still says what it was, not a bare "Used tool".
    const words = toolName.replace(/[_-]+/g, ' ').trim();
    return words ? named(words) : DEFAULT_TOOL_LABELS;
}

/** The fallback line for a tool row that arrived without a narration (older saved turns). */
function toolFallbackLabel(step: AgentStep, t: Translate): string {
    const meta = getToolLabels(step.tool, step);
    // The done-label ("Wrote file") would claim a failed, denied or stopped call succeeded.
    const unfinished = step.success == null && !step.result;
    return step.active
        ? say(t, meta.activeLabel)
        : step.timedOut ? t('tool.timedOut', { label: meta.activeLabel })
        : step.autoDeclined ? t('tool.autoDeclined', { label: meta.activeLabel })
        : step.declined ? t('tool.declined', { label: meta.activeLabel })
        : step.refused ? t('tool.refused', { label: meta.activeLabel })
        : step.success === false ? t('tool.failed', { label: meta.activeLabel })
        : unfinished ? t('tool.unfinished', { label: meta.activeLabel }) : say(t, meta.label);
}

/** The one plain line a tool row shows by default, and the live line while it runs. */
export function toolRowText(step: AgentStep, t: Translate = tEn): string {
    // Narration can only humanize the prefixed tool id; the connector's own name says more.
    if (step.mcpServerLabel) return toolFallbackLabel(step, t);
    // GAIA writes this row itself, so it can say it in the UI's language.
    if (step.tool === 'use_skill' && step.detail) return t('activity.usingSkill', { name: visibleName(step.detail) });
    // A present-tense narration would read as if a call that never ran had run.
    if ((step.declined || step.timedOut || step.refused || step.autoDeclined) && !step.active) return toolFallbackLabel(step, t);
    return step.narration ? compactNarration(markStrayDirection(step.narration)) : toolFallbackLabel(step, t);
}

const COMPACT_DETAIL_CHARS = 48;

/** Lines of an MCP tool's output shown before "Show all". */
export const MCP_OUTPUT_PREVIEW_LINES = 5;

/** Below this many call rows the list is short enough to scan without a filter. */
export const FILTER_MIN_TOOL_ROWS = 6;

export type StepStatusFilter = 'all' | 'success' | 'error';

/** Whether a row survives the steps filter: only tool rows are sifted, by name (or row text) and by outcome. */
export function matchesStepFilter(row: AgentStep, nameFilter: string, statusFilter: StepStatusFilter, t: Translate = tEn): boolean {
    if (row.type !== 'tool' || (!nameFilter && statusFilter === 'all')) return true;
    const needle = nameFilter.toLowerCase();
    if (needle && !row.tool?.toLowerCase().includes(needle) && !toolRowText(row, t).toLowerCase().includes(needle)) return false;
    if (statusFilter === 'success' && row.success !== true) return false;
    if (statusFilter === 'error' && row.success !== false) return false;
    return true;
}

const FETCH_ERROR = /^Error fetching page:\s*(\d{3})?/;

/** What a failed page fetch shows on its row; the library's own error stays in the opened step. */
export function failureText(step: AgentStep, t: Translate): string {
    const result = step.result ?? '';
    const match = step.tool === 'fetch_page' ? FETCH_ERROR.exec(result) : null;
    if (!match) return result;
    const code = match[1];
    if (code && ['401', '403', '429'].includes(code)) return t('activity.fetchBlocked', { code });
    return t('activity.fetchFailed');
}

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
    /** The finished reasoning that led to this call, opened from the call's row. */
    thought?: ActivityRow;
}

/**
 * Fold each finished thought into the call it led to, so the list reads as steps
 * instead of "Thought" between every one. A thought still streaming, or one that
 * led to a note, a plan or the answer, keeps its own row.
 */
export function foldThoughts(rows: ActivityRow[]): ActivityRow[] {
    const out: ActivityRow[] = [];
    let pending: ActivityRow | undefined;
    for (const row of rows) {
        if (row.type === 'thinking' && !row.active && (row.detail || '').trim()) {
            if (pending) out.push(pending);
            pending = row;
            continue;
        }
        if (pending && row.type === 'tool') {
            out.push({ ...row, thought: pending });
            pending = undefined;
            continue;
        }
        if (pending) out.push(pending);
        pending = undefined;
        out.push(row);
    }
    if (pending) out.push(pending);
    return out;
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

        // Merge consecutive thinking steps; concatenate so accumulated text survives. A step
        // that has a duration ends a thought; saved token pieces have none until their last.
        if (step.type === 'thinking' && prev && prev.type === 'thinking') {
            const gap = prev.latencyMs != null && /\S$/.test(prev.detail || '') && /^\S/.test(step.detail || '') ? '\n\n' : '';
            const merged = (prev.detail || '') + gap + (step.detail || '');
            result[result.length - 1] = { ...step, timestamp: prev.timestamp, detail: merged || prev.detail };
            return;
        }
        // Merge consecutive status steps, never over a warning
        if (step.type === 'status' && prev && prev.type === 'status' && prev.success !== false && step.active !== false && !prev.kept && !step.kept) {
            result[result.length - 1] = { ...step, label: step.label || prev.label };
            return;
        }
        // Absorb thinking into adjacent status — keep thinking detail
        if (step.type === 'thinking' && prev && prev.type === 'status' && prev.active !== false) {
            result[result.length - 1] = { ...prev, type: 'thinking', detail: step.detail || prev.detail, active: step.active };
            return;
        }
        // Absorb status into adjacent thinking — preserve thinking detail
        if (step.type === 'status' && step.success !== false && !step.kept && prev && prev.type === 'thinking') {
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

/** The skill a skill pickup's narration names. */
function skillName(row: ActivityRow): string | undefined {
    if (!SKILL_USE_TOOLS.has(row.tool ?? '')) return undefined;
    return /:\s*(.+)$/.exec(row.narration ?? '')?.[1]?.trim() || undefined;
}

/** Most kinds of step named in a folded run before "and N more". */
const SUMMARY_KINDS = 3;

/** The one line a folded run of steps shows: what it did, in order. */
export function stepsSummary(rows: ActivityRow[], t: Translate = tEn): string {
    const kinds: { label: MessageRef; failedLabel?: MessageRef; allFailed: boolean; failures: number; id: string; count: number }[] = [];
    for (const row of rows) {
        let label: MessageRef;
        let failedLabel: MessageRef | undefined;
        if (row.type === 'tool' && row.timedOut) label = ref('activity.timedOut');
        else if (row.type === 'tool' && row.autoDeclined) label = ref('activity.autoDeclined');
        else if (row.type === 'tool' && row.declined) label = ref('activity.youDeclined');
        else if (row.type === 'tool' && row.refused) label = ref('activity.gaiaRefused');
        else if (row.type === 'tool') {
            const meta = getToolLabels(row.tool, row);
            const skill = skillName(row);
            label = skill ? ref('activity.usedSkill', { name: skill }) : meta.label;
            failedLabel = skill ? ref('activity.skillFailed', { name: skill }) : ref('tool.failed', { label: meta.activeLabel });
        }
        else if (row.type === 'policy_alert') label = ref('activity.blocked');
        else if (row.type === 'error') label = ref('activity.hitError');
        else if (row.type === 'plan') label = ref('activity.madePlan');
        else continue;
        const id = `${label.ref}:${JSON.stringify(label.params ?? {})}`;
        const failed = row.type === 'tool' && row.success === false;
        const seen = kinds.find((k) => k.id === id);
        const n = row.count ?? 1;
        if (seen) {
            seen.count += n;
            seen.allFailed &&= failed;
            seen.failures += failed ? n : 0;
        }
        else kinds.push({ label, failedLabel, allFailed: failed, failures: failed ? n : 0, id, count: n });
    }
    // Written once per language, so a bracketed locale brackets the whole line, not each piece.
    return t.compose((sayIn) => {
        if (kinds.length === 0) return sayIn('activity.thoughtThrough');
        const parts = kinds.slice(0, SUMMARY_KINDS).map((k, i) => {
            // "Saved to memory" over calls that all failed would claim they worked.
            const shown = k.allFailed && k.failedLabel ? k.failedLabel : k.label;
            const label = sayIn(shown.ref, shown.params);
            const text = i === 0 ? label : label.charAt(0).toLowerCase() + label.slice(1);
            const counted = k.count > 1 ? `${text} ×${k.count}` : text;
            // "Ran command ×3" over one that failed would hide the failure.
            const some = k.failedLabel && !k.allFailed && k.failures > 0;
            return some ? sayIn('activity.someFailed', { text: counted, n: k.failures }) : counted;
        });
        const list = parts.join(sayIn('activity.summarySep'));
        const rest = kinds.length - parts.length;
        return rest > 0 ? sayIn('activity.summaryMore', { list, n: rest }) : list;
    });
}

/** A failed call the agent recovered from stays folded; a turn-level error never hides behind a click. */
export function isTurnError(row: ActivityRow): boolean {
    return row.type === 'error' || row.type === 'policy_alert';
}

/** A warning the agent raised mid-turn ("Execution paused"); it carries no phase for the live line, and stays with the turn. */
export function isWarning(row: ActivityRow): boolean {
    return row.type === 'status' && row.success === false;
}

/** A finished step the user waited on (a download); it stays with the turn, unlike a passing status. */
export function isKept(row: ActivityRow): boolean {
    return row.type === 'status' && row.kept === true;
}

/** Whether a row draws anything; the live line, not a row, says what the model is doing. */
export function shownWhenDone(row: ActivityRow): boolean {
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

/** Live reasoning shorter than this needs no clock. */
const THINKING_ELAPSED_MIN_MS = 3000;

/** "Thinking · 4s" while it runs, "Thought for 8s" once it is over and its length is known. */
export function thoughtLabel(live: boolean, durationMs?: number, t: Translate = tEn): string {
    if (live) {
        return durationMs != null && durationMs >= THINKING_ELAPSED_MIN_MS
            ? t('activity.thinkingFor', { time: formatElapsed(durationMs / 1000) })
            : t('activity.thinking');
    }
    if (durationMs == null || durationMs < 1000) return t('activity.thought');
    return t('activity.thoughtFor', { time: formatElapsed(durationMs / 1000) });
}

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

/** One line of a call's arguments: a `key: value` pair, or a bare value. */
export interface ArgRow {
    key?: string;
    value: string;
}

/** `key: value` where the colon is followed by a space, so `C:\x` is never read as key `C`. */
const ARG_ROW_RE = /^([A-Za-z_][\w.-]{0,29}):(?:\s+([\s\S]*))?$/;

/** A call's arguments as rows; a single row means the text isn't a list of pairs. */
export function argRows(detail: string): ArgRow[] {
    // Split on ", " only where a new `key: ` starts, so a list value stays on one row.
    const parts = detail.includes('\n')
        ? detail.split('\n').filter(Boolean)
        : detail.split(/, (?=[A-Za-z_][\w.-]{0,29}:(?:\s|$))/).filter(Boolean);
    return parts.map((part) => {
        const row = ARG_ROW_RE.exec(part.trim());
        return row ? { key: row[1], value: readableListValue((row[2] ?? '').trim()) } : { value: part };
    });
}
