// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot } from 'test-renderer';

import { GaiaApiError } from '@/api/errors';
import { createFakeClient } from '@/api/fake';
import { GaiaClientProvider } from '@/data/GaiaClientProvider';
import { useChatStreamStore } from '@/state/chatStreams';

import { QuestionCard } from './QuestionCard';
import type { QuestionCardData } from './questionLogic';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const buttons: Record<string, { onPress: () => void }> = {};

jest.mock('@/ui', () => {
    const { Text: RNText } = jest.requireActual('react-native');
    const Pass = ({ children }: { children?: unknown }) => children ?? null;
    return {
        Badge: ({ label }: { label: string }) => <RNText>{label}</RNText>,
        Button: (p: { label: string; onPress: () => void }) => {
            buttons[p.label] = p;
            return null;
        },
        Notice: ({ title, message }: { title: string; message: string }) => <RNText>{`${title} | ${message}`}</RNText>,
        Row: Pass,
        Stack: Pass,
        Surface: Pass,
        Text: RNText,
        TextField: () => null,
    };
});
jest.mock('./QuestionText', () => {
    const { Text: RNText } = jest.requireActual('react-native');
    return { QuestionText: ({ text }: { text: string }) => <RNText>{text}</RNText> };
});

const data: QuestionCardData = {
    sessionId: 's1',
    requestId: 'r1',
    question: 'Which folder?',
    options: [{ value: 'dl', label: 'Downloads' }],
    allowFreeText: true,
    sensitive: false,
    expiresAt: null,
};

beforeEach(() => useChatStreamStore.setState({ turns: {}, resolved: {} }));

async function pickDownloads(failWith?: Error) {
    const client = createFakeClient();
    if (failWith) {
        client.sendUserInput = async () => {
            throw failWith;
        };
    }
    const r = createRoot({ textComponentTypes: ['Text', 'RCTText'] });
    await act(async () => {
        r.render(
            <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
                <GaiaClientProvider client={client}>
                    <QuestionCard data={data} />
                </GaiaClientProvider>
            </QueryClientProvider>,
        );
    });
    await act(async () => buttons.Downloads.onPress());
    return { client, shown: JSON.stringify(r.container.toJSON()) };
}

describe('QuestionCard', () => {
    it('sends the picked option and shows what was answered', async () => {
        const { client, shown } = await pickDownloads();
        expect(client.answers).toEqual([{ session_id: 's1', request_id: 'r1', value: 'dl', display: 'Downloads' }]);
        expect(shown).toContain('You answered: Downloads');
    });

    it('says the question is gone on a 404, and reports any other failure', async () => {
        expect((await pickDownloads(new GaiaApiError(404, 'gone'))).shown).toContain('no longer waiting');
        const failed = (await pickDownloads(new GaiaApiError(500, 'Backend unavailable'))).shown;
        expect(failed).toContain('Failed to send the answer.');
        expect(failed).not.toContain('no longer waiting');
    });
});
