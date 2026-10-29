// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/** The greeting stream hands every outcome, failures included, to its caller. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { streamGreeting } from '../api';
import type { GreetingEvent } from '../../types';

function sseResponse(body: string): Response {
    const stream = new ReadableStream<Uint8Array>({
        start(controller) {
            // Split mid-event, as a real connection may deliver it.
            const bytes = new TextEncoder().encode(body);
            const half = Math.floor(bytes.length / 2);
            controller.enqueue(bytes.slice(0, half));
            controller.enqueue(bytes.slice(half));
            controller.close();
        },
    });
    return new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

const data = (events: GreetingEvent[]) => events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');

async function collect(): Promise<GreetingEvent[]> {
    const events: GreetingEvent[] = [];
    await streamGreeting((e) => events.push(e), new AbortController().signal);
    return events;
}

describe('streamGreeting', () => {
    beforeEach(() => { vi.stubGlobal('fetch', vi.fn()); });
    afterEach(() => { vi.unstubAllGlobals(); });

    it('passes on the stage, the words and the end, in order', async () => {
        const sent: GreetingEvent[] = [
            { type: 'status', phase: 'loading', model: 'Gemma-4-E4B-it-GGUF', device: 'gpu' },
            { type: 'chunk', content: 'Welcome back. ' },
            { type: 'chunk', content: 'Pick up the parser?' },
            { type: 'done', text: 'Welcome back. Pick up the parser?' },
        ];
        vi.mocked(fetch).mockResolvedValue(sseResponse(data(sent)));
        expect(await collect()).toEqual(sent);
        expect(vi.mocked(fetch).mock.calls[0][0]).toMatch(/\/system\/greeting$/);
        expect(vi.mocked(fetch).mock.calls[0][1]).toMatchObject({ method: 'POST' });
    });

    it('reports a stream that ends without an answer', async () => {
        vi.mocked(fetch).mockResolvedValue(sseResponse(data([{ type: 'chunk', content: 'Hel' }])));
        const events = await collect();
        expect(events[events.length - 1]).toEqual({ type: 'error', message: 'The greeting stopped before it finished.' });
    });

    it('reports an unreachable backend', async () => {
        vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'));
        expect(await collect()).toEqual([
            { type: 'error', message: "Can't reach GAIA's backend, so the agent can't say hello yet." },
        ]);
    });

    it("reports the backend's own words for an HTTP error", async () => {
        vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: 'GAIA is shutting down' }), { status: 503 }));
        expect(await collect()).toEqual([{ type: 'error', message: 'GAIA is shutting down' }]);
    });

    it('says nothing once the empty chat is gone', async () => {
        const controller = new AbortController();
        controller.abort();
        vi.mocked(fetch).mockRejectedValue(Object.assign(new Error('aborted'), { name: 'AbortError' }));
        const onEvent = vi.fn();
        await streamGreeting(onEvent, controller.signal);
        expect(onEvent).not.toHaveBeenCalled();
    });
});
