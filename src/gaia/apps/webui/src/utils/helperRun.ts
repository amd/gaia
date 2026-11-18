// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** A `delegate_task` result as the activity log shows it (`gaia/ui/helper_results.py`). */

import { pluralKey } from '../i18n/registry';
import type { MessageKey } from '../i18n/registry';
import type { Translate } from '../i18n/useT';

export const DELEGATE_TOOL = 'delegate_task';
/** `result_data.type` of an in-process delegate_task result. */
export const HELPERS_RESULT_TYPE = 'helpers';
/** Most helpers one call runs; the list shows no more than this. */
export const MAX_SHOWN_HELPERS = 16;

export type HelperState = 'ok' | 'partial' | 'failed';
export type HelperRunStatus = 'success' | 'partial' | 'error';

export interface HelperItem {
    id: number;
    task: string;
    status: HelperState;
    reason?: string;
    conclusion?: string;
    steps?: number;
    wallS?: number;
}

export interface HelperRun {
    status: HelperRunStatus;
    /** The backend's English line, for when the items can't be counted. */
    summary: string;
    helpers: HelperItem[];
    /** Helpers the list leaves out. */
    hidden: number;
    /** Every helper the result named is in `helpers`, so they can be counted. */
    complete: boolean;
    /** The model served helpers one at a time. */
    oneAtATime: boolean;
}

const RUN_STATUSES = new Set<string>(['success', 'partial', 'error']);
const HELPER_STATES = new Set<string>(['ok', 'partial', 'failed']);

const isCount = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && v >= 0;

function helperItem(raw: unknown): HelperItem | null {
    if (!raw || typeof raw !== 'object') return null;
    const r = raw as Record<string, unknown>;
    if (typeof r.status !== 'string' || !HELPER_STATES.has(r.status)) return null;
    if (typeof r.id !== 'number' || !Number.isFinite(r.id)) return null;
    return {
        id: r.id,
        task: typeof r.task === 'string' ? r.task : '',
        status: r.status as HelperState,
        reason: typeof r.reason === 'string' && r.reason ? r.reason : undefined,
        conclusion: typeof r.conclusion === 'string' && r.conclusion.trim() ? r.conclusion : undefined,
        steps: isCount(r.steps) ? r.steps : undefined,
        wallS: isCount(r.wall_s) ? r.wall_s : undefined,
    };
}

/** The helper run in a step's `helpers`, or null when it isn't one. */
export function parseHelperRun(raw: unknown): HelperRun | null {
    if (!raw || typeof raw !== 'object') return null;
    const r = raw as Record<string, unknown>;
    if (typeof r.status !== 'string' || !RUN_STATUSES.has(r.status)) return null;
    if (typeof r.summary !== 'string' || !Array.isArray(r.helpers)) return null;
    const items = r.helpers.map(helperItem).filter((h): h is HelperItem => h !== null).sort((a, b) => a.id - b.id);
    const sentHidden = isCount(r.hidden) ? Math.floor(r.hidden) : 0;
    const over = Math.max(0, items.length - MAX_SHOWN_HELPERS);
    return {
        status: r.status as HelperRunStatus,
        summary: r.summary,
        helpers: items.slice(0, MAX_SHOWN_HELPERS),
        hidden: sentHidden + over,
        complete: items.length === r.helpers.length && items.length > 0 && sentHidden + over === 0,
        oneAtATime: typeof r.width_note === 'string' && r.width_note.trim() !== '',
    };
}

/** "3 of 4 helpers finished, 1 failed." in the UI's language, or the backend's line when the list is partial. */
export function helperRunSummary(run: HelperRun, t: Translate): string {
    if (!run.complete) return run.summary;
    const total = run.helpers.length;
    const count = (s: HelperState) => run.helpers.filter((h) => h.status === s).length;
    const ok = count('ok');
    const partial = count('partial');
    const failed = count('failed');
    return t.compose((say) => {
        let line = say(pluralKey('activity.helpers.finished', total), { ok: String(ok), total: String(total) });
        if (partial) line += say('activity.helpers.stoppedCount', { n: String(partial) });
        if (failed) line += say('activity.helpers.failedCount', { n: String(failed) });
        return line + say('activity.helpers.end');
    });
}

const REASONS: Record<string, MessageKey> = {
    step_limit: 'activity.helpers.reason.step_limit',
    loop: 'activity.helpers.reason.loop',
    empty: 'activity.helpers.reason.empty',
    error: 'activity.helpers.reason.error',
    timeout: 'activity.helpers.reason.timeout',
    cancelled: 'activity.helpers.reason.cancelled',
};

/** Why a helper stopped, in plain words; a reason GAIA doesn't know is shown as sent. */
export function helperReason(reason: string, t: Translate): string {
    return Object.prototype.hasOwnProperty.call(REASONS, reason) ? t.bare(REASONS[reason]) : reason;
}

/** "Stopped short (out of steps)" or "Failed: no answer"; empty for a helper that finished. */
export function helperOutcome(item: HelperItem, t: Translate): string {
    if (item.status === 'ok') return '';
    const reason = item.reason ? helperReason(item.reason, t) : '';
    if (item.status === 'partial') {
        return reason ? t.bare('activity.helpers.stoppedShort', { reason }) : t.bare('activity.helpers.stoppedShortPlain');
    }
    return reason ? t.bare('activity.helpers.failedBecause', { reason }) : t.bare('activity.helpers.failedPlain');
}
