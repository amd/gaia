// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';

import type { Message } from '@/api/client.types';
import { GaiaConfigError } from '@/api/errors';
import { queryKeys, useGaiaClient } from '@/data';
import { describeError, useT } from '@/i18n/useT';
import { retryModel, useComparisonStore, type Comparison } from '@/state/answerComparison';

export interface AnswerComparisonControls {
    comparison: Comparison | null;
    /** A comparison is waiting on the model; the chat takes no new message meanwhile. */
    comparing: boolean;
    retryWith(message: Message, model: string): void;
    keep(which: 'original' | 'retry'): Promise<void>;
    /** Closing a finished comparison keeps the original answer. */
    close(): void;
}

/** "Retry with another model": one answer set against another model's, and the person's pick. */
export function useAnswerComparison(sessionId: string): AnswerComparisonControls {
    const t = useT();
    const client = useGaiaClient();
    const queryClient = useQueryClient();
    const comparison = useComparisonStore((s) => s.byChat[sessionId] ?? null);
    const store = useComparisonStore.getState;

    const retryWith = useCallback(
        (message: Message, model: string) => {
            const open = store().byChat[sessionId];
            if (open?.state.status === 'running') return;
            store().start(sessionId, message.id, model);
            const settle = store().settle;
            void (async () => {
                try {
                    if (!client) throw new GaiaConfigError('mobileApp.error.noConnection');
                    const result = await client.retryWith(sessionId, message.id, model);
                    settle(sessionId, message.id, { status: 'ready', result });
                } catch (error) {
                    settle(sessionId, message.id, { status: 'failed', model, error: describeError(error, t) });
                }
            })();
        },
        [client, sessionId, store, t],
    );

    const keep = useCallback(
        async (which: 'original' | 'retry') => {
            const open = store().byChat[sessionId];
            if (!open) return;
            store().setKeeping(sessionId, which);
            try {
                if (!client) throw new GaiaConfigError('mobileApp.error.noConnection');
                await client.chooseRetry(sessionId, open.messageId, which);
                if (which === 'retry') {
                    await queryClient.invalidateQueries({ queryKey: queryKeys.messages(sessionId) });
                    void queryClient.invalidateQueries({ queryKey: queryKeys.sessions });
                }
                store().close(sessionId);
            } catch (error) {
                store().settle(sessionId, open.messageId, {
                    status: 'failed',
                    model: retryModel(open.state),
                    error: describeError(error, t),
                });
            }
        },
        [client, queryClient, sessionId, store, t],
    );

    const close = useCallback(() => {
        const open = store().byChat[sessionId];
        if (open?.state.status === 'ready') void keep('original');
        else store().close(sessionId);
    }, [keep, sessionId, store]);

    return { comparison, comparing: comparison?.state.status === 'running', retryWith, keep, close };
}
