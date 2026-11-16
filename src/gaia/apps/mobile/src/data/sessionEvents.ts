// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { AppState } from 'react-native';

import type { GaiaClient } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { queryKeys } from './queries';

// The reconnect delay of webui's session-events stream in App.tsx.
export const EVENTS_BACKOFF_MIN_MS = 2_000;
export const EVENTS_BACKOFF_MAX_MS = 30_000;
// A connection that held this long before failing starts the delay over.
const HEALTHY_MS = 10_000;

export interface SessionEventDeps {
    client: GaiaClient;
    signal: AbortSignal;
    /** Called for each event; `sessionId` is set when another client asked this one to open that chat. */
    onEvent: (sessionId: string | null) => void;
    onError: (err: unknown) => void;
    sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
    now?: () => number;
}

function sleepUnlessAborted(ms: number, signal: AbortSignal): Promise<void> {
    return new Promise((resolve) => {
        const timer = setTimeout(resolve, ms);
        signal.addEventListener('abort', () => {
            clearTimeout(timer);
            resolve();
        });
    });
}

/** Follows the PC's chat-list events until `signal` aborts, reconnecting with a growing delay. */
export async function followSessionEvents(deps: SessionEventDeps): Promise<void> {
    const { client, signal, onEvent, onError, sleep = sleepUnlessAborted, now = Date.now } = deps;
    let delay = EVENTS_BACKOFF_MIN_MS;
    while (!signal.aborted) {
        const connectedAt = now();
        try {
            for await (const event of client.watchSessionEvents({ signal })) {
                const id = (event as { session_id?: unknown }).session_id;
                onEvent((event.type as string) === 'set_active_session' && typeof id === 'string' && id ? id : null);
            }
        } catch (err) {
            if (signal.aborted) return;
            onError(err);
        }
        if (signal.aborted) return;
        if (now() - connectedAt >= HEALTHY_MS) delay = EVENTS_BACKOFF_MIN_MS;
        await sleep(delay, signal);
        delay = Math.min(delay * 2, EVENTS_BACKOFF_MAX_MS);
    }
}

/**
 * While the app is in the foreground: refetches the chat list on every event and when the app
 * comes back, and passes `onOpenChat` the chat another client asked to show.
 */
export function useSessionSync(onOpenChat: (sessionId: string) => void): void {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    useEffect(() => {
        if (!client) return undefined;
        let stop: AbortController | null = null;
        const refresh = () => void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
        const start = () => {
            if (stop) return;
            const controller = new AbortController();
            stop = controller;
            void followSessionEvents({
                client,
                signal: controller.signal,
                onEvent: (id) => {
                    refresh();
                    if (id) onOpenChat(id);
                },
                onError: (err) => console.warn('GAIA chat-list events dropped; reconnecting', err),
            });
        };
        const end = () => {
            stop?.abort();
            stop = null;
        };
        if (AppState.currentState === 'active') start();
        const subscription = AppState.addEventListener('change', (state) => {
            if (state === 'active') {
                refresh();
                start();
            } else {
                end();
            }
        });
        return () => {
            subscription.remove();
            end();
        };
    }, [client, queryClient, onOpenChat]);
}
