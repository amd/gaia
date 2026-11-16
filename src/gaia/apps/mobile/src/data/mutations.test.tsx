// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';

import type { Session } from '@/api/client.types';
import { createFakeClient } from '@/api/fake';

import { GaiaClientProvider } from './GaiaClientProvider';
import { useDeleteSession, useRenameSession, useSetPinned } from './mutations';
import { queryKeys, useSessions } from './queries';

const mockReplace = jest.fn();

function session(id: string, over: Partial<Session> = {}): Session {
    return {
        id, title: `Chat ${id}`, created_at: '2026-11-14T00:00:00Z', updated_at: '2026-11-14T00:00:00Z',
        model: '', system_prompt: null, message_count: 1, document_ids: [], ...over,
    };
}

function setup(client = createFakeClient({ sessions: [session('a'), session('b')] })) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
            <GaiaClientProvider client={client}>{children}</GaiaClientProvider>
        </QueryClientProvider>
    );
    return { client, queryClient, wrapper };
}

describe('session mutations', () => {
    beforeEach(() => mockReplace.mockClear());

    it('rename and pin show at once, refetch the list and the chat, and a failure puts the row back', async () => {
        const { client, queryClient, wrapper } = setup();
        const list = await renderHook(() => useSessions(), { wrapper });
        await waitFor(() => expect(list.result.current.data?.sessions).toHaveLength(2));
        const invalidated = jest.spyOn(queryClient, 'invalidateQueries');
        const rename = await renderHook(() => useRenameSession(), { wrapper });
        const pin = await renderHook(() => useSetPinned(), { wrapper });

        await act(async () => rename.result.current.mutate({ sessionId: 'a', title: 'Budget' }));
        await waitFor(async () => expect((await client.getSession('a')).title).toBe('Budget'));
        await waitFor(() => expect(list.result.current.data?.sessions.find((s) => s.id === 'a')).toMatchObject({ title: 'Budget' }));
        expect(invalidated).toHaveBeenCalledWith({ queryKey: queryKeys.sessions });
        expect(invalidated).toHaveBeenCalledWith({ queryKey: queryKeys.session('a') });

        client.setSessionPinned = async () => {
            throw new Error('nope');
        };
        await act(async () => pin.result.current.mutate({ id: 'b', pinned: true }));
        await waitFor(() => expect(pin.result.current.isError).toBe(true));
        expect(queryClient.getQueryData<{ sessions: Session[] }>(queryKeys.sessions)?.sessions.find((s) => s.id === 'b')?.pinned).toBeFalsy();
    });

    it('deleting the open chat goes back to the new-chat screen; another chat stays put', async () => {
        const { client, wrapper } = setup();
        const open = await renderHook(() => useDeleteSession('a', () => mockReplace('/')), { wrapper });

        await act(async () => open.result.current.mutate('b'));
        await waitFor(() => expect(client.deletedSessions).toEqual(['b']));
        expect(mockReplace).not.toHaveBeenCalled();

        await act(async () => open.result.current.mutate('a'));
        await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/'));
    });
});
