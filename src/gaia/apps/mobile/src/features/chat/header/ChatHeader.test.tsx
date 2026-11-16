// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';

import { ChatHeader } from './ChatHeader';

jest.mock('@/features/nav/ScreenHeader', () => ({ ScreenHeader: ({ end }: { end?: ReactNode }) => end ?? null }));

const SESSION = {
    id: 'chat-1',
    title: 'A chat',
    created_at: '2026-11-14T00:00:00Z',
    updated_at: '2026-11-14T00:00:00Z',
    model: '',
    system_prompt: null,
    message_count: 0,
    document_ids: [],
};

async function mount(onOpenSessionPanel?: () => void) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrap = ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
            <GaiaClientProvider client={createFakeClient({ sessions: [SESSION] })}>
                <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
                    <ThemeProvider>{children}</ThemeProvider>
                </SafeAreaProvider>
            </GaiaClientProvider>
        </QueryClientProvider>
    );
    await render(<ChatHeader sessionId="chat-1" onOpenSessionPanel={onOpenSessionPanel} />, { wrapper: wrap });
    await screen.findByTestId('private-toggle');
}

describe('ChatHeader session panel button', () => {
    it('shows only when onOpenSessionPanel is set', async () => {
        await mount();
        expect(screen.queryByTestId('session-panel-toggle')).toBeNull();
    });

    it('shows when onOpenSessionPanel is set', async () => {
        await mount(() => undefined);
        expect(screen.getByTestId('session-panel-toggle')).toBeTruthy();
    });
});
