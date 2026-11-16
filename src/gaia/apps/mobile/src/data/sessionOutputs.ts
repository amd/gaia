// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import type { SessionOutputs } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { requireClient } from './queries';

export const sessionOutputsKeys = {
    chat: (sessionId: string) => ['sessionOutputs', sessionId] as const,
};

/** What the chat produced, newest first. Reads only while `enabled` (the section is open) and again when a reply ends. */
export function useSessionOutputs(sessionId: string, { enabled, live }: { enabled: boolean; live: boolean }) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const wasLive = useRef(live);
    useEffect(() => {
        if (wasLive.current && !live && enabled) void queryClient.invalidateQueries({ queryKey: sessionOutputsKeys.chat(sessionId) });
        wasLive.current = live;
    }, [live, enabled, queryClient, sessionId]);
    return useQuery<SessionOutputs>({
        queryKey: sessionOutputsKeys.chat(sessionId),
        enabled: enabled && client !== null,
        queryFn: ({ signal }) => requireClient(client).getOutputs(sessionId, { signal }),
        retry: false,
    });
}
