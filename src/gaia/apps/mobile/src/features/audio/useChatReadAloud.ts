// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { useEffect, useRef } from 'react';

import { readsAloudWhenDone } from '@webui/utils/autoReadAloud.core';
import { cleanToolCallContent } from '@webui/utils/toolCallContent';

import type { Message } from '@/api/client.types';
import { useGaiaClient, useSpeechSettings, type ChatStream } from '@/data';

import { useReadAloudStore } from './readAloudStore';

/** The key the bubble's button and the store share for a saved reply. */
export function readAloudId(message: Message): string {
    return `${message.session_id}:${message.id}`;
}

/**
 * A chat's read-aloud upkeep: with "Read each reply aloud" on, a reply that finishes here is read;
 * leaving the chat stops one of its replies being read.
 */
export function useChatReadAloud(sessionId: string, stream: Pick<ChatStream, 'status' | 'error' | 'stopState' | 'failed' | 'failure'>, saved: Message[]): void {
    const client = useGaiaClient();
    const auto = useSpeechSettings().data?.auto === true;
    const busy = stream.status !== 'idle';
    const wasBusy = useRef(busy);

    useEffect(() => {
        const ended = wasBusy.current && !busy;
        wasBusy.current = busy;
        if (!ended || !auto || !client || stream.error) return;
        // The turn ends in the same render the transcript gains its reply; a failed re-read leaves the sent question last.
        const reply = saved.filter((m) => m.role !== 'system').at(-1);
        if (!reply || reply.role !== 'assistant' || reply.id <= 0) return;
        if (!readsAloudWhenDone({ ...reply, stopState: reply.stopState ?? stream.stopState, failed: reply.failed || stream.failed, failure: reply.failure ?? stream.failure })) return;
        void useReadAloudStore.getState().read(client, readAloudId(reply), cleanToolCallContent(reply.content, false));
    }, [busy, auto, client, saved, stream.error, stream.stopState, stream.failed, stream.failure]);

    useEffect(
        () => () => {
            const { messageId, stop } = useReadAloudStore.getState();
            if (messageId?.startsWith(`${sessionId}:`)) stop();
        },
        [sessionId],
    );
}
