// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useState } from 'react';
import * as api from '../services/api';
import { expectShape } from '../utils/responseShape';
import type { SessionEnvironment } from '../types/sessionPanel';

/**
 * One environment per chat, shared by the session panel and the composer's
 * internet-off notice. Read when a chat opens and after each change; never on
 * a timer.
 */
interface Entry {
    env: SessionEnvironment | null;
    error: string | null;
    inFlight: Promise<void> | null;
    /** Bumped by every change, so a read started before it can't overwrite it. */
    version: number;
    listeners: Set<() => void>;
}

const cache = new Map<string, Entry>();

function entryFor(sessionId: string): Entry {
    let entry = cache.get(sessionId);
    if (!entry) {
        entry = { env: null, error: null, inFlight: null, version: 0, listeners: new Set() };
        cache.set(sessionId, entry);
    }
    return entry;
}

function publish(sessionId: string, env: SessionEnvironment | null, error: string | null): void {
    const entry = entryFor(sessionId);
    entry.env = env ?? entry.env;
    entry.error = error;
    entry.listeners.forEach((fn) => fn());
}

function load(sessionId: string): Promise<void> {
    const entry = entryFor(sessionId);
    if (entry.inFlight) return entry.inFlight;
    const started = entry.version;
    entry.inFlight = Promise.resolve().then(() => api.getEnvironment(sessionId))
        .then((env) => {
            const checked = expectShape(env, (r) => Array.isArray(r.folders), "chat's environment");
            if (entry.version === started) publish(sessionId, checked, null);
        })
        .catch((err: unknown) => publish(sessionId, null, err instanceof Error ? err.message : String(err)))
        .finally(() => { entry.inFlight = null; });
    return entry.inFlight;
}

/** Test hook: forget every cached environment. */
export function resetSessionEnvironmentCache(): void {
    cache.clear();
}

export function useSessionEnvironment(sessionId: string) {
    const [, setTick] = useState(0);
    useEffect(() => {
        const entry = entryFor(sessionId);
        const sync = () => setTick((n) => n + 1);
        entry.listeners.add(sync);
        if (!entry.env && !entry.inFlight) void load(sessionId);
        return () => { entry.listeners.delete(sync); };
    }, [sessionId]);

    const refresh = useCallback(() => load(sessionId), [sessionId]);
    /** Apply a change, then share the server's answer with every reader. */
    const change = useCallback(async (op: () => Promise<SessionEnvironment>) => {
        const env = await op();
        entryFor(sessionId).version += 1;
        publish(sessionId, env, null);
        return env;
    }, [sessionId]);

    const entry = entryFor(sessionId);
    return { env: entry.env, error: entry.error, refresh, change };
}
