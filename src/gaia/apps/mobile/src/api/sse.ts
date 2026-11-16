// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import type { StreamEvent } from '@webui/types';

import { GaiaStreamError, GaiaStreamStalledError } from './errors';
import { createUtf8Decoder } from './utf8';

export interface SseParser {
    push(chunk: Uint8Array): StreamEvent[];
    /** Call once when the body ends: delivers an event the server never closed with a blank line. */
    end(): StreamEvent[];
}

const LINE_END = /\r\n|\n|\r/;

function toEvent(data: string): StreamEvent {
    const preview = data.slice(0, 100);
    let parsed: unknown;
    try {
        parsed = JSON.parse(data);
    } catch (cause) {
        throw new GaiaStreamError('mobileApp.error.badEvent', { preview }, { cause });
    }
    if (parsed === null || typeof parsed !== 'object' || typeof (parsed as { type?: unknown }).type !== 'string') {
        throw new GaiaStreamError('mobileApp.error.badEvent', { preview });
    }
    // Types this app doesn't know yet are delivered as they came.
    return parsed as StreamEvent;
}

export function createSseParser(): SseParser {
    const decoder = createUtf8Decoder();
    let buffer = '';
    let dataLines: string[] = [];
    let sawData = false;

    function dispatch(out: StreamEvent[]): void {
        if (!sawData) return;
        const data = dataLines.join('\n');
        dataLines = [];
        sawData = false;
        if (data.trim() === '') return;
        out.push(toEvent(data));
    }

    function handleLine(line: string, out: StreamEvent[]): void {
        if (line === '') return dispatch(out);
        if (line.startsWith(':')) return;
        const colon = line.indexOf(':');
        const field = colon === -1 ? line : line.slice(0, colon);
        if (field !== 'data') return;
        let value = colon === -1 ? '' : line.slice(colon + 1);
        if (value.startsWith(' ')) value = value.slice(1);
        dataLines.push(value);
        sawData = true;
    }

    function drain(out: StreamEvent[], final: boolean): void {
        for (;;) {
            const match = LINE_END.exec(buffer);
            if (!match) return;
            // A trailing CR may be the first half of a CRLF split across chunks.
            if (!final && match[0] === '\r' && match.index + 1 === buffer.length) return;
            const line = buffer.slice(0, match.index);
            buffer = buffer.slice(match.index + match[0].length);
            handleLine(line, out);
        }
    }

    return {
        push(chunk) {
            const out: StreamEvent[] = [];
            buffer += decoder.push(chunk);
            drain(out, false);
            return out;
        },
        end() {
            const out: StreamEvent[] = [];
            buffer += decoder.end();
            drain(out, true);
            if (buffer !== '') {
                handleLine(buffer, out);
                buffer = '';
            }
            dispatch(out);
            return out;
        },
    };
}

/** Milliseconds of silence allowed before the next chunk, chosen from the last event read. */
export type IdleLimit = (last: StreamEvent | undefined) => number;

async function readWithin(
    reader: ReadableStreamDefaultReader<Uint8Array>,
    idleMs: number | undefined,
): Promise<ReadableStreamReadResult<Uint8Array>> {
    if (idleMs === undefined) return reader.read();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const stalled = new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new GaiaStreamStalledError(idleMs)), idleMs);
    });
    try {
        return await Promise.race([reader.read(), stalled]);
    } finally {
        clearTimeout(timer);
    }
}

/**
 * The body's events as they arrive. With `idleLimit`, silence past it raises
 * GaiaStreamStalledError; the caller then drops the connection.
 */
export async function* parseSseStream(
    body: ReadableStream<Uint8Array>,
    idleLimit?: IdleLimit,
): AsyncGenerator<StreamEvent> {
    const parser = createSseParser();
    const reader = body.getReader();
    let last: StreamEvent | undefined;
    for (;;) {
        const { done, value } = await readWithin(reader, idleLimit?.(last));
        if (done) break;
        for (const event of parser.push(value)) {
            last = event;
            yield event;
        }
    }
    yield* parser.end();
}
