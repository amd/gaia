// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import type { ChatPerformance, ContextUsage, StreamEvent } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { requireClient } from './queries';

export const contextKeys = {
    usage: (sessionId: string) => ['contextUsage', sessionId] as const,
    performance: (sessionId: string) => ['chatPerformance', sessionId] as const,
};

export interface ContextReading {
    usage: ContextUsage | null;
    /** Why there is no reading yet, from the PC. */
    unavailable: string | null;
}

/**
 * How full the chat's context window is: the PC's reading between turns, and the numbers a running
 * turn streams. It is measured again each time a turn ends (`running` goes false).
 */
export function useContextUsage(sessionId: string, events: readonly StreamEvent[], running: boolean): ContextReading {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const key = contextKeys.usage(sessionId);
    const { data } = useQuery({
        queryKey: key,
        enabled: client !== null,
        queryFn: ({ signal }) => requireClient(client).getContextUsage(sessionId, { signal }),
    });
    useEffect(() => {
        if (!running) void queryClient.invalidateQueries({ queryKey: key });
    }, [running, queryClient, key]);

    let streamed: StreamEvent | undefined;
    if (running) for (let i = events.length - 1; i >= 0 && !streamed; i--) if (events[i]!.type === 'context_usage') streamed = events[i];
    if (streamed) return { usage: streamed as unknown as ContextUsage, unavailable: null };
    if (!data) return { usage: null, unavailable: null };
    return data.available ? { usage: data, unavailable: null } : { usage: null, unavailable: data.reason };
}

/** The context reading for the session panel: read only while `enabled`, and again once a turn ends. */
export function useContextSection(sessionId: string, { enabled, live }: { enabled: boolean; live: boolean }): ContextReading & { error: unknown } {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const key = contextKeys.usage(sessionId);
    const wasLive = useRef(live);
    useEffect(() => {
        if (wasLive.current && !live && enabled) void queryClient.invalidateQueries({ queryKey: key });
        wasLive.current = live;
    }, [live, enabled, queryClient, key]);
    const { data, error } = useQuery({
        queryKey: key,
        enabled: enabled && client !== null,
        queryFn: ({ signal }) => requireClient(client).getContextUsage(sessionId, { signal }),
        retry: false,
    });
    if (!data) return { usage: null, unavailable: null, error };
    return data.available ? { usage: data, unavailable: null, error } : { usage: null, unavailable: data.reason, error };
}

/** Where each answer's time and tokens went; read only while `enabled`, and again once a turn ends. */
export function useChatPerformance(sessionId: string, { enabled, live }: { enabled: boolean; live: boolean }) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const key = contextKeys.performance(sessionId);
    const wasLive = useRef(live);
    useEffect(() => {
        if (wasLive.current && !live && enabled) void queryClient.invalidateQueries({ queryKey: key });
        wasLive.current = live;
    }, [live, enabled, queryClient, key]);
    return useQuery<ChatPerformance>({
        queryKey: key,
        enabled: enabled && client !== null,
        queryFn: ({ signal }) => requireClient(client).getChatPerformance(sessionId, { signal }),
        retry: false,
    });
}
