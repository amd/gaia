// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Wire-level contract test for `needs_confirmation` (issue #2109,
 * Increment 2, stateless D1).
 *
 * The plan requires `'needs_confirmation'` to be added to `AGENT_EVENT_TYPES`
 * (services/api.ts) so it rides the EXISTING generic agent-event dispatch in
 * `consumeSSEResponse` — no bespoke `else if` branch. This test drives that
 * dispatch for real: a genuine SSE byte stream (not a directly-invoked
 * callback, unlike the ChatView-level tests) containing a `needs_confirmation`
 * event, parsed by the real (unmocked) `sendMessageStream`.
 *
 * Today `needs_confirmation` is in neither `StreamEventType` nor
 * `AGENT_EVENT_TYPES`, so `consumeSSEResponse` takes its `else` branch
 * (`log.stream.warn('Unknown SSE event type...')`) and never calls
 * `onAgentEvent` for it — this test is expected to fail (red) until the
 * implementer adds the type.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { waitFor } from '@testing-library/react';
import { sendMessageStream, type StreamCallbacks } from '../api';

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

describe('needs_confirmation SSE dispatch (services/api.ts)', () => {
    beforeEach(() => {
        vi.stubGlobal('fetch', vi.fn());
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('routes a needs_confirmation SSE event to onAgentEvent with action + summary intact', async () => {
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
            sseResponse([
                { type: 'needs_confirmation', action: 'send_now', summary: 'Send it now?' },
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
                type: 'needs_confirmation',
                action: 'send_now',
                summary: 'Send it now?',
            }),
        );
    });

    it('does not surface needs_confirmation as an error and does not silently drop it', async () => {
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
            sseResponse([
                { type: 'needs_confirmation', action: 'archive_thread', summary: 'Archive it?' },
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
        // Exactly one agent event for the one needs_confirmation frame sent —
        // proves it wasn't dropped as an "unknown SSE event type".
        expect(onAgentEvent).toHaveBeenCalledTimes(1);
    });
});
