// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react-native';
import { Platform } from 'react-native';

import type { Message } from '@/api/client.types';
import { translator } from '@/i18n/useT';
import { ThemeProvider } from '@/ui';
import { SheetHost } from '@/ui/overlays/SheetHost';

import { AssistantBubble } from '../messages/AssistantBubble';

jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(() => Promise.resolve(true)) }));
jest.mock('expo-constants', () => ({ __esModule: true, default: { expoConfig: { version: 'test' } } }));
jest.mock('react-native-safe-area-context', () => ({ useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }) }));

const t = translator('en');
const describeNative = Platform.OS === 'web' ? describe.skip : describe;
const throwing = {
    columns: ['Name'],
    get rows(): never {
        throw new Error('payload threw');
    },
};
const message: Message = {
    id: 1,
    session_id: 's',
    role: 'assistant',
    content: 'The answer text.',
    created_at: '2026-11-13T14:05:09Z',
    rag_sources: null,
    cards: [
        { render: 'list', data: { items: ['fine'] } },
        { render: 'table', data: { rows: [] } },
        { render: 'chart', data: {} },
        { render: 'table', data: throwing },
    ],
};

describeNative('answer cards', () => {
    it('shows the fallback for an invalid or unknown card and an error for a throwing one, and the rest of the answer stays', async () => {
        const error = jest.spyOn(console, 'error').mockImplementation(() => undefined);
        await render(
            <QueryClientProvider client={new QueryClient()}>
                <ThemeProvider>
                    <SheetHost>
                        <AssistantBubble message={message} />
                    </SheetHost>
                </ThemeProvider>
            </QueryClientProvider>,
        );

        expect(screen.getByText('fine')).toBeTruthy();
        expect(screen.getByText(t('render.invalidPayload', { render: 'table' }))).toBeTruthy();
        expect(screen.getByText(t('render.unsupportedType', { render: 'chart' }))).toBeTruthy();
        expect(screen.getByText(t('render.cardFailed'))).toBeTruthy();
        expect(screen.getByText('The answer text.')).toBeTruthy();
        error.mockRestore();
    });
});
