// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Wire-level contract tests for `needs_input` (#2595), mirroring
 * api.confirmation.test.ts's pattern for `needs_confirmation`:
 *
 *   - `needs_input` rides the existing generic agent-event dispatch in
 *     `consumeSSEResponse` (AGENT_EVENT_TYPES) rather than a bespoke branch.
 *   - `respondToInput` posts the exact backend contract found in
 *     `src/gaia/ui/routers/chat.py`'s `POST /api/chat/user-input`:
 *     `{session_id, request_id, value}`.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { waitFor } from '@testing-library/react';
import { sendMessageStream, respondToInput, type StreamCallbacks } from '../api';

/** Build a real SSE-formatted Response streaming the given JSON events. */
function sseResponse(events: Record<string, unknown>[]): Response {
    const body = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
        start(controller) {
            controller.enqueue(encoder.encode(body));
            controller.close();
        },
    });
    return new Response(stream, {
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
    });
}

describe('needs_input SSE dispatch (services/api.ts)', () => {
    beforeEach(() => {
        vi.stubGlobal('fetch', vi.fn());
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('routes a needs_input SSE event to onAgentEvent with its full payload intact', async () => {
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
            sseResponse([
                {
                    type: 'needs_input',
                    request_id: 'req-1',
                    question: 'Which mailbox?',
                    options: [{ value: 'gmail', label: 'Gmail', description: '' }],
                    allow_free_text: true,
                    sensitive: false,
                },
                { type: 'done', content: 'ok' },
            ]),
        );

        const onAgentEvent = vi.fn();
        const onDone = vi.fn();
        const callbacks: StreamCallbacks = {
            onChunk: vi.fn(),
            onAgentEvent,
            onDone,
            onError: vi.fn(),
        };

        sendMessageStream('session-1', 'hi', callbacks);

        await waitFor(() => expect(onDone).toHaveBeenCalled());

        expect(onAgentEvent).toHaveBeenCalledWith(
            expect.objectContaining({
                type: 'needs_input',
                request_id: 'req-1',
                question: 'Which mailbox?',
                allow_free_text: true,
                sensitive: false,
            }),
        );
    });

    it('does not surface needs_input as an error and does not silently drop it', async () => {
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
            sseResponse([
                {
                    type: 'needs_input',
                    request_id: 'req-2',
                    question: 'Proceed?',
                    options: [],
                    allow_free_text: true,
                    sensitive: false,
                },
                { type: 'done', content: 'ok' },
            ]),
        );

        const onAgentEvent = vi.fn();
        const onError = vi.fn();
        const onDone = vi.fn();
        const callbacks: StreamCallbacks = {
            onChunk: vi.fn(),
            onAgentEvent,
            onDone,
            onError,
        };

        sendMessageStream('session-1', 'hi', callbacks);

        await waitFor(() => expect(onDone).toHaveBeenCalled());

        expect(onError).not.toHaveBeenCalled();
        expect(onAgentEvent).toHaveBeenCalledTimes(1);
    });
});

describe('respondToInput (services/api.ts)', () => {
    beforeEach(() => {
        vi.stubGlobal(
            'fetch',
            vi.fn().mockResolvedValue(
                new Response(JSON.stringify({ status: 'ok', request_id: 'req-1' }), {
                    status: 200,
                    headers: { 'content-type': 'application/json' },
                }),
            ),
        );
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('POSTs {session_id, request_id, value} to /chat/user-input', async () => {
        await respondToInput('session-1', 'req-1', 'gmail');

        const mockFetch = fetch as ReturnType<typeof vi.fn>;
        expect(mockFetch).toHaveBeenCalledTimes(1);
        const [url, init] = mockFetch.mock.calls[0];
        expect(String(url)).toContain('/chat/user-input');
        expect(init.method).toBe('POST');
        expect(JSON.parse(init.body)).toEqual({
            session_id: 'session-1',
            request_id: 'req-1',
            value: 'gmail',
        });
    });
});
