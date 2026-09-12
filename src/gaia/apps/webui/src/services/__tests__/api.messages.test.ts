// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { afterEach, describe, expect, it, vi } from 'vitest';
import { getMessageCount, getMessages } from '../api';

function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
        status, headers: { 'content-type': 'application/json' },
    });
}

function transcript(count: number) {
    return Array.from({ length: count }, (_, index) => ({
        id: index + 1, session_id: 'long-chat', role: index % 2 ? 'assistant' : 'user',
        content: `Message ${index + 1}`, created_at: '2026-09-11T00:00:00Z',
        agent_steps: [{ type: 'tool_call', tool: 'read_file', args: { path: 'README.md' } }],
    }));
}

function serveTranscript(messages: ReturnType<typeof transcript>) {
    const requests: URL[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: string) => {
        const url = new URL(input, 'http://localhost');
        requests.push(url);
        const offset = Number(url.searchParams.get('offset') || 0);
        const limit = Number(url.searchParams.get('limit') || 100);
        return jsonResponse({ messages: messages.slice(offset, offset + limit), total: messages.length });
    }));
    return requests;
}

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe('complete transcript retrieval', () => {
    it('probes the count with one minimal page request', async () => {
        const requests = serveTranscript(transcript(205));
        expect(await getMessageCount('long-chat')).toBe(205);
        expect(requests).toHaveLength(1);
        expect(requests[0].searchParams.get('limit')).toBe('1');
        expect(requests[0].searchParams.get('offset')).toBe('0');
    });

    it.each([0, 1, 100, 101, 205])('loads all %i messages in order with metadata intact', async (count) => {
        const messages = transcript(count);
        const requests = serveTranscript(messages);
        expect(await getMessages('long-chat')).toEqual({ messages, total: count });
        expect(requests).toHaveLength(Math.max(1, Math.ceil(count / 100)));
        expect(requests.map((url) => Number(url.searchParams.get('offset') || 0)))
            .toEqual(Array.from({ length: requests.length }, (_, index) => index * 100));
    });

    it('bounds a retrieval to the initial count when new messages arrive during paging', async () => {
        const messages = transcript(250);
        const requests: URL[] = [];
        vi.stubGlobal('fetch', vi.fn(async (input: string) => {
            const url = new URL(input, 'http://localhost');
            requests.push(url);
            const offset = Number(url.searchParams.get('offset') || 0);
            const limit = Number(url.searchParams.get('limit') || 100);
            return jsonResponse({
                messages: messages.slice(offset, offset + limit),
                total: requests.length === 1 ? 150 : 250,
            });
        }));
        expect(await getMessages('long-chat')).toEqual({ messages: messages.slice(0, 150), total: 150 });
        expect(requests).toHaveLength(2);
        expect(requests[1].searchParams.get('limit')).toBe('50');
    });

    it('surfaces a later page failure instead of returning a partial transcript', async () => {
        vi.stubGlobal('fetch', vi.fn()
            .mockResolvedValueOnce(jsonResponse({ messages: transcript(100), total: 101 }))
            .mockResolvedValueOnce(jsonResponse({ detail: 'unavailable' }, 503)));
        await expect(getMessages('long-chat')).rejects.toThrow('unavailable');
    });

    it('fails on an empty page before the reported end instead of looping forever', async () => {
        const request = vi.fn()
            .mockResolvedValueOnce(jsonResponse({ messages: transcript(100), total: 101 }))
            .mockResolvedValueOnce(jsonResponse({ messages: [], total: 101 }));
        vi.stubGlobal('fetch', request);
        await expect(getMessages('long-chat')).rejects.toThrow(/incomplete transcript/i);
        expect(request).toHaveBeenCalledTimes(2);
    });
});
