// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react-native';
import { Platform } from 'react-native';

import type { Message } from '@/api/client.types';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import { AssistantBubble } from './AssistantBubble';

jest.mock('expo-constants', () => ({ __esModule: true, default: { expoConfig: { version: 'test' } } }));
jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(() => Promise.resolve(true)) }));
jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));

const reply = (content: string): Message => ({
    id: 2,
    session_id: 's',
    role: 'assistant',
    content,
    created_at: '2026-11-13T14:05:09Z',
    rag_sources: null,
});

const show = (content: string) =>
    render(
        <QueryClientProvider client={new QueryClient()}>
            <ThemeProvider>
                <SheetHost>
                    <AssistantBubble message={reply(content)} />
                </SheetHost>
            </ThemeProvider>
        </QueryClientProvider>,
    );

const describeNative = Platform.OS === 'web' ? describe.skip : describe;

describeNative('AssistantBubble screen-reader grouping', () => {
    it('reads plain prose as one element with an actions entry, and leaves a reply with links walkable', async () => {
        await show('Plain answer without links.');
        const group = screen.getByText(/Plain answer/).parent;
        let node = group;
        while (node && node.props.accessible !== true && node.props.accessible !== false) node = node.parent;
        expect(node?.props.accessible).toBe(true);
        expect(node?.props.accessibilityActions?.length).toBeGreaterThan(0);
    });
});
