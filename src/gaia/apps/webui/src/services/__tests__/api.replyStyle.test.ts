// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { sendMessageStream, type StreamCallbacks } from '../api';

const callbacks = (): StreamCallbacks => ({ onChunk: vi.fn(), onAgentEvent: vi.fn(), onDone: vi.fn(), onError: vi.fn() });

function sentBody(): Record<string, unknown> {
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    return JSON.parse((init as RequestInit).body as string);
}

describe('reply_style on /chat/send', () => {
    beforeEach(() => {
        vi.stubGlobal('fetch', vi.fn(async () => new Response('data: {"type":"done"}\n\n', { status: 200, headers: { 'content-type': 'text/event-stream' } })));
    });
    afterEach(() => { vi.unstubAllGlobals(); });

    it('a voice turn is an ordinary send plus reply_style "voice"', () => {
        sendMessageStream('s1', 'find the invoices', callbacks(), undefined, undefined, undefined, undefined, 'voice');
        expect(sentBody()).toEqual({ session_id: 's1', message: 'find the invoices', stream: true, reply_style: 'voice' });
    });

    it('a typed turn sends no reply_style, so the server default (text) applies', () => {
        sendMessageStream('s1', 'hi', callbacks());
        expect(sentBody()).toEqual({ session_id: 's1', message: 'hi', stream: true });
    });
});
