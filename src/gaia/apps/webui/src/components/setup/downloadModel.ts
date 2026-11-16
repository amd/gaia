// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** What the download rows say, from a run's status: shared by the web UI and the phone app. */

import type { Translate } from '../../i18n/translator';
import type { SetupModelProgress, SetupModelState, SetupRunStatus } from '../../types';
import {
    STALL_NOTICE_S, STALL_STUCK_S, formatBytes, formatEta, formatSize, formatSpeed, ltrIn, type Estimate,
} from './format';

/** Cancelling past this much asks first. */
export const CONFIRM_CANCEL_BYTES = 1e9;

/** What is happening now on top, then what went wrong, what is done, and what waits. */
const STATE_ORDER: Record<SetupModelState, number> = {
    downloading: 0, verifying: 1, failed: 2, ready: 3, unchecked: 3, cancelled: 4, queued: 5, skipped: 6,
};

/** Within a state: embedder, then chat, then the rest. */
function roleOrder(m: SetupModelProgress): number {
    if (m.roles.includes('embedding')) return 0;
    if (m.roles.includes('chat')) return 1;
    return 2;
}

export function sortedModels(models: SetupModelProgress[]): SetupModelProgress[] {
    return [...models].filter((m) => m.state !== 'skipped')
        .sort((a, b) => STATE_ORDER[a.state] - STATE_ORDER[b.state] || roleOrder(a) - roleOrder(b));
}

/** A log line the row already says: which model, its progress bar, or that it finished. */
const REPEATS_ROW = /^\s*(Downloading:|Downloaded\b|\[[=\-\s>#]*\])/;

/** The log is worth opening only once it says something the row doesn't. */
export function logAddsDetail(lines: string[]): boolean {
    return lines.some((l) => l.trim() && !REPEATS_ROW.test(l));
}

export const STATE_KEY: Record<SetupModelState, Parameters<Translate>[0]> = {
    queued: 'setup.state.queued',
    downloading: 'setup.state.downloading',
    verifying: 'setup.state.verifying',
    ready: 'setup.state.ready',
    failed: 'setup.state.failed',
    skipped: 'setup.state.skipped',
    cancelled: 'setup.state.cancelled',
    unchecked: 'setup.state.unchecked',
};

/** Whether anything is still moving: a run, or a single download. */
export function downloadsActive(status: SetupRunStatus | null | undefined): boolean {
    if (!status) return false;
    if (status.state === 'running' || status.state === 'verifying') return true;
    // A row verifying outside a run is Retry testing it again.
    return !!status.models?.some((m) => m.state === 'downloading' || m.state === 'queued' || m.state === 'verifying');
}

export function failureText(m: SetupModelProgress, name: string, freeGb: number | null, t: Translate): string {
    switch (m.error_kind) {
        case 'network':
            return t('setup.fail.network', { name });
        case 'disk_full': {
            const left = Math.max(0, m.bytes_total - m.bytes_done) / 1e9;
            const need = freeGb != null ? Math.max(0.1, left - freeGb) : left;
            return t('setup.fail.diskFull', { name, done: formatBytes(m.bytes_done), need: formatSize(need) });
        }
        case 'verify':
            return t('setup.fail.verify', { name });
        case 'memory':
            return t('setup.fail.testMemory', { name });
        case 'stuck':
            return t('setup.fail.testStuck', { name });
        case 'answer':
            return t('setup.fail.testAnswer', { name });
        case 'window':
            return t('setup.fail.testWindow', { name });
        case 'path_too_long':
            return t('setup.fail.pathTooLong', { name });
        default:
            return t('setup.fail.lemonade', { name });
    }
}

/** Seconds left of a keep-trying wait, 0 when none runs. */
export function retryingSeconds(m: SetupModelProgress, nowMs: number): number {
    return m.retrying_until ? Math.max(0, Math.round(m.retrying_until - nowMs / 1000)) : 0;
}

/** Seconds a downloading row has had no data; 0 while it waits out a dropped connection. */
export function stalledSeconds(m: SetupModelProgress, estimate: Estimate | undefined, retryingFor: number): number {
    return m.state === 'downloading' && !retryingFor ? estimate?.stalledFor ?? 0 : 0;
}

/** The line under a row: sizes and speed, a wait, a stall, or what happens next. */
export function rowDetail(args: {
    model: SetupModelProgress;
    estimate: Estimate | undefined;
    retryingFor: number;
    stalled: number;
    checking: boolean;
    checkingFor: number;
    t: Translate;
}): string {
    const { model: m, estimate, retryingFor, stalled, checking, checkingFor, t } = args;
    if (m.state === 'downloading') {
        const sizes = ltrIn(t.locale, m.bytes_total > 0 ? `${formatBytes(m.bytes_done)} / ${formatBytes(m.bytes_total)}` : formatBytes(m.bytes_done));
        if (retryingFor > 0) return t('setup.progress.retrying', { n: String(retryingFor) });
        if (stalled >= STALL_STUCK_S) return t('setup.progress.stuck');
        if (stalled >= STALL_NOTICE_S) return t('setup.progress.stalled', { n: String(STALL_NOTICE_S) });
        if (estimate?.speed && estimate.eta != null) {
            const { speed, eta } = estimate;
            return t.compose((say) => `${sizes} · ${ltrIn(t.locale, formatSpeed(speed))} · ${formatEta(eta, say)}`);
        }
        return t.compose((say) => `${sizes} · ${say('setup.progress.estimating')}`);
    }
    if (m.state === 'verifying') {
        return !checking ? t('setup.progress.waitingCheck')
            : checkingFor >= 10 ? t('setup.progress.checkingFor', { n: String(checkingFor) })
                : t('setup.progress.checkingLoad');
    }
    if (m.state === 'ready' || m.state === 'queued') return m.bytes_total ? formatBytes(m.bytes_total) : '';
    if (m.state === 'unchecked') return t('setup.progress.unchecked');
    if (m.state === 'cancelled') return m.bytes_done > 0 ? t('setup.progress.keptPartial', { done: formatBytes(m.bytes_done) }) : '';
    return '';
}

/** Whether a run is going, or a single row is moving: the sidebar chip's "moving". */
export function isMoving(status: SetupRunStatus | null): boolean {
    if (!status) return false;
    if (status.state === 'running' || status.state === 'verifying') return true;
    // A row verifying outside a run is Retry testing it again.
    return !!status.models?.some((m) => m.state === 'downloading' || m.state === 'verifying');
}

/**
 * The rows a person outside setup cares about: a run still going shows all of
 * its models; otherwise only the ones this session started, or that need an
 * answer (failed, cancelled). Overall is re-added over those rows, as the
 * backend adds it.
 */
export function scopedStatus(status: SetupRunStatus | null, started: readonly string[]): SetupRunStatus | null {
    if (!status) return null;
    if (status.state === 'running' || status.state === 'verifying') return status;
    const models = (status.models ?? []).filter((m) =>
        started.includes(m.id) || m.state === 'downloading' || m.state === 'failed' || m.state === 'cancelled');
    const counted = models.filter((m) => m.state !== 'skipped');
    const done = counted.reduce((sum, m) => sum + Math.min(m.bytes_done, m.bytes_total), 0);
    const total = counted.reduce((sum, m) => sum + m.bytes_total, 0);
    const speed = counted.filter((m) => m.state === 'downloading').reduce((sum, m) => sum + m.speed_bps, 0);
    return {
        ...status,
        models,
        overall: {
            bytes_done: done,
            bytes_total: total,
            speed_bps: speed,
            eta_s: speed > 0 ? Math.round((total - done) / speed) : null,
            percent: total ? Math.floor((done * 100) / total) : null,
            failed: counted.filter((m) => m.state === 'failed').length,
        },
    };
}
