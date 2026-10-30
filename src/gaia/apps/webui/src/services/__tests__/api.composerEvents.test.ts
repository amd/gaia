// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * The stream events behind the message queue and the context meter reach the
 * chat, and the follow-up / retry calls send the shapes the backend reads.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { waitFor } from '@testing-library/react';
import { chooseRetry, retryWith, sendFollowUp, sendMessageStream, type StreamCallbacks } from '../api';

function sseResponse(events: Record<string, unknown>[]): Response {
    const body = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
    const stream = new ReadableStream<Uint8Array>({
        start(controller) {
            controller.enqueue(new TextEncoder().encode(body));
            controller.close();
        },
    });
    return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

function jsonResponse(body: unknown): Response {
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
}

describe('composer stream events and calls', () => {
    beforeEach(() => { vi.stubGlobal('fetch', vi.fn()); });
    afterEach(() => { vi.unstubAllGlobals(); });

    it('routes followup_taken, followup_returned and context_usage to onAgentEvent', async () => {
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(sseResponse([
            { type: 'context_usage', window: 32768, used: 9000 },
            { type: 'followup_taken', text: 'also this' },
            { type: 'followup_returned', texts: ['too late'] },
            { type: 'done', content: 'ok' },
        ]));
        const onAgentEvent = vi.fn();
        const onDone = vi.fn();
        const callbacks: StreamCallbacks = { onChunk: vi.fn(), onAgentEvent, onDone, onError: vi.fn() };

        sendMessageStream('s', 'hi', callbacks);
        await waitFor(() => expect(onDone).toHaveBeenCalled());

        expect(onAgentEvent.mock.calls.map(([e]) => e.type)).toEqual([
            'context_usage', 'followup_taken', 'followup_returned',
        ]);
    });

    it('posts the bodies the chat routes read', async () => {
        const mocked = fetch as ReturnType<typeof vi.fn>;
        mocked.mockImplementation(async () => jsonResponse({}));

        await sendFollowUp('s', 'more');
        await retryWith('s', 7, 'Other-GGUF');
        await chooseRetry('s', 7, 'retry');

        const sent = mocked.mock.calls.map(([url, init]) => [String(url).replace(/^.*\/api/, '/api'), JSON.parse(init.body)]);
        expect(sent).toEqual([
            ['/api/chat/followup', { session_id: 's', text: 'more' }],
            ['/api/chat/retry-with', { session_id: 's', message_id: 7, model: 'Other-GGUF' }],
            ['/api/chat/retry-with/choose', { session_id: 's', message_id: 7, keep: 'retry' }],
        ]);
    });
});
