// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * The backend answers 403 to any mutating /api request without `x-gaia-ui: 1`
 * (`UIRequestGuardMiddleware` in src/gaia/ui/security.py). A request that drops
 * it breaks its button at runtime while every other test stays green, so each
 * way the UI sends one is checked here, and a new raw request has to be added.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, sendMessageStream, uploadDocumentBlob, uploadFile, uploadMedia } from '../api';
import { createKnowledge, deleteKnowledge, editKnowledge } from '../memoryApi';
import { pollPhoneStatus } from '../phoneApi';
import { speak } from '../speechApi';

const jsonResponse = (body: unknown) =>
    new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });

/** The `x-gaia-ui` value the nth fetch sent, matched the way HTTP does: ignoring case. */
function uiHeaderOf(fetchMock: ReturnType<typeof vi.fn>, call = 0): string | null {
    const init = fetchMock.mock.calls[call][1] as RequestInit | undefined;
    return new Headers(init?.headers).get('x-gaia-ui');
}

describe('every mutating request carries x-gaia-ui', () => {
    let fetchMock: ReturnType<typeof vi.fn>;

    beforeEach(() => {
        fetchMock = vi.fn(async () => jsonResponse({}));
        vi.stubGlobal('fetch', fetchMock);
    });

    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    it.each(['POST', 'PUT', 'PATCH', 'DELETE'])('apiFetch %s', async (method) => {
        await apiFetch(method, '/anything', method === 'DELETE' ? undefined : {});
        expect(uiHeaderOf(fetchMock)).toBe('1');
    });

    it('apiFetch keeps it when the caller adds its own headers', async () => {
        await apiFetch('POST', '/anything', {}, { 'x-request-id': 'abc' });
        expect(uiHeaderOf(fetchMock)).toBe('1');
    });

    it('sending a chat message', () => {
        fetchMock.mockImplementation(() => new Promise(() => {}));
        const controller = sendMessageStream('s1', 'hello', {
            onChunk: () => {},
            onAgentEvent: () => {},
            onDone: () => {},
            onError: () => {},
        });
        expect(fetchMock.mock.calls[0][0]).toMatch(/\/chat\/send$/);
        expect(uiHeaderOf(fetchMock)).toBe('1');
        controller.abort();
    });

    it('uploading a document', async () => {
        fetchMock.mockResolvedValue(jsonResponse({ id: 'd1' }));
        await uploadDocumentBlob(new File(['x'], 'notes.txt'));
        expect(uiHeaderOf(fetchMock)).toBe('1');
    });

    it('uploading a chat attachment', async () => {
        fetchMock.mockResolvedValue(jsonResponse({ filename: 'notes.txt' }));
        await uploadFile(new File(['x'], 'notes.txt'));
        expect(uiHeaderOf(fetchMock)).toBe('1');
    });

    it('reading text aloud', async () => {
        fetchMock.mockResolvedValue(new Response('RIFF', { status: 200 }));
        await speak('hello');
        expect(uiHeaderOf(fetchMock)).toBe('1');
    });

    it('polling for open phone alerts', async () => {
        await pollPhoneStatus();
        expect(uiHeaderOf(fetchMock)).toBe('1');
    });

    it.each([
        ['adding a memory', () => createKnowledge({ content: 'likes tea' } as Parameters<typeof createKnowledge>[0])],
        ['editing a memory', () => editKnowledge('k1', { content: 'likes coffee' } as Parameters<typeof editKnowledge>[1])],
        ['deleting a memory', () => deleteKnowledge('k1')],
    ])('%s', async (_name, call) => {
        await call();
        expect(uiHeaderOf(fetchMock)).toBe('1');
    });

    it('uploading a recording, which uses XMLHttpRequest for its progress', async () => {
        const sent: Record<string, string> = {};
        class FakeRequest {
            status = 200;
            responseText = '{}';
            upload = { onprogress: null };
            onload: (() => void) | null = null;
            onerror: (() => void) | null = null;
            onabort: (() => void) | null = null;
            open = vi.fn();
            setRequestHeader(name: string, value: string) {
                sent[name.toLowerCase()] = value;
            }
            send() {
                this.onload?.();
            }
            abort() {}
        }
        vi.stubGlobal('XMLHttpRequest', FakeRequest);
        await uploadMedia(new File(['x'], 'talk.wav'), () => {}).catch(() => {});
        expect(sent['x-gaia-ui']).toBe('1');
    });
});

const SOURCES = import.meta.glob(['/src/**/*.{ts,tsx}', '!/src/**/__tests__/**', '!/src/**/*.test.{ts,tsx}'], {
    query: '?raw',
    import: 'default',
    eager: true,
}) as Record<string, string>;

/** Every file that sends a request itself instead of through apiFetch, and how often. */
const RAW_REQUESTS: Record<string, { fetch: number; xhr: number }> = {
    '/src/services/api.ts': { fetch: 5, xhr: 1 },
    '/src/services/memoryApi.ts': { fetch: 1, xhr: 0 },
    '/src/services/phoneApi.ts': { fetch: 1, xhr: 0 },
    '/src/services/setupApi.ts': { fetch: 1, xhr: 0 },
    '/src/services/speechApi.ts': { fetch: 1, xhr: 0 },
};

function withoutComments(source: string): string {
    return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
}

describe('raw requests', () => {
    it('are only the ones listed above, each covered by a test in this file', () => {
        const found: Record<string, { fetch: number; xhr: number }> = {};
        for (const [path, source] of Object.entries(SOURCES)) {
            const code = withoutComments(source);
            const fetches = code.match(/(^|[^\w$])fetch\(/gm)?.length ?? 0;
            const xhrs = code.match(/new XMLHttpRequest\b/g)?.length ?? 0;
            if (fetches || xhrs) found[path] = { fetch: fetches, xhr: xhrs };
        }
        expect(
            found,
            'A request sent with fetch or XMLHttpRequest skips apiFetch, which adds x-gaia-ui. ' +
                'Use apiFetch, or send UI_HEADER, add a case above, and update RAW_REQUESTS.',
        ).toEqual(RAW_REQUESTS);
    });
});
