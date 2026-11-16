// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { RetryResult } from '@/api/client.types';

import { retryModel, useComparisonStore } from './answerComparison';

const result: RetryResult = {
    message_id: 5,
    model: 'Other-GGUF',
    content: 'Another answer',
    stats: { model: 'Other-GGUF', retried: true, seconds: 4, load_seconds: 2, input_tokens: 1, output_tokens: 2, tokens_per_second: 9, time_to_first_token: 1 },
};

describe('answer comparison state', () => {
    afterEach(() => useComparisonStore.setState({ byChat: {} }));

    it('runs, then holds the retry beside the original, and a late result for another message is dropped', () => {
        const s = useComparisonStore.getState();
        s.start('chat', 5, 'Other-GGUF');
        expect(useComparisonStore.getState().byChat.chat?.state).toEqual({ status: 'running', model: 'Other-GGUF' });

        s.settle('chat', 9, { status: 'ready', result });
        expect(useComparisonStore.getState().byChat.chat?.state.status).toBe('running');

        s.settle('chat', 5, { status: 'ready', result });
        const open = useComparisonStore.getState().byChat.chat;
        expect(open?.state.status).toBe('ready');
        expect(open && retryModel(open.state)).toBe('Other-GGUF');

        s.setKeeping('chat', 'retry');
        expect(useComparisonStore.getState().byChat.chat?.keeping).toBe('retry');
        s.close('chat');
        expect(useComparisonStore.getState().byChat.chat).toBeUndefined();
    });
});
