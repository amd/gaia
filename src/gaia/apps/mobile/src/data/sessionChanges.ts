// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { SessionChanges, SessionGit, UndoAllResult } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { requireClient } from './queries';
import { turnChangesKeys } from './turnChanges';

export const sessionChangesKeys = {
    total: (sessionId: string) => ['sessionChanges', sessionId, 'total'] as const,
    git: (sessionId: string) => ['sessionChanges', sessionId, 'git'] as const,
};

/** Re-reads `key` once a reply ends, since it may have changed files. */
function useRefreshWhenReplyEnds(sessionId: string, live: boolean, key: readonly unknown[]) {
    const queryClient = useQueryClient();
    const wasLive = useRef(live);
    useEffect(() => {
        if (wasLive.current && !live) {
            void queryClient.invalidateQueries({ queryKey: key });
            void queryClient.invalidateQueries({ queryKey: turnChangesKeys.chat(sessionId) });
        }
        wasLive.current = live;
    });
}

/** Everything the chat changed on disk, across its turns. */
export function useSessionChanges(sessionId: string, live: boolean) {
    const client = useGaiaClient();
    useRefreshWhenReplyEnds(sessionId, live, sessionChangesKeys.total(sessionId));
    return useQuery<SessionChanges>({
        queryKey: sessionChangesKeys.total(sessionId),
        enabled: client !== null && sessionId !== '',
        queryFn: ({ signal }) => requireClient(client).getSessionChanges(sessionId, { signal }),
        retry: false,
    });
}

/** The git repositories the chat's files are in. */
export function useSessionGit(sessionId: string, live: boolean) {
    const client = useGaiaClient();
    useRefreshWhenReplyEnds(sessionId, live, sessionChangesKeys.git(sessionId));
    return useQuery<SessionGit>({
        queryKey: sessionChangesKeys.git(sessionId),
        enabled: client !== null && sessionId !== '',
        queryFn: ({ signal }) => requireClient(client).getSessionGit(sessionId, { signal }),
        retry: false,
    });
}

/** Undo all, Commit and Create PR; each re-reads what the others show. */
export function useChangesActions(sessionId: string) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const refresh = () =>
        Promise.all([
            queryClient.invalidateQueries({ queryKey: sessionChangesKeys.total(sessionId) }),
            queryClient.invalidateQueries({ queryKey: sessionChangesKeys.git(sessionId) }),
            queryClient.invalidateQueries({ queryKey: turnChangesKeys.chat(sessionId) }),
        ]);
    const undoAll = useMutation<UndoAllResult, Error, boolean>({
        mutationFn: (force) => requireClient(client).undoAllChanges(sessionId, force),
        onSuccess: refresh,
    });
    const commit = useMutation<{ files: string[]; commit: string }, Error, { root: string; message: string }>({
        mutationFn: ({ root, message }) => requireClient(client).commitChanges(sessionId, root, message),
        onSuccess: refresh,
    });
    const pullRequest = useMutation<{ url: string }, Error, { root: string; title: string; body: string }>({
        mutationFn: ({ root, title, body }) => requireClient(client).createPullRequest(sessionId, root, title, body),
        onSuccess: refresh,
    });
    return { undoAll, commit, pullRequest };
}
