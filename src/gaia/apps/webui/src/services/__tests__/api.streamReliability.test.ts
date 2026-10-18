// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * How the chat stream behaves when the backend is busy, slow or silently gone.
 *
 * The backend sends a keepalive every 5 s for the whole turn, so a stream that
 * stays silent for 30 s is a dead connection, not a slow model. A send the
 * backend never answers must end too, and the user must see that GAIA is
 * waiting rather than a bare spinner.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { attachToRun, sendMessageStream, type StreamCallbacks } from '../api';

const encoder = new TextEncoder();

/** An SSE response whose body the test feeds by hand, and which never closes on its own. */
function openStream() {
    let ctrl!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(c) { ctrl = c; } });
    return {
        response: new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } }),
        send: (event: Record<string, unknown>) => ctrl.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`)),
        keepalive: () => ctrl.enqueue(encoder.encode(': keepalive\n\n')),
    };
}

function callbacks() {
    return {
        onChunk: vi.fn<StreamCallbacks['onChunk']>(),
        onAgentEvent: vi.fn<StreamCallbacks['onAgentEvent']>(),
        onDone: vi.fn<StreamCallbacks['onDone']>(),
        onError: vi.fn<(err: Error & { httpStatus?: number }) => void>(),
    };
}

const fetchMock = () => fetch as unknown as ReturnType<typeof vi.fn>;

/** A fetch that never answers until its signal aborts. */
function hangingFetch() {
    return (_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
    });
}

describe('chat stream idle watchdog (E4)', () => {
    beforeEach(() => {
        vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] });
        vi.stubGlobal('fetch', vi.fn());
    });
    afterEach(() => {
        vi.useRealTimers();
        vi.unstubAllGlobals();
    });

    it('ends a stream that goes silent for 30 s with a stalled error', async () => {
        const s = openStream();
        fetchMock().mockResolvedValue(s.response);
        const cb = callbacks();
        sendMessageStream('s1', 'hi', cb);
        await vi.advanceTimersByTimeAsync(0);
        s.send({ type: 'status', status: 'working', message: 'Thinking' });
        await vi.advanceTimersByTimeAsync(29_000);
        expect(cb.onError).not.toHaveBeenCalled();
        await vi.advanceTimersByTimeAsync(2_000);
        expect(cb.onError).toHaveBeenCalledTimes(1);
        expect(cb.onError.mock.calls[0][0].name).toBe('StreamStalledError');
        expect(cb.onDone).not.toHaveBeenCalled();
    });

    it('keeps a stream open while keepalives arrive', async () => {
        const s = openStream();
        fetchMock().mockResolvedValue(s.response);
        const cb = callbacks();
        sendMessageStream('s1', 'hi', cb);
        await vi.advanceTimersByTimeAsync(0);
        for (let i = 0; i < 24; i++) {
            await vi.advanceTimersByTimeAsync(5_000);
            s.keepalive();
        }
        expect(cb.onError).not.toHaveBeenCalled();
    });

    it('gives a turn waiting in the queue for the model a much longer silence', async () => {
        const s = openStream();
        fetchMock().mockResolvedValue(s.response);
        const cb = callbacks();
        sendMessageStream('s1', 'hi', cb);
        await vi.advanceTimersByTimeAsync(0);
        s.send({ type: 'status', status: 'working', phase: 'queued', queue_position: 1, message: 'Waiting for the GPU' });
        await vi.advanceTimersByTimeAsync(5 * 60_000);
        expect(cb.onError).not.toHaveBeenCalled();
        // Once the turn starts, silence counts again.
        s.send({ type: 'status', status: 'working', message: 'Thinking' });
        await vi.advanceTimersByTimeAsync(31_000);
        expect(cb.onError.mock.calls[0][0].name).toBe('StreamStalledError');
    });

    it('still drops a queued turn whose connection died, after 10 minutes', async () => {
        const s = openStream();
        fetchMock().mockResolvedValue(s.response);
        const cb = callbacks();
        sendMessageStream('s1', 'hi', cb);
        await vi.advanceTimersByTimeAsync(0);
        s.send({ type: 'status', status: 'working', phase: 'queued', queue_position: 2, message: 'Waiting for the GPU' });
        await vi.advanceTimersByTimeAsync(10 * 60_000 + 1_000);
        expect(cb.onError.mock.calls[0][0].name).toBe('StreamStalledError');
    });

    it('does not report a connection that dies after the turn ended', async () => {
        const s = openStream();
        fetchMock().mockResolvedValue(s.response);
        const cb = callbacks();
        sendMessageStream('s1', 'hi', cb);
        await vi.advanceTimersByTimeAsync(0);
        s.send({ type: 'done', content: 'ok' });
        await vi.advanceTimersByTimeAsync(31_000);
        expect(cb.onDone).toHaveBeenCalledTimes(1);
        expect(cb.onError).not.toHaveBeenCalled();
    });

    it('applies to a re-attached run too', async () => {
        const s = openStream();
        fetchMock().mockResolvedValue(s.response);
        const cb = callbacks();
        attachToRun('s1', cb);
        await vi.advanceTimersByTimeAsync(31_000);
        expect(cb.onError.mock.calls[0][0].name).toBe('StreamStalledError');
    });

    it('treats an attach to a run that already finished as done, not an error', async () => {
        fetchMock().mockResolvedValue(new Response(JSON.stringify({ detail: 'No active run for this session.' }), { status: 404 }));
        const cb = callbacks();
        attachToRun('s1', cb);
        await vi.advanceTimersByTimeAsync(0);
        expect(cb.onError).not.toHaveBeenCalled();
        expect(cb.onDone).toHaveBeenCalledTimes(1);
    });
});

describe('bounded send (E1)', () => {
    beforeEach(() => {
        vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] });
        vi.stubGlobal('fetch', vi.fn());
    });
    afterEach(() => {
        vi.useRealTimers();
        vi.unstubAllGlobals();
    });

    it('shows a waiting status while the backend has not started the turn', async () => {
        fetchMock().mockImplementation(hangingFetch());
        const cb = callbacks();
        sendMessageStream('s1', 'hi', cb);
        await vi.advanceTimersByTimeAsync(5_000);
        const waiting = cb.onAgentEvent.mock.calls.map((c) => c[0]).find((e) => e.type === 'status' && e.phase === 'waiting');
        expect(waiting?.message).toMatch(/waiting/i);
    });

    it('gives up on a send the backend never answers, with a timeout error', async () => {
        fetchMock().mockImplementation(hangingFetch());
        const cb = callbacks();
        sendMessageStream('s1', 'hi', cb);
        await vi.advanceTimersByTimeAsync(89_000);
        expect(cb.onError).not.toHaveBeenCalled();
        await vi.advanceTimersByTimeAsync(2_000);
        expect(cb.onError).toHaveBeenCalledTimes(1);
        expect(cb.onError.mock.calls[0][0].name).toBe('StreamTimeoutError');
    });

    it('does not report a user Stop as an error', async () => {
        fetchMock().mockImplementation(hangingFetch());
        const cb = callbacks();
        const controller = sendMessageStream('s1', 'hi', cb);
        await vi.advanceTimersByTimeAsync(1_000);
        controller.abort();
        await vi.advanceTimersByTimeAsync(120_000);
        expect(cb.onError).not.toHaveBeenCalled();
    });

    it('reports a busy server as an ApiError carrying the status, not a raw JSON string', async () => {
        fetchMock().mockResolvedValue(new Response(
            JSON.stringify({ detail: 'The server is busy processing other chat requests. Please try again in a few moments.' }),
            { status: 429 },
        ));
        const cb = callbacks();
        sendMessageStream('s1', 'hi', cb);
        await vi.advanceTimersByTimeAsync(0);
        const err = cb.onError.mock.calls[0][0];
        expect(err.name).toBe('ApiError');
        expect(err.httpStatus).toBe(429);
    });
});
