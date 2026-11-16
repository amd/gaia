// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react-native';
import { Platform } from 'react-native';

import type { TurnChanges as Turn } from '@/api/client.types';
import { GaiaApiError } from '@/api/errors';
import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { translator } from '@/i18n/useT';
import { ThemeProvider } from '@/ui';

import { TurnChanges } from './TurnChanges';

jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));

const t = translator('en');
const turn: Turn = {
    turn_id: 't1',
    session_id: 's1',
    message_id: 7,
    started_at: '2026-11-15T00:00:00Z',
    finished_at: '2026-11-15T00:01:00Z',
    restored_at: null,
    files: [{ path: '/work/a.txt', change: 'edited', tool: 'write_file' }],
    untracked: [],
    incomplete: false,
};

// Undo is destructive: a file changed since the turn is overwritten only after "Undo anyway".
(Platform.OS === 'web' ? describe.skip : describe)('Undo this turn', () => {
    it('posts force only after Undo anyway', async () => {
        const conflict = new GaiaApiError(409, 'a.txt changed after this turn', undefined, undefined, { status: 'conflict', message: 'a.txt changed after this turn' });
        const client = createFakeClient({ turnChanges: { s1: [turn] }, undoConflict: conflict });
        await render(
            <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
                <GaiaClientProvider client={client}>
                    <ThemeProvider>
                        <TurnChanges sessionId="s1" messageId={7} />
                    </ThemeProvider>
                </GaiaClientProvider>
            </QueryClientProvider>,
        );

        await screen.findByText(t.plural('mobileApp.changes.summary', 1));
        await fireEvent.press(screen.getByTestId('turn-changes-undo'));
        await fireEvent.press(screen.getByTestId('turn-changes-confirm'));
        await screen.findByText('a.txt changed after this turn');
        expect(client.undone).toEqual([{ sessionId: 's1', turnId: 't1', force: false }]);

        await fireEvent.press(screen.getByTestId('turn-changes-force'));
        await screen.findByText(t('mobileApp.changes.done', { parts: t.plural('mobileApp.changes.done.restored', 1) }));
        expect(client.undone.map((u) => u.force)).toEqual([false, true]);
    });
});
