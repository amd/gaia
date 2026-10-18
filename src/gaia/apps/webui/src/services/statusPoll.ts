// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The app's poll of GET /api/system/status. One request is in flight at a
 * time, so a slow backend never collects a pile of probes, and the poll backs
 * off while the backend or the model server is slow to answer, so it adds no
 * load to a busy machine.
 */

import * as api from './api';
import type { SystemStatus } from '../types';

export const STATUS_POLL_MS = 5_000;
/** While a model downloads, its progress bar wants a faster refresh. */
export const STATUS_POLL_DOWNLOAD_MS = 2_000;
export const STATUS_POLL_MAX_MS = 30_000;
/** The backend's own model-server probe waits 10 s, and its device probes a few more. */
export const STATUS_REQUEST_TIMEOUT_MS = 30_000;

export type StatusOutcome =
    | { kind: 'ok'; status: SystemStatus }
    /** No answer within STATUS_REQUEST_TIMEOUT_MS: the backend is busy or stuck. */
    | { kind: 'slow' }
    /** The request failed outright: nothing is listening, or the backend errored. */
    | { kind: 'unreachable'; error: unknown };

/**
 * `up`; `unresponsive` when the model server is listening but its health check
 * failed or timed out (often just busy); `down` when nothing answers its port.
 */
export type ModelServerHealth = 'up' | 'unresponsive' | 'down';

export function modelServerHealth(status: SystemStatus): ModelServerHealth {
    if (status.lemonade_running) return 'up';
    return status.lemonade_error ? 'unresponsive' : 'down';
}

function isSlow(outcome: StatusOutcome): boolean {
    return outcome.kind === 'slow'
        || (outcome.kind === 'ok' && modelServerHealth(outcome.status) === 'unresponsive');
}

function isDownloading(outcome: StatusOutcome): boolean {
    const state = outcome.kind === 'ok' ? outcome.status.download_progress?.state : undefined;
    return state === 'downloading' || state === 'starting';
}

export interface StatusPoller {
    /** Probe now; a call while a probe is in flight shares it. */
    check: () => Promise<void>;
    /** Start polling; returns its stop. */
    start: () => () => void;
}

export function createStatusPoller(onOutcome: (outcome: StatusOutcome) => void): StatusPoller {
    let inFlight: Promise<void> | null = null;
    let slowStreak = 0;
    let downloading = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    // A stopped poll's probe still in flight must not reschedule.
    let generation = 0;

    const probe = async (): Promise<StatusOutcome> => {
        const controller = new AbortController();
        const limit = setTimeout(() => controller.abort(), STATUS_REQUEST_TIMEOUT_MS);
        try {
            return { kind: 'ok', status: await api.getSystemStatus(controller.signal) };
        } catch (error) {
            return controller.signal.aborted ? { kind: 'slow' } : { kind: 'unreachable', error };
        } finally {
            clearTimeout(limit);
        }
    };

    const check = (): Promise<void> => {
        if (inFlight) return inFlight;
        inFlight = probe()
            .then((outcome) => {
                slowStreak = isSlow(outcome) ? slowStreak + 1 : 0;
                downloading = isDownloading(outcome);
                onOutcome(outcome);
            })
            .finally(() => { inFlight = null; });
        return inFlight;
    };

    const nextDelay = () => {
        const base = downloading ? STATUS_POLL_DOWNLOAD_MS : STATUS_POLL_MS;
        return Math.min(base * 2 ** slowStreak, STATUS_POLL_MAX_MS);
    };

    const tick = (gen: number) => {
        void check().finally(() => {
            if (gen === generation) timer = setTimeout(() => tick(gen), nextDelay());
        });
    };

    return {
        check,
        start: () => {
            const gen = ++generation;
            tick(gen);
            return () => {
                if (gen === generation) generation++;
                if (timer) clearTimeout(timer);
                timer = null;
            };
        },
    };
}
