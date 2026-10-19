// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useCallback, useEffect, useRef, useState } from 'react';
import * as api from '../services/api';
import { useChatStore } from '../stores/chatStore';
import { log } from '../utils/logger';
import type { ComparisonState } from '../components/AnswerComparison';
import type { Message } from '../types';

export interface Comparison {
    messageId: number;
    state: ComparisonState;
    keeping: 'original' | 'retry' | null;
}

/** "Retry with…": one answer compared against another model's, and the user's pick. */
export function useAnswerComparison({ sessionId, isStreaming, isStale, setMessages, refreshContext }: {
    sessionId: string;
    isStreaming: boolean;
    isStale: () => boolean;
    setMessages: (messages: Message[]) => void;
    refreshContext: () => void;
}) {
    const [comparison, setComparison] = useState<Comparison | null>(null);
    const comparing = comparison?.state.status === 'running';
    const comparingRef = useRef(false);
    comparingRef.current = comparing;
    const comparisonRef = useRef(comparison);
    comparisonRef.current = comparison;

    useEffect(() => { setComparison(null); }, [sessionId]);

    const handleRetryWith = useCallback((message: Message, model: string) => {
        if (isStreaming || comparingRef.current) return;
        log.chat.info(`Retrying message ${message.id} with ${model}`);
        setComparison({ messageId: message.id, state: { status: 'running', model }, keeping: null });
        api.retryWith(sessionId, message.id, model)
            .then((result) => {
                if (isStale()) return;
                setComparison((c) => (c && c.messageId === message.id
                    ? { ...c, state: { status: 'ready', result } }
                    : c));
            })
            .catch((err) => {
                log.chat.error(`Retry with ${model} failed`, err);
                if (isStale()) return;
                setComparison((c) => (c && c.messageId === message.id
                    ? { ...c, state: { status: 'failed', model, error: err instanceof Error ? err.message : String(err) } }
                    : c));
            });
    }, [sessionId, isStreaming, isStale]);

    const handleKeepAnswer = useCallback(async (which: 'original' | 'retry') => {
        if (!comparison) return;
        const { messageId } = comparison;
        setComparison({ ...comparison, keeping: which });
        try {
            const kept = await api.chooseRetry(sessionId, messageId, which);
            if (isStale()) return;
            if (kept.kept === 'retry') {
                const s = kept.stats;
                setMessages(useChatStore.getState().messages.map((m) => (m.id === messageId
                    ? {
                        ...m,
                        content: kept.content,
                        stats: {
                            tokens_per_second: s.tokens_per_second ?? 0,
                            time_to_first_token: s.time_to_first_token ?? 0,
                            input_tokens: s.input_tokens ?? 0,
                            output_tokens: s.output_tokens ?? 0,
                            model: s.model,
                            retried: true,
                            seconds: s.seconds,
                        },
                    }
                    : m)));
                refreshContext();
            }
            setComparison(null);
        } catch (err) {
            log.chat.error('Could not keep the chosen answer', err);
            if (isStale()) return;
            setComparison((c) => (c ? {
                ...c,
                keeping: null,
                state: { status: 'failed', model: c.state.status === 'ready' ? c.state.result.model : c.state.model, error: err instanceof Error ? err.message : String(err) },
            } : c));
        }
    }, [comparison, sessionId, setMessages, isStale, refreshContext]);

    const handleCloseComparison = useCallback(() => {
        // Closing a finished comparison keeps the original answer.
        if (comparison?.state.status === 'ready') {
            void handleKeepAnswer('original');
            return;
        }
        setComparison(null);
    }, [comparison, handleKeepAnswer]);

    /** A new turn moves the chat on, so an open comparison keeps the original answer. */
    const closeForNewTurn = useCallback(() => {
        const open = comparisonRef.current;
        if (!open) return;
        if (open.state.status === 'ready') {
            api.chooseRetry(sessionId, open.messageId, 'original')
                .catch((err) => log.chat.error('Could not close the answer comparison', err));
        }
        setComparison(null);
    }, [sessionId]);

    return { comparison, comparing, comparingRef, handleRetryWith, handleKeepAnswer, handleCloseComparison, closeForNewTurn };
}
