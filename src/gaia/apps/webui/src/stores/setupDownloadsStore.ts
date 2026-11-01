// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Model downloads outside first-run setup: the ones Settings starts, and any
 * run still going. One poll of /api/setup/status feeds Settings and the
 * sidebar chip, and the downloads Settings queues run one at a time, so they
 * keep going when Settings closes.
 */

import { create } from 'zustand';
import * as api from '../services/api';
import type { SetupRunStatus } from '../types';

const POLL_MS = 1000;

interface Queued {
    id: string;
    acknowledgeLowDisk: boolean;
    keepTrying: boolean;
}

interface SetupDownloadsState {
    status: SetupRunStatus | null;
    error: string | null;
    queue: Queued[];
    /** Models this session asked to download, so a finished run's rows don't count. */
    started: string[];
    /** Read the status now, and keep polling while something moves. */
    refresh: () => Promise<void>;
    /** Download these one after another; the user has already seen the totals. */
    enqueue: (ids: string[], options?: { acknowledgeLowDisk?: boolean; keepTrying?: boolean }) => Promise<void>;
    /** Stop one model's download, or all of them; queued ones are dropped. */
    cancel: (id?: string) => Promise<void>;
}

export function isMoving(status: SetupRunStatus | null): boolean {
    if (!status) return false;
    if (status.state === 'running' || status.state === 'verifying') return true;
    return !!status.models?.some((m) => m.state === 'downloading');
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

let timer: ReturnType<typeof setInterval> | null = null;

function errorText(err: unknown): string {
    return err instanceof Error ? err.message : String(err);
}

export const useSetupDownloads = create<SetupDownloadsState>((set, get) => {
    const schedule = () => {
        const busy = isMoving(get().status) || get().queue.length > 0;
        if (busy && !timer) timer = setInterval(() => { void get().refresh(); }, POLL_MS);
        if (!busy && timer) {
            clearInterval(timer);
            timer = null;
        }
    };

    /** Start the next queued download once nothing else is pulling. */
    const next = async () => {
        const { status, queue } = get();
        if (queue.length === 0 || isMoving(status)) return;
        const [head, ...rest] = queue;
        set({ queue: rest });
        try {
            set({ status: await api.downloadSetupModel(head.id, head), error: null });
        } catch (err) {
            set({ error: errorText(err) });
        }
    };

    return {
        status: null,
        error: null,
        queue: [],
        started: [],
        refresh: async () => {
            try {
                set({ status: await api.getSetupStatus(), error: null });
            } catch (err) {
                set({ error: `Lost contact with GAIA: ${errorText(err)}` });
            }
            await next();
            schedule();
        },
        enqueue: async (ids, options = {}) => {
            const add = ids.map((id) => ({ id, acknowledgeLowDisk: !!options.acknowledgeLowDisk, keepTrying: !!options.keepTrying }));
            set((s) => ({
                queue: [...s.queue.filter((q) => !ids.includes(q.id)), ...add],
                started: [...new Set([...s.started, ...ids])],
                error: null,
            }));
            await next();
            schedule();
        },
        cancel: async (id) => {
            set((s) => ({ queue: id ? s.queue.filter((q) => q.id !== id) : [] }));
            try {
                await api.cancelSetup(id);
            } catch (err) {
                set({ error: errorText(err) });
            }
            await get().refresh();
        },
    };
});

/** For tests: stop polling and forget everything. */
export function resetSetupDownloads(): void {
    if (timer) clearInterval(timer);
    timer = null;
    useSetupDownloads.setState({ status: null, error: null, queue: [], started: [] });
}
