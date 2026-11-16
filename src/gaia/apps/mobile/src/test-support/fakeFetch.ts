// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { ReadableStream as NodeReadableStream } from 'node:stream/web';
import { TextEncoder } from 'node:util';

import type { FetchLike, FetchRequestInit, FetchResponseLike } from '@/platform/fetch.types';

export interface RecordedCall {
    url: string;
    method: string | undefined;
    headers: Record<string, string>;
    body: string | undefined;
    /** A file or form body, which is not JSON text. */
    rawBody: Blob | FormData | undefined;
    signal: AbortSignal | undefined;
}

export type Responder = (call: RecordedCall) => FetchResponseLike | Promise<FetchResponseLike>;

export function createRecordingFetch(responder: Responder): { fetch: FetchLike; calls: RecordedCall[] } {
    const calls: RecordedCall[] = [];
    const fetch: FetchLike = async (url: string, init?: FetchRequestInit) => {
        const call: RecordedCall = {
            url,
            method: init?.method,
            headers: init?.headers ?? {},
            body: typeof init?.body === 'string' ? init.body : undefined,
            rawBody: typeof init?.body === 'string' ? undefined : init?.body,
            signal: init?.signal,
        };
        calls.push(call);
        return responder(call);
    };
    return { fetch, calls };
}

export function textResponse(text: string, status = 200): FetchResponseLike {
    return {
        ok: status >= 200 && status < 300,
        status,
        headers: { get: () => null },
        body: null,
        text: async () => text,
    };
}

export function jsonResponse(body: unknown, status = 200): FetchResponseLike {
    return textResponse(JSON.stringify(body), status);
}

export function streamResponse(chunks: (string | Uint8Array)[], status = 200): FetchResponseLike {
    const encoder = new TextEncoder();
    const body = new NodeReadableStream<Uint8Array>({
        start(controller) {
            for (const chunk of chunks) controller.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk);
            controller.close();
        },
    });
    return {
        ok: status >= 200 && status < 300,
        status,
        headers: { get: () => 'text/event-stream' },
        body: body as unknown as ReadableStream<Uint8Array>,
        text: async () => '',
    };
}

export function bytes(text: string): Uint8Array {
    return new TextEncoder().encode(text);
}
