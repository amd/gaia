// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';

import type { FileDiff, TurnChanges, UndoResult } from '@/api/client.types';

import { useGaiaClient } from './GaiaClientProvider';
import { requireClient } from './queries';

export const turnChangesKeys = {
    chat: (sessionId: string) => ['turnChanges', sessionId] as const,
    diff: (sessionId: string, turnId: string) => ['turnChanges', sessionId, turnId, 'diff'] as const,
};

/** The file changes every turn of the chat made, one listing shared by all its answers. */
export function useTurnChanges(sessionId: string) {
    const client = useGaiaClient();
    return useQuery<TurnChanges[]>({
        queryKey: turnChangesKeys.chat(sessionId),
        enabled: client !== null && sessionId !== '',
        queryFn: async ({ signal }) => {
            const res = await requireClient(client).listTurnChanges(sessionId, { signal });
            return res.turns;
        },
    });
}

export function useTurnDiff(sessionId: string, turnId: string) {
    const client = useGaiaClient();
    return useQuery<FileDiff[]>({
        queryKey: turnChangesKeys.diff(sessionId, turnId),
        enabled: client !== null,
        queryFn: async ({ signal }) => (await requireClient(client).getTurnDiff(sessionId, turnId, { signal })).files,
    });
}

/** Undoes one turn; re-reads the chat's listing so every answer's row updates. */
export function useUndoTurn(sessionId: string) {
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    return useCallback(
        async (turnId: string, force: boolean): Promise<UndoResult> => {
            const result = await requireClient(client).undoTurn(sessionId, turnId, force);
            await queryClient.invalidateQueries({ queryKey: turnChangesKeys.chat(sessionId) });
            return result;
        },
        [client, queryClient, sessionId],
    );
}

/** Re-reads the listing, for when a turn has just ended and may have changed files. */
export function useRefreshTurnChanges(sessionId: string) {
    const queryClient = useQueryClient();
    return useCallback(
        () => queryClient.invalidateQueries({ queryKey: turnChangesKeys.chat(sessionId) }),
        [queryClient, sessionId],
    );
}
