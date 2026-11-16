// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { SessionEnvironment } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { requireClient } from './queries';

export const environmentKeys = {
    session: (sessionId: string) => ['environment', sessionId] as const,
};

/**
 * The folders the chat may touch and whether its web tools are on. Reads only while `enabled`
 * (the section is open, or the offline notice is mounted), and again once a reply ends.
 */
export function useSessionEnvironment(sessionId: string, { enabled, live = false }: { enabled: boolean; live?: boolean }) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const wasLive = useRef(live);
    useEffect(() => {
        if (wasLive.current && !live && enabled) void queryClient.invalidateQueries({ queryKey: environmentKeys.session(sessionId) });
        wasLive.current = live;
    }, [live, enabled, queryClient, sessionId]);
    return useQuery<SessionEnvironment>({
        queryKey: environmentKeys.session(sessionId),
        enabled: enabled && client !== null,
        queryFn: ({ signal }) => requireClient(client).getEnvironment(sessionId, { signal }),
        retry: false,
    });
}

/** The environment changes the panel and the offline notice make; each shares the PC's answer with every reader. */
export function useEnvironmentActions(sessionId: string) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const key = environmentKeys.session(sessionId);
    const share = (env: SessionEnvironment) => queryClient.setQueryData(key, env);
    const addFolder = useMutation<SessionEnvironment, Error, string>({
        mutationFn: (path) => requireClient(client).addEnvironmentFolder(sessionId, path),
        onSuccess: share,
    });
    const removeFolder = useMutation<SessionEnvironment, Error, string>({
        mutationFn: (path) => requireClient(client).removeEnvironmentFolder(sessionId, path),
        onSuccess: share,
    });
    const setInternet = useMutation<SessionEnvironment, Error, boolean>({
        mutationFn: (on) => requireClient(client).setInternet(sessionId, on),
        onSuccess: share,
        // The PC saves the switch even when it can't tell the running reply, so show what was saved.
        onError: () => queryClient.invalidateQueries({ queryKey: key }),
    });
    return { addFolder, removeFolder, setInternet };
}
