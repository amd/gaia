// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The app's poll of GET /api/system/status. One request is in flight at a
 * time, so a slow backend never collects a pile of probes, and the poll backs
 * off while the backend or the model server is slow to answer, so it adds no
 * load to a busy machine.
 */

import * as api from './api';
import { log } from '../utils/logger';
import type { SystemStatus } from '../types';

export const STATUS_POLL_MS = 5_000;
/** While a model downloads, its progress bar wants a faster refresh. */
export const STATUS_POLL_DOWNLOAD_MS = 2_000;
export const STATUS_POLL_MAX_MS = 30_000;
/** Above the status handler's own sequential probes (health, models, catalog, stats, devices: 31 s+). */
export const STATUS_REQUEST_TIMEOUT_MS = 60_000;

export type StatusOutcome =
    | { kind: 'ok'; status: SystemStatus }
    /** No answer within STATUS_REQUEST_TIMEOUT_MS: the backend is busy or stuck. */
    | { kind: 'slow' }
    /** The backend answered with an HTTP error (a 5xx under load, a broken handler). */
    | { kind: 'error'; httpStatus: number; message: string }
    /** The request never reached a backend: nothing is listening. */
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
    return outcome.kind === 'slow' || outcome.kind === 'error'
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
    let current: { controller: AbortController; cancelled: boolean } | null = null;

    /** The outcome, or null when stop() cancelled the request. */
    const probe = async (): Promise<StatusOutcome | null> => {
        const request = { controller: new AbortController(), cancelled: false };
        current = request;
        const { controller } = request;
        const limit = setTimeout(() => controller.abort(), STATUS_REQUEST_TIMEOUT_MS);
        try {
            return { kind: 'ok', status: await api.getSystemStatus(controller.signal) };
        } catch (error) {
            if (request.cancelled) return null;
            if (controller.signal.aborted) return { kind: 'slow' };
            const httpStatus = (error as { httpStatus?: unknown })?.httpStatus;
            // The status handler never sends 502/504; a proxy or tunnel does when nothing answers behind it.
            if ((error as Error)?.name === 'ApiError' && typeof httpStatus === 'number'
                && httpStatus !== 502 && httpStatus !== 504) {
                return { kind: 'error', httpStatus, message: (error as Error).message };
            }
            return { kind: 'unreachable', error };
        } finally {
            clearTimeout(limit);
            if (current === request) current = null;
        }
    };

    const check = (): Promise<void> => {
        if (inFlight) return inFlight;
        const pending: Promise<void> = probe()
            .then((outcome) => {
                if (outcome === null) return;
                slowStreak = isSlow(outcome) ? slowStreak + 1 : 0;
                downloading = isDownloading(outcome);
                try {
                    onOutcome(outcome);
                } catch (err) {
                    log.system.error('Could not apply the system status; the next poll will try again', err);
                }
            })
            .finally(() => { if (inFlight === pending) inFlight = null; });
        inFlight = pending;
        return pending;
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
                if (gen !== generation) return;
                generation++;
                if (timer) clearTimeout(timer);
                timer = null;
                if (current) {
                    // Detached, so a restart (React StrictMode) sends a fresh request at once.
                    current.cancelled = true;
                    current.controller.abort();
                    current = null;
                    inFlight = null;
                }
            };
        },
    };
}
