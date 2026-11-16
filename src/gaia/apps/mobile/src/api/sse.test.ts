// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { bytes, streamResponse } from '@/test-support/fakeFetch';

import { GaiaStreamError } from './errors';
import { createSseParser, parseSseStream } from './sse';

function parseAll(chunks: (string | Uint8Array)[]) {
    const parser = createSseParser();
    const events = chunks.flatMap((c) => parser.push(typeof c === 'string' ? bytes(c) : c));
    return [...events, ...parser.end()];
}

describe('sse parser', () => {
    it('parses one event per blank line', () => {
        expect(parseAll(['data: {"type":"chunk","content":"a"}\n\ndata: {"type":"done"}\n\n'])).toEqual([
            { type: 'chunk', content: 'a' },
            { type: 'done' },
        ]);
    });

    it('joins a line split across chunks', () => {
        expect(parseAll(['data: {"type":"chu', 'nk","content":"a"}\n', '\n'])).toEqual([
            { type: 'chunk', content: 'a' },
        ]);
    });

    it('keeps a multi-byte character split across chunks', () => {
        const all = bytes('data: {"type":"chunk","content":"مرحبا بك 😀"}\n\n');
        for (let cut = 1; cut < all.length; cut++) {
            const events = parseAll([all.slice(0, cut), all.slice(cut)]);
            expect(events).toEqual([{ type: 'chunk', content: 'مرحبا بك 😀' }]);
        }
    });

    it('handles CRLF, including a CRLF split between chunks', () => {
        expect(parseAll(['data: {"type":"done"}\r\n\r\n'])).toEqual([{ type: 'done' }]);
        expect(parseAll(['data: {"type":"done"}\r', '\n\r', '\n'])).toEqual([{ type: 'done' }]);
    });

    it('ignores comment and heartbeat lines', () => {
        expect(parseAll([': keep-alive\n\n: ping\ndata: {"type":"done"}\n\n'])).toEqual([{ type: 'done' }]);
    });

    it('joins several data lines of one event with a newline', () => {
        expect(parseAll(['data: {"type":"chunk",\ndata: "content":"a"}\n\n'])).toEqual([
            { type: 'chunk', content: 'a' },
        ]);
    });

    it('delivers a final event the server never closed with a blank line', () => {
        expect(parseAll(['data: {"type":"chunk","content":"a"}\n\ndata: {"type":"done"}\n'])).toEqual([
            { type: 'chunk', content: 'a' },
            { type: 'done' },
        ]);
        expect(parseAll(['data: {"type":"done"}'])).toEqual([{ type: 'done' }]);
    });

    it('delivers event types it does not know, untouched', () => {
        expect(parseAll(['data: {"type":"brand_new","x":1}\n\n'])).toEqual([{ type: 'brand_new', x: 1 }]);
    });

    it('raises on an event that is not JSON', () => {
        expect(() => parseAll(['data: {not json}\n\n'])).toThrow(GaiaStreamError);
    });

    it('raises on JSON with no string type', () => {
        expect(() => parseAll(['data: {"content":"a"}\n\n'])).toThrow(GaiaStreamError);
        expect(() => parseAll(['data: [1]\n\n'])).toThrow(GaiaStreamError);
    });

    it('raises on a final event cut off mid-JSON', () => {
        expect(() => parseAll(['data: {"type":"chunk","con'])).toThrow(GaiaStreamError);
    });
});

describe('parseSseStream', () => {
    it('streams events from a response body', async () => {
        const res = streamResponse(['data: {"type":"chunk","content":"a"}\n\n', 'data: {"type":"done"}\n\n']);
        const events = [];
        for await (const e of parseSseStream(res.body!)) events.push(e);
        expect(events.map((e) => e.type)).toEqual(['chunk', 'done']);
    });
});
