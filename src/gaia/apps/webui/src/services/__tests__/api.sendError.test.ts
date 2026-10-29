// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { waitFor } from '@testing-library/react';
import { ApiError, sendMessageStream, type StreamCallbacks } from '../api';

function fail(status: number, body: string) {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
        new Response(body, { status, headers: { 'content-type': 'application/json' } }),
    );
}

async function sendAndCatch(): Promise<ApiError> {
    const onError = vi.fn();
    const callbacks: StreamCallbacks = { onChunk: vi.fn(), onAgentEvent: vi.fn(), onDone: vi.fn(), onError };
    sendMessageStream('session-1', 'hi', callbacks);
    await waitFor(() => expect(onError).toHaveBeenCalled());
    return onError.mock.calls[0][0] as ApiError;
}

describe('a refused send', () => {
    beforeEach(() => { vi.stubGlobal('fetch', vi.fn()); });
    afterEach(() => { vi.unstubAllGlobals(); });

    it("says the server's reason in words, not the raw JSON body", async () => {
        fail(409, JSON.stringify({ detail: 'This chat is still answering.' }));
        const err = await sendAndCatch();
        expect(err.message).toBe('This chat is still answering.');
        expect(err.httpStatus).toBe(409);
    });

    it('turns a bare 500 into a plain sentence', async () => {
        fail(500, JSON.stringify({ detail: 'Internal Server Error' }));
        const err = await sendAndCatch();
        expect(err.message).toBe('Server error. Please try again.');
        expect(err.message).not.toContain('{');
    });

    it('a stream that closes before saying anything is an error, not a blank answer', async () => {
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
            new Response('', { status: 200, headers: { 'content-type': 'text/event-stream' } }),
        );
        const onDone = vi.fn();
        const onError = vi.fn();
        sendMessageStream('session-1', 'hi', { onChunk: vi.fn(), onAgentEvent: vi.fn(), onDone, onError });
        await waitFor(() => expect(onError).toHaveBeenCalled());
        expect(onError.mock.calls[0][0].message).toMatch(/closed the connection before any answer/);
        expect(onDone).not.toHaveBeenCalled();
    });

    it('a stream that closes after some answer still finishes the turn', async () => {
        const body = `data: ${JSON.stringify({ type: 'chunk', content: 'Hello' })}

`;
        (fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
            new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } }),
        );
        const onDone = vi.fn();
        const onError = vi.fn();
        sendMessageStream('session-1', 'hi', { onChunk: vi.fn(), onAgentEvent: vi.fn(), onDone, onError });
        await waitFor(() => expect(onDone).toHaveBeenCalled());
        expect(onError).not.toHaveBeenCalled();
    });
});
