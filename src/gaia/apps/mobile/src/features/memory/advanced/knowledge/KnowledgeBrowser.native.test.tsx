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

import { SAMPLE_KNOWLEDGE } from '../../devScenes';
import { KNOWLEDGE_SEARCH_DEBOUNCE_MS, KnowledgeBrowser } from './KnowledgeBrowser';

// The web project's renderer can't mount react-native-web's DOM.
const describeNative = Platform.OS === 'web' ? describe.skip : describe;

describeNative('KnowledgeBrowser', () => {
    it('asks the PC once the typing pauses, with the search and the category filter the web sends', async () => {
        const client = createFakeClient({ memoryKnowledge: SAMPLE_KNOWLEDGE });
        const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const wrap = ({ children }: { children: ReactNode }) => (
            <QueryClientProvider client={queryClient}>
                <GaiaClientProvider client={client}>
                    <SafeAreaProvider initialMetrics={{ frame: { x: 0, y: 0, width: 390, height: 800 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } }}>
                        <ThemeProvider>{children}</ThemeProvider>
                    </SafeAreaProvider>
                </GaiaClientProvider>
            </QueryClientProvider>
        );
        await render(<KnowledgeBrowser contexts={['global']} notify={() => undefined} />, { wrapper: wrap });
        await screen.findByText('Call me Sam.');
        expect(client.knowledgeReads[0]).toMatchObject({ sort_by: 'updated_at', order: 'desc', offset: 0, limit: 15, include_sensitive: true, include_superseded: false });

        const reads = client.knowledgeReads.length;
        fireEvent.changeText(screen.getByTestId('memory-knowledge-search'), 'Sam');
        expect(client.knowledgeReads).toHaveLength(reads);

        await waitFor(() => expect(client.knowledgeReads.at(-1)).toMatchObject({ search: 'Sam' }), { timeout: KNOWLEDGE_SEARCH_DEBOUNCE_MS * 4 });
        await waitFor(() => expect(screen.queryByText(/Calgary/)).toBeNull());
        expect(screen.getByText('Call me Sam.')).toBeTruthy();

        fireEvent.press(screen.getByTestId('memory-knowledge-filter-preference'));
        await waitFor(() => expect(client.knowledgeReads.at(-1)).toMatchObject({ search: 'Sam', category: ['preference'], offset: 0 }));
    });
});
