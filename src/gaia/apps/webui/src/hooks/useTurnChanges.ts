// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import * as api from '../services/api';
import type { TurnChanges } from '../types/changes';

/**
 * One listing per chat, shared by every message in it. A message the cached
 * listing has not seen yet (a turn that just finished) triggers one refetch;
 * concurrent callers share the request in flight.
 */
interface SessionEntry {
    byMessage: Map<number, TurnChanges>;
    checked: Set<number>;
    inFlight: Promise<void> | null;
    listeners: Set<() => void>;
}

const cache = new Map<string, SessionEntry>();

function entryFor(sessionId: string): SessionEntry {
    let entry = cache.get(sessionId);
    if (!entry) {
        entry = { byMessage: new Map(), checked: new Set(), inFlight: null, listeners: new Set() };
        cache.set(sessionId, entry);
    }
    return entry;
}

function load(sessionId: string): Promise<void> {
    const entry = entryFor(sessionId);
    if (entry.inFlight) return entry.inFlight;
    entry.inFlight = Promise.resolve()
        .then(() => api.listTurnChanges(sessionId))
        .then((res) => {
            if (!res || !Array.isArray(res.turns)) {
                throw new Error('the server sent no list of turns');
            }
            const { turns } = res;
            entry.byMessage = new Map(
                turns.filter((t) => t.message_id != null).map((t) => [t.message_id as number, t]),
            );
            entry.listeners.forEach((fn) => fn());
        })
        .finally(() => { entry.inFlight = null; });
    return entry.inFlight;
}

/** Re-read the chat's listing, e.g. after Undo all, so every answer's row updates. */
export function refreshTurnChanges(sessionId: string): Promise<void> {
    return load(sessionId);
}

/** Test hook: forget every cached listing. */
export function resetTurnChangesCache(): void {
    cache.clear();
}

/** The file changes the turn answered by *messageId* made, or null if none. */
export function useTurnChanges(sessionId: string, messageId: number) {
    const [turn, setTurn] = useState<TurnChanges | null>(
        () => cache.get(sessionId)?.byMessage.get(messageId) ?? null,
    );
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (messageId <= 0) return;
        const entry = entryFor(sessionId);
        const sync = () => setTurn(entry.byMessage.get(messageId) ?? null);
        entry.listeners.add(sync);
        if (!entry.byMessage.has(messageId) && !entry.checked.has(messageId)) {
            entry.checked.add(messageId);
            load(sessionId).then(
                () => setError(null),
                (err: unknown) => {
                    // Let a later mount of this message try again.
                    entry.checked.delete(messageId);
                    setError(err instanceof Error ? err.message : String(err));
                },
            );
        } else {
            sync();
        }
        return () => { entry.listeners.delete(sync); };
    }, [sessionId, messageId]);

    const refresh = useCallback(() => load(sessionId), [sessionId]);
    return { turn, error, refresh };
}
