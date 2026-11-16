// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react-native';
import type { ReactNode } from 'react';
import { Platform } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data';
import { ThemeProvider } from '@/ui';

import { SAMPLE_KNOWLEDGE } from '../devScenes';
import type { MemoryNotice } from '../useMemoryNotice';
import { AboutPane } from './AboutPane';

// The web project's renderer can't mount react-native-web's DOM.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

describeNative('AboutPane', () => {
    it('Undo after Forget saves the same memory again, with the same text and category', async () => {
        const client = createFakeClient({ memoryKnowledge: SAMPLE_KNOWLEDGE });
        const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const notices: MemoryNotice[] = [];
        const wrap = ({ children }: { children: ReactNode }) => (
            <QueryClientProvider client={queryClient}>
                <GaiaClientProvider client={client}>
                    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
                        <ThemeProvider>{children}</ThemeProvider>
                    </SafeAreaProvider>
                </GaiaClientProvider>
            </QueryClientProvider>
        );
        await render(<AboutPane notify={(n) => notices.push(n)} memoryEnabled onOpenAdvanced={() => undefined} />, { wrapper: wrap });
        await screen.findByText('Call me Sam.');

        fireEvent.press(screen.getByTestId('memory-about-forget-k8'));
        await waitFor(() => expect(notices).toHaveLength(1));
        expect(client.knowledgeCalls).toEqual([{ action: 'delete', id: 'k8' }]);
        expect(screen.queryByText('Call me Sam.')).toBeNull();

        notices[0]!.action!.onPress();
        await screen.findByText('Call me Sam.');
        const restored = client.knowledgeCalls[1]!;
        expect(restored.action).toBe('add');
        expect(restored.action === 'add' && restored.body).toMatchObject({ content: 'Call me Sam.', category: 'preference' });
    });
});
