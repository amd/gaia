// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { downloadsActive, isMoving, scopedStatus, sortedModels, STATE_KEY } from '@webui/components/setup/downloadModel';
import { overallFromPlan } from '@webui/components/setup/selection';
import type { SetupModelProgress, SetupModelState, SetupPlan, SetupRunStatus } from '@webui/types';

import type { Translate } from '@/i18n/useT';

import { fullRun } from '../flow';

export interface DownloadView {
    run: SetupRunStatus;
    rows: SetupModelProgress[];
    percent: number;
    bytesDone: number;
    failedCount: number;
    active: boolean;
    runFailed: boolean;
    /** Every row finished or kept, but the run stopped before its load check: Finish setup runs it. */
    downloadDone: boolean;
    /** A piece besides a model failed; its row in the contents says why and offers Retry. */
    componentFailed: boolean;
    cancelled: SetupModelProgress[];
    /** The rest of the run is still pulling models, so a failed row waits to be retried. */
    retryLater: boolean;
}

/** What the Download step shows for a run; null while the full run has not reported. */
export function downloadView(status: SetupRunStatus | null, plan: SetupPlan | undefined): DownloadView | null {
    const run = fullRun(status);
    if (!run) return null;
    const overall = overallFromPlan(run, plan ?? null);
    const rows = sortedModels(run.models ?? []);
    const runFailed = run.state === 'failed' || run.state === 'cancelled';
    const models = run.models ?? [];
    const active = downloadsActive(run);
    const downloadDone =
        runFailed &&
        models.length > 0 &&
        models.every((m) => m.state === 'ready' || m.state === 'skipped' || m.state === 'verifying' || m.state === 'unchecked') &&
        !isMoving(run);
    return {
        run,
        rows,
        percent: overall?.percent ?? 0,
        bytesDone: overall?.bytes_done ?? 0,
        failedCount: overall?.failed ?? rows.filter((m) => m.state === 'failed').length,
        active,
        runFailed,
        downloadDone,
        componentFailed: runFailed && !run.problem && Object.values(run.components ?? {}).includes('failed'),
        cancelled: rows.filter((m) => m.state === 'cancelled'),
        retryLater: run.state === 'running' || run.state === 'verifying',
    };
}

/** What the progress chip shows: "Downloading {done} of {count} · {pct}%", a failure, or nothing. */
export type ChipView = { kind: 'moving'; done: number; count: number; percent: number } | { kind: 'failed' } | null;

export function chipView(raw: SetupRunStatus | null | undefined): ChipView {
    const status = scopedStatus(raw ?? null, []);
    if (!status) return null;
    const models = (status.models ?? []).filter((m) => m.state !== 'skipped');
    const moving = isMoving(status);
    const failed = models.some((m) => m.state === 'failed') && status.state !== 'running';
    if (!moving && !failed) return null;
    if (failed && !moving) return { kind: 'failed' };
    // A failed or cancelled row isn't downloading, so it isn't one of the "N of M".
    const counted = models.filter((m) => m.state !== 'failed' && m.state !== 'cancelled');
    const finished = counted.filter((m) => m.state === 'ready' || m.state === 'verifying' || m.state === 'unchecked').length;
    return {
        kind: 'moving',
        done: Math.min(counted.length, finished + 1),
        count: counted.length,
        percent: status.overall?.percent ?? 0,
    };
}

/** Spoken milestones between two reads: a row changing state, a failure, each quarter overall. */
export function milestones(
    before: { states: ReadonlyMap<string, SetupModelState>; quarter: number },
    rows: SetupModelProgress[],
    percent: number,
    names: Record<string, string>,
    t: Translate,
): { said: string[]; failed: string[] } {
    const said: string[] = [];
    const failed: string[] = [];
    for (const m of rows) {
        if (before.states.get(m.id) === m.state) continue;
        const name = names[m.id] ?? m.id;
        if (m.state === 'failed') failed.push(t('setup.progress.announceFailed', { name }));
        else said.push(t('setup.progress.announceState', { name, state: t(STATE_KEY[m.state]) }));
    }
    const quarter = Math.floor(percent / 25);
    if (quarter > before.quarter && quarter > 0 && quarter < 4) said.push(t('setup.progress.announceOverall', { n: String(quarter * 25) }));
    return { said, failed };
}
