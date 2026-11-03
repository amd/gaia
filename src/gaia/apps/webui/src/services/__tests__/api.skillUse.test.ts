// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A `skill_use` event reaches the chat view. It was first dropped here: the
 * stream reader passes only listed agent events on, so the store and the row
 * worked in tests while the live chat never showed "Using skill".
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { waitFor } from '@testing-library/react';
import { sendMessageStream, type StreamCallbacks } from '../api';

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

describe('skill_use SSE dispatch (services/api.ts)', () => {
    beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it('routes skill_use to onAgentEvent with the skill, how and loaded list', async () => {
        const event = { type: 'skill_use', skill: 'analyze-recording', how: 'auto', loaded: ['gaia-voice', 'analyze-recording'] };
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(sseResponse([event, { type: 'done', content: 'ok' }]));
        const onAgentEvent = vi.fn();
        const onDone = vi.fn();
        const callbacks: StreamCallbacks = { onChunk: vi.fn(), onAgentEvent, onDone, onError: vi.fn() };

        sendMessageStream('session-1', 'hi', callbacks);

        await waitFor(() => expect(onDone).toHaveBeenCalled());
        expect(onAgentEvent).toHaveBeenCalledWith(expect.objectContaining(event));
    });
});
